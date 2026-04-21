import abc
from typing import List, Dict, Any, Generator, Optional, Callable


class BaseLLMHandler(abc.ABC):
    """
    Базовый интерфейс для всех обработчиков ИИ (Cloud API, Local Models).
    Все новые модели (Ollama, vLLM, OpenRouter) должны реализовывать эти методы.
    """

    @abc.abstractmethod
    def send_message_stream(
        self,
        messages: List[Dict[str, Any]],
        tools_definitions: Optional[List[Dict[str, Any]]] = None,
        cancellation_token: Optional[Callable[[], bool]] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Отправляет сообщения и возвращает генератор чанков (стриминг).
        
        Args:
            messages: Контекст сообщений (роли: system, user, assistant, tool).
            tools_definitions: Описание доступных инструментов (JSON Schema).
            cancellation_token: Функция, возвращающая True, если генерацию нужно прервать (Soft Interrupt).
            
        Yields:
            Словарь с чанками ответа:
            {
                "content": str,         # Текст
                "tool_calls": dict,     # Если модель вызывает инструмент
                "finish_reason": str    # 'stop', 'tool_calls' или 'cancelled'
            }
        """
        pass

    @abc.abstractmethod
    def ping_fast_interrupt(self, context_text: str) -> bool:
        """
        Мгновенный запрос к LLM (без истории) для определения, перебил ли пользователь речь.
        
        Args:
            context_text: Распознанный текст пользователя во время чужой речи.
            
        Returns:
            True, если это перебивание, False, если шум/"угу".
        """
        pass
    
    @abc.abstractmethod
    def get_token_stats(self) -> Dict[str, float]:
        """
        Возвращает статистику токенов и скорость генерации (t/s).
        """
        pass
