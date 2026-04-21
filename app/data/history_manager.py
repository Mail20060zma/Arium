import json
import logging
import threading
import queue
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

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
        self._is_running = False
        if self._worker_thread:
            # Send a poison pill
            self._event_queue.put({"action": "stop"})
            self._worker_thread.join(timeout=2.0)
            self._flush_to_disk()

    def _worker_loop(self) -> None:
        """Основной цикл (Event Loop) фонового потока."""
        needs_flush = False
        while self._is_running:
            try:
                # Get events with a small timeout to allow batching flushes
                event = self._event_queue.get(timeout=1.0)
                
                if event.get("action") == "stop":
                    self._flush_to_disk() # Явный сброс перед полной остановкой
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
                    msg["id"] = f"msg_{int(time.time() * 1000)}"
                self._history.append(msg)
                
            elif action == "truncate" or action == "update":
                msg_id = event["msg_id"]
                new_text = event["text"]
                # Найти сообщение с конца и обновить
                for m in reversed(self._history):
                    if m.get("id") == msg_id or m.get("role") == "assistant":
                        m["content"] = new_text
                        logger.debug(f"Updated message {m.get('id')} to {len(new_text)} chars.")
                        break
                        
            elif action == "clear":
                self._history.clear()

    # --- Публичные неблокирующие методы (Отправка событий) ---

    def append_message(self, role: str, content: str, msg_id: Optional[str] = None, **kwargs) -> str:
        """Добавить сообщение (не блокирует вызов)."""
        mid = msg_id or f"msg_{int(time.time() * 1000)}"
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

    def update_message(self, msg_id: str, text: str) -> None:
        """Обновить сообщение в реальном времени."""
        self._event_queue.put({
            "action": "update", 
            "msg_id": msg_id, 
            "text": text
        })

    def clear_history(self) -> None:
        """Очистить историю."""
        self._event_queue.put({"action": "clear"})

    # --- Публичные методы чтения (Мгновенный доступ из ОЗУ) ---

    def get_context_window(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Получить окно контекста для отправки в LLM.
        Возвращает чистые сообщения (без внутренних метаданных).
        """
        if limit is None:
            limit = self.max_context_window
            
        with self._history_lock:
            # Берем последние limit сообщений
            recent = self._history[-limit:] if limit > 0 else self._history
            
            # Формируем формат OpenAI
            api_messages = []
            for m in recent:
                msg = {"role": m["role"], "content": m["content"]}
                if "tool_calls" in m:
                    msg["tool_calls"] = m["tool_calls"]
                if "tool_call_id" in m:
                    msg["tool_call_id"] = m["tool_call_id"]
                    msg["name"] = m.get("name", "")
                
                api_messages.append(msg)
                
            return api_messages

    def get_last_message_id(self, role: str = "assistant") -> Optional[str]:
        """Получить ID последнего сообщения указанной роли."""
        with self._history_lock:
            for m in reversed(self._history):
                if m.get("role") == role:
                    return m.get("id")
        return None
