"""
OpenRouter LLM Handler - использует OpenAI SDK с openrouter.ai как base_url
"""
import logging
import time
import json
import os
from typing import List, Dict, Any, Optional, Callable
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI, APIError, RateLimitError, APIConnectionError
import threading

from .llm_common import StreamChunkBuffer, ToolExecutor
from tools.instructions import TOOLS_USE


logger = logging.getLogger(__name__)


class OpenRouterHandler:
    """
    Обработчик для OpenRouter API используя OpenAI SDK.
    
    Поддерживает:
    - Streaming ответов
    - Tool calling с параллельной обработкой
    - Обработка ошибок с retry
    """
    
    ERROR_MESSAGES = {
        429: "Выбранная модель недоступна из-за высокой нагрузки. Попробуйте другую модель или позже.",
        502: "Серверы сейчас перегружены. Попробуйте позже.",
        404: "Выбранная модель больше не поддерживается. Выберите другую.",
        403: "Провайдер отклонил запрос. Возможно, не поддерживается в вашем регионе.",
    }
    
    def __init__(self, 
                 api_key: str,
                 model: str = "openai/gpt-4-turbo-preview",
                 base_url: str = "https://openrouter.ai/api/v1",
                 tool_handlers: Optional[Dict[str, callable]] = None):
        """
        Args:
            api_key: OpenRouter API ключ
            model: Название модели
            base_url: Base URL для OpenRouter
            tool_handlers: Словарь обработчиков инструментов {имя: функция}
        """
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key
        )
        self.model = model
        self.tool_executor = ToolExecutor(tool_handlers or {})
        self._lock = threading.Lock()
    
    def _build_request_payload(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Строит payload для API запроса с инструментами из instructions.py."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "stream": False,  # Ожидание полного ответа
        }
        
        # Добавляем tools из instructions.py
        if TOOLS_USE:
            payload["tools"] = TOOLS_USE
            payload["tool_choice"] = "auto"
            logger.debug(f"Добавлены инструменты: {[t['function']['name'] for t in TOOLS_USE]}")
        
        return payload
    
    def send_message(self, messages: List[Dict[str, Any]], 
                     save_callback: Optional[Callable] = None,
                     max_retries: int = 3) -> Dict[str, Any]:
        """
        Отправляет сообщение и получает полный ответ (без streaming).
        НЕ обрабатывает tool_calls - возвращает их в main.py.
        
        Args:
            messages: Список сообщений для API
            save_callback: Callback для прогресса сохранения
            max_retries: Максимум попыток при ошибке
            
        Returns:
            Словарь с ответом (содержит tool_calls как есть)
        """
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Отправка запроса в OpenRouter (попытка {attempt}/{max_retries}, без streaming)...")
                
                payload = self._build_request_payload(messages)
                
                # Очищаем сообщения от "error" роли для API
                clean_messages = [
                    msg for msg in payload["messages"] 
                    if msg.get("role") not in ["error"]
                ]
                payload["messages"] = clean_messages
                
                # Получаем полный ответ
                response = self.client.chat.completions.create(**payload)
                
                # Обработка полного ответа (без streaming)
                result = self._handle_full_response(response)
                
                # Возвращаем результат с tool_calls как есть - обработка в main.py
                if result.get("tool_calls"):
                    logger.info(f"🔧 Обнаружены tool calls: {[tc['function']['name'] for tc in result['tool_calls']]}")
                
                return result
                
            except (APIError, RateLimitError, APIConnectionError) as e:
                error_code = getattr(e, 'status_code', None)
                logger.error(f"❌ API error {error_code}: {str(e)}")
                
                if attempt >= max_retries:
                    error_msg = self.ERROR_MESSAGES.get(
                        error_code,
                        f"Ошибка API: {str(e)}"
                    )
                    logger.error(f"❌ Финальная ошибка: {error_msg}")
                    return {
                        "role": "error",
                        "content": error_msg,
                        "error": True
                    }
                
                # Экспоненциальный backoff
                wait_time = 2 ** (attempt - 1)
                logger.warning(f"⚠️ Ошибка (попытка {attempt}/{max_retries}): {str(e)}. Повторяем через {wait_time}с...")
                time.sleep(wait_time)
            
            except Exception as e:
                logger.error(f"❌ Неожиданная ошибка: {str(e)}")
                if attempt >= max_retries:
                    return {
                        "role": "error",
                        "content": f"Ошибка обработки: {str(e)}",
                        "error": True
                    }
                time.sleep(2 ** (attempt - 1))
        
        return {
            "role": "error",
            "content": "Не удалось получить ответ после всех попыток",
            "error": True
        }
    
    def _handle_full_response(self, response) -> Dict[str, Any]:
        """Обрабатывает полный ответ (без streaming)."""
        try:
            # Получаем первый choice
            if not response.choices:
                logger.error("❌ Пустой ответ от API")
                return {"role": "assistant", "content": ""}
            
            choice = response.choices[0]
            message = choice.message
            
            result = {
                "role": message.role,
                "content": message.content or ""
            }
            
            # Проверяем tool_calls
            if message.tool_calls:
                tool_calls = []
                for tc in message.tool_calls:
                    tool_calls.append({
                        "id": tc.id,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    })
                result["tool_calls"] = tool_calls
                logger.info(f"✅ Получен полный ответ с {len(tool_calls)} tool calls")
            else:
                logger.info(f"✅ Получен полный ответ ({len(result['content'])} символов)")
            
            return result
        
        except Exception as e:
            logger.error(f"❌ Ошибка обработки ответа: {str(e)}")
            raise
    
    def _process_tool_calls(self, messages: List[Dict[str, Any]], response_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Обрабатывает tool calls (кроме text_to_audio) и отправляет результаты обратно в API.
        text_to_audio должна быть обработана в главном потоке (main.py).
        """
        tool_calls = response_data.get("tool_calls", [])
        
        if not tool_calls:
            return response_data
        
        # Фильтруем tool_calls: обрабатываем все КРОМЕ text_to_audio
        calls_to_process = [tc for tc in tool_calls if tc.get("function", {}).get("name") != "text_to_audio"]
        calls_to_skip = [tc for tc in tool_calls if tc.get("function", {}).get("name") == "text_to_audio"]
        
        # Если нет функций для обработки - возвращаем как есть
        if not calls_to_process:
            logger.info(f"⏭️ Пропускаем text_to_audio функции ({len(calls_to_skip)}шт) - обработка в main.py")
            return response_data
        
        logger.info(f"🔄 Обработка {len(calls_to_process)} tool calls (пропускаем {len(calls_to_skip)} text_to_audio)...")
        
        # Добавляем assistant ответ с tool calls
        messages.append({
            "role": "assistant",
            "content": response_data.get("content", ""),
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"]
                    }
                }
                for tc in tool_calls  # Все calls - и обрабатываемые и нет
            ]
        })
        
        # Выполняем ТОЛЬКО non-text_to_audio функции
        tool_results = self.tool_executor.execute_tool_calls_parallel(calls_to_process)
        logger.info(f"✅ Tool calls выполнены")
        
        # Добавляем результаты
        for tool_id, result in tool_results.items():
            messages.append({
                "role": "tool",
                "tool_call_id": tool_id,
                "content": json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
            })
        
        # Отправляем еще раз с результатами
        logger.info("📤 Отправляем результаты tool calls обратно в API для получения финального ответа...")
        return self.send_message(messages)
    
    def set_model(self, model: str) -> None:
        """Устанавливает новую модель."""
        self.model = model
        logger.info(f"Модель изменена на: {model}")
    
    def set_tool_handlers(self, tool_handlers: Dict[str, callable]) -> None:
        """Устанавливает обработчики инструментов."""
        self.tool_executor = ToolExecutor(tool_handlers)
        logger.info(f"Tool handlers обновлены: {list(tool_handlers.keys())}")
