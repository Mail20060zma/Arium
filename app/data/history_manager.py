import json
import logging
import threading
import queue
import time
import base64
import mimetypes
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
                # Удаляем ненужные ключи
                for key in ["spoken_text", "model", "tool_results"]:
                    msg.pop(key, None)
                if "id" not in msg:
                    msg["id"] = f"msg_{uuid4().hex}"
                self._history.append(msg)
                
            elif action == "truncate":
                msg_id = event["msg_id"]
                new_text = event.get("text", "")
                for m in reversed(self._history):
                    if m.get("id") == msg_id:
                        m["content"] = new_text
                        m.pop("spoken_text", None)
                        m.pop("model", None)
                        m.pop("tool_results", None)
                        
                        # Также урезаем текст в аргументах tool_calls (text_to_audio)
                        tool_calls = m.get("tool_calls")
                        if isinstance(tool_calls, list):
                            for tc in tool_calls:
                                if not isinstance(tc, dict):
                                    continue
                                fn = tc.get("function")
                                if isinstance(fn, dict) and fn.get("name") == "text_to_audio":
                                    args_str = fn.get("arguments", "{}")
                                    try:
                                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                                        if isinstance(args, dict):
                                            args["text"] = new_text
                                            fn["arguments"] = json.dumps(args, ensure_ascii=False)
                                    except Exception as e:
                                        logger.warning(f"Failed to update tool_call text on truncate: {e}")
                                    
                                    # Находим соответствующий tool-ответ в истории и урезаем его
                                    tc_id = tc.get("id")
                                    if tc_id:
                                        for tool_msg in reversed(self._history):
                                            if tool_msg.get("role") == "tool" and tool_msg.get("tool_call_id") == tc_id:
                                                try:
                                                    tool_content = json.loads(tool_msg.get("content", "{}"))
                                                    if isinstance(tool_content, dict) and "status" in tool_content:
                                                        tool_content["status"] = "interrupted"
                                                        tool_content["text_spoken"] = new_text
                                                        tool_content["interrupted"] = True
                                                        tool_msg["content"] = json.dumps(tool_content, ensure_ascii=False)
                                                    else:
                                                        tool_msg["content"] = new_text
                                                except ValueError:
                                                    # Это не JSON, просто строка
                                                    tool_msg["content"] = new_text
                                                except Exception as e:
                                                    logger.warning(f"Failed to update tool response on truncate: {e}")
                                        
                        logger.debug(f"Truncated message {msg_id} to {len(new_text)} chars.")
                        break

            elif action == "update":
                msg_id = event["msg_id"]
                updates = event.get("updates", {})
                for key in ["spoken_text", "model", "tool_results"]:
                    updates.pop(key, None)
                for m in reversed(self._history):
                    if m.get("id") == msg_id:
                        m.update(updates)
                        for key in ["spoken_text", "model", "tool_results"]:
                            m.pop(key, None)
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
        Возвращает чистые сообщения (без внутренних метаданных), совместимые с OpenAI API.
        """
        if limit is None:
            limit = self.max_context_window
            
        with self._history_lock:
            # Берем последние limit сообщений
            recent = self._history[-limit:] if limit > 0 else self._history
            
            api_messages = []
            skipped_tool_call_ids = set()
            
            for m in recent:
                role = str(m.get("role", "user"))
                
                if role in {"user", "system", "developer"}:
                    api_messages.append({
                        "role": role,
                        "content": m.get("content") or ""
                    })
                    continue
                    
                if role == "assistant":
                    msg = {"role": "assistant"}
                    
                    # 1. Обработка reasoning_content
                    if include_reasoning and m.get("reasoning_content"):
                        reasoning = str(m.get("reasoning_content"))
                        if len(reasoning) > reasoning_max_chars:
                            reasoning = reasoning[:reasoning_max_chars] + "..."
                        msg["reasoning_content"] = reasoning
                        
                    # 2. Обработка tool_calls
                    tool_calls = m.get("tool_calls")
                    filtered_tool_calls = []
                    if isinstance(tool_calls, list):
                        for tc in tool_calls:
                            if not isinstance(tc, dict):
                                continue
                            
                            # Проверяем имя функции
                            func_name = ""
                            fn = tc.get("function")
                            if isinstance(fn, dict):
                                func_name = str(fn.get("name", ""))
                            
                            filtered_tool_calls.append(tc)
                            
                    if filtered_tool_calls:
                        msg["tool_calls"] = filtered_tool_calls
                        # OpenAI разрешает content быть null при наличии tool_calls
                        msg["content"] = m.get("content")
                    else:
                        # Если tool_calls отсутствуют или отфильтрованы, content должен быть строкой
                        content_val = m.get("content")
                        msg["content"] = str(content_val) if content_val is not None else ""
                        
                    api_messages.append(msg)
                    continue
                    
                if role == "tool":
                    tool_call_id = m.get("tool_call_id")
                    tool_name = m.get("name")
                    
                    # Проверяем, нужно ли пропустить этот ответ инструмента
                    if tool_call_id and str(tool_call_id) in skipped_tool_call_ids:
                        skipped_tool_call_ids.discard(str(tool_call_id))
                        continue
                        
                    content_val = m.get("content") or ""
                    
                    # Парсинг JSON-контента инструмента в поисках image_path(s)
                    parsed_content = None
                    try:
                        if isinstance(content_val, str) and content_val.strip().startswith("{"):
                            parsed_content = json.loads(content_val)
                    except:
                        pass
                        
                    if isinstance(parsed_content, dict) and ("image_path" in parsed_content or "image_paths" in parsed_content):
                        new_content = []
                        text_repr = parsed_content.copy()
                        image_paths = []
                        
                        if "image_path" in text_repr:
                            image_paths.append(text_repr.pop("image_path"))
                        if "image_paths" in text_repr:
                            image_paths.extend(text_repr.pop("image_paths"))
                            
                        new_content.append({
                            "type": "text",
                            "text": json.dumps(text_repr, ensure_ascii=False)
                        })
                        
                        for ipath in image_paths:
                            p = Path(ipath)
                            if p.exists() and p.is_file():
                                try:
                                    mime_type, _ = mimetypes.guess_type(p.name)
                                    if not mime_type:
                                        mime_type = "image/jpeg"
                                    with open(p, "rb") as f:
                                        b64_encoded = base64.b64encode(f.read()).decode("utf-8")
                                    new_content.append({
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:{mime_type};base64,{b64_encoded}"
                                        }
                                    })
                                except Exception as e:
                                    logger.warning(f"Failed to load image {ipath}: {e}")
                                    
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": str(tool_call_id) if tool_call_id else "",
                            "content": new_content if len(new_content) > 1 else content_val
                        }
                    else:
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": str(tool_call_id) if tool_call_id else "",
                            "content": content_val
                        }
                    if tool_name:
                        tool_msg["name"] = str(tool_name)
                        
                    api_messages.append(tool_msg)
                    continue
                    
            return api_messages

    def get_last_message_id(self, role: str = "assistant") -> Optional[str]:
        """Получить ID последнего сообщения указанной роли."""
        with self._history_lock:
            for m in reversed(self._history):
                if m.get("role") == role:
                    return m.get("id")
        return None
