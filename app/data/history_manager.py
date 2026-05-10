import json
import logging
import threading
import queue
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

class HistoryManager:
    """
    Асинхронный менеджер истории сообщений (Actor Pattern).
    Обеспечивает потокобезопасную работу с историей и файлом `chat_history.json`.
    Поддерживает урезание сообщений (truncate) и сдвиг окна контекста.
    """
    def __init__(self, filepath: str = "app/data/chat_history.json", max_context_window: int = 50):
        self._filepath = Path(filepath)
        self._filepath.parent.mkdir(parents=True, exist_ok=True)
        
        self.max_context_window = max_context_window
        
        # In-Memory Cache 
        self._history: List[Dict[str, Any]] = []
        self._load_from_disk()
        
        # Worker Queue
        self._event_queue = queue.Queue()
        self._is_running = False
        self._worker_thread = None
        
        # Synchronization for reads
        self._history_lock = threading.RLock()
        
    def _load_from_disk(self) -> None:
        """Загрузка истории из файла при старте."""
        if self._filepath.exists():
            try:
                with open(self._filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._history = data.get("messages", [])
                    logger.info(f"Loaded {len(self._history)} messages from history.")
            except Exception as e:
                logger.error(f"Error loading history: {e}")
                self._history = []

    def _flush_to_disk(self) -> None:
        """Асинхронная запись кэша на диск."""
        with self._history_lock:
            data_to_save = {
                "last_updated": time.time(),
                "messages": self._history
            }
        
        try:
            # Atomic save: write to temp file then rename
            temp_file = self._filepath.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            temp_file.replace(self._filepath)
        except Exception as e:
            logger.error(f"Error saving history to disk: {e}")

    def start_worker(self) -> None:
        """Запуск фонового воркера для обработки событий истории."""
        if self._is_running:
            return
        
        self._is_running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, name="HistoryManagerWorker", daemon=True)
        self._worker_thread.start()
        logger.info("History Manager worker started.")

    def stop_worker(self) -> None:
        """Остановка воркера."""
        if self._worker_thread:
            # Send a poison pill
            self._event_queue.put({"action": "stop"})
            self._worker_thread.join(timeout=2.0)
            self._is_running = False
            self._flush_to_disk()

    def _worker_loop(self) -> None:
        """Основной цикл (Event Loop) фонового потока."""
        needs_flush = False
        while True:
            try:
                # Get events with a small timeout to allow batching flushes
                event = self._event_queue.get(timeout=1.0)
                
                if event.get("action") == "stop":
                    self._flush_to_disk() # Явный сброс перед полной остановкой
                    self._event_queue.task_done()
                    break
                
                self._process_event(event)
                needs_flush = True
                self._event_queue.task_done()
                
            except queue.Empty:
                if needs_flush:
                    self._flush_to_disk()
                    needs_flush = False
            except Exception as e:
                logger.error(f"History worker error: {e}")

    def _process_event(self, event: Dict[str, Any]) -> None:
        """Обработка одного события записи."""
        action = event.get("action")
        
        with self._history_lock:
            if action == "add":
                msg = event["message"]
                if "id" not in msg:
                    msg["id"] = f"msg_{uuid4().hex}"
                self._history.append(msg)
                
            elif action == "truncate":
                msg_id = event["msg_id"]
                new_text = event.get("text", "")
                for m in reversed(self._history):
                    if m.get("id") == msg_id:
                        m["content"] = new_text
                        m["spoken_text"] = new_text
                        logger.debug(f"Truncated message {msg_id} to {len(new_text)} chars.")
                        break

            elif action == "update":
                msg_id = event["msg_id"]
                updates = event.get("updates", {})
                for m in reversed(self._history):
                    if m.get("id") == msg_id:
                        m.update(updates)
                        logger.debug(f"Updated message {msg_id} fields: {list(updates.keys())}")
                        break
                        
            elif action == "clear":
                self._history.clear()

    # --- Публичные неблокирующие методы (Отправка событий) ---

    def append_message(self, role: str, content: Optional[str], msg_id: Optional[str] = None, **kwargs) -> str:
        """Добавить сообщение (не блокирует вызов)."""
        mid = msg_id or f"msg_{uuid4().hex}"
        msg = {
            "id": mid,
            "role": role,
            "content": content,
            "timestamp": time.time()
        }
        msg.update(kwargs)
        self._event_queue.put({"action": "add", "message": msg})
        return mid

    def truncate_message(self, msg_id: str, actually_spoken_text: str) -> None:
        """Обрезать концовку сообщения ИИ, если его перебили."""
        self._event_queue.put({
            "action": "truncate", 
            "msg_id": msg_id, 
            "text": actually_spoken_text
        })

    def update_message(self, msg_id: str, text: Optional[str] = None, **kwargs) -> None:
        """Обновить сообщение в реальном времени.

        Args:
            msg_id: ID сообщения.
            text: Новое значение content (опционально).
            **kwargs: Дополнительные поля сообщения (spoken_text, reasoning_content, finish_reason и т.д.).
        """
        updates: Dict[str, Any] = {}
        if text is not None:
            updates["content"] = text
        updates.update(kwargs)
        if not updates:
            return

        self._event_queue.put({
            "action": "update",
            "msg_id": msg_id,
            "updates": updates,
        })

    def clear_history(self) -> None:
        """Очистить историю."""
        self._event_queue.put({"action": "clear"})

    def wait_until_idle(self, timeout: float = 2.0) -> bool:
        """Дождаться обработки всех queued-событий истории."""
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if self._event_queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._event_queue.unfinished_tasks == 0

    # --- Публичные методы чтения (Мгновенный доступ из ОЗУ) ---

    def get_context_window(
        self,
        limit: Optional[int] = None,
        include_reasoning: bool = False,
        reasoning_max_chars: int = 4000,
    ) -> List[Dict[str, Any]]:
        """
        Получить окно контекста для отправки в LLM.
        Возвращает чистые сообщения (без внутренних метаданных).
        """
        if limit is None:
            limit = self.max_context_window
            
        with self._history_lock:
            # Берем последние limit сообщений
            recent = self._history[-limit:] if limit > 0 else self._history
            
            # Формируем каноничный формат Chat Completions.
            # В контекст отправляем только официальные поля role/content/tool_calls/tool_call_id/name.
            api_messages = []
            tool_messages_in_turn = 0
            max_tool_messages_per_turn = 4
            for m in recent:
                role = str(m.get("role", "user"))
                content = m.get("content", "")

                if role in {"user", "system", "developer"}:
                    tool_messages_in_turn = 0

                if role == "assistant":
                    if isinstance(m.get("tool_calls"), list) and m.get("tool_calls"):
                        if tool_messages_in_turn >= max_tool_messages_per_turn:
                            continue
                        tool_messages_in_turn += 1

                    msg = {"role": "assistant"}
                    tool_calls = m.get("tool_calls")
                    if isinstance(tool_calls, list) and tool_calls:
                        # Tool-calls озвучки не нужны в будущем контексте и могут вызывать шум/циклы.
                        names = []
                        for tc in tool_calls:
                            if not isinstance(tc, dict):
                                continue
                            fn = tc.get("function")
                            if isinstance(fn, dict):
                                names.append(str(fn.get("name", "")))
                        if names and all(name == "text_to_audio" for name in names):
                            continue

                        msg["tool_calls"] = tool_calls
                        msg["content"] = content if content is not None else None
                    else:
                        msg["content"] = "" if content is None else str(content)
                    api_messages.append(msg)
                    continue

                if role == "tool":
                    if tool_messages_in_turn >= max_tool_messages_per_turn:
                        continue

                    tool_call_id = m.get("tool_call_id")
                    if not tool_call_id:
                        continue

                    if str(m.get("name", "")) == "text_to_audio":
                        continue

                    tool_messages_in_turn += 1

                    msg = {
                        "role": "tool",
                        "tool_call_id": str(tool_call_id),
                        "content": "" if content is None else str(content),
                    }
                    if "name" in m and m.get("name"):
                        msg["name"] = str(m.get("name"))
                    api_messages.append(msg)
                    continue

                if role in {"user", "system", "developer"}:
                    msg = {
                        "role": role,
                        "content": "" if content is None else str(content),
                    }
                    api_messages.append(msg)

            return api_messages

    def get_last_message_id(self, role: str = "assistant") -> Optional[str]:
        """Получить ID последнего сообщения указанной роли."""
        with self._history_lock:
            for m in reversed(self._history):
                if m.get("role") == role:
                    return m.get("id")
        return None
