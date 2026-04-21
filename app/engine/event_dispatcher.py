import time
import threading
import logging
from typing import Callable, Dict, Any, List

logger = logging.getLogger(__name__)

class EventDispatcher:
    """
    Диспетчер событий с поддержкой Debouncing.
    Собирает события (например, конец речи или результаты tool_calls) 
    и ждет небольшое окно (debounce_ms), чтобы сгруппировать их перед отправкой в LLM.
    Это предотвращает гонку данных и дублирующиеся запросы.
    """
    def __init__(self, debounce_ms: int = 200, on_trigger: Callable = None):
        self.debounce_seconds = debounce_ms / 1000.0
        self.on_trigger = on_trigger
        
        self._lock = threading.Lock()
        self._timer: threading.Timer = None
        self._event_buffer: List[Dict[str, Any]] = []
        
    def dispatch(self, event_type: str, data: Any = None):
        """
        Добавляет событие в буфер и перезапускает таймер.
        Если в течение debounce_ms новых событий не поступает, вызывается on_trigger.
        """
        with self._lock:
            self._event_buffer.append({"type": event_type, "data": data, "timestamp": time.time()})
            
            # Если таймер уже запущен - сбрасываем его (debounce)
            if self._timer is not None:
                self._timer.cancel()
                
            self._timer = threading.Timer(self.debounce_seconds, self._flush)
            self._timer.start()
            
    def _flush(self):
        """
        Сбрасывает буфер и вызывает коллбэк.
        Этот метод вызывается из отдельного потока Timer.
        """
        with self._lock:
            events_to_process = self._event_buffer.copy()
            self._event_buffer.clear()
            self._timer = None
            
        if self.on_trigger and events_to_process:
            logger.debug(f"[EventDispatcher] Отправка батча из {len(events_to_process)} событий")
            self.on_trigger(events_to_process)
            
    def cancel(self):
        """Отмена текущего таймера (например, если контекст сброшен)"""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._event_buffer.clear()
