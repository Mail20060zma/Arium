import time
import logging
import json
from typing import List, Dict, Any, Generator, Optional, Callable
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.LLM.base_handler import BaseLLMHandler

logger = logging.getLogger(__name__)

try:
    import tiktoken
except ImportError:
    tiktoken = None
    logger.warning("tiktoken не установлен. Подсчет токенов будет отключен.")


class UniversalOpenAIHandler(BaseLLMHandler):
    """
    Универсальный обработчик LLM на базе OpenAI SDK.
    Поддерживает:
    - Многопоточный вызов функций (Tool Calling).
    - Остановку генерации на лету (Cancellation Token).
    - Рекурсивный вызов (до 5 раз).
    - Подсчет скорости генерации (t/s).
    """

    def __init__(self, api_key: str, base_url: str, model: str, tool_handlers: Dict[str, Callable] = None):
        self.api_key = api_key if api_key else "" # Some local servers need dummy keys
        self.base_url = base_url
        self.model = model
        self.tool_handlers = tool_handlers or {}
        
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        
        self.encoding = None
        if tiktoken:
            try:
                self.encoding = tiktoken.get_encoding("cl100k_base")
            except Exception as e:
                logger.warning(f"Ошибка загрузки tiktoken: {e}")

        self.token_stats = {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "generation_speed_tps": 0.0
        }
        
    def _count_tokens(self, text: str) -> int:
        if not self.encoding or not text:
            return 0
        try:
            return len(self.encoding.encode(text))
        except:
            return 0

    def get_token_stats(self) -> Dict[str, float]:
        return self.token_stats

    def execute_tool(self, tool_call: Any, tool_name: str) -> Dict[str, Any]:
        """Выполнение одного инструмента синхронно"""
        tool_args_str = tool_call.function.arguments
        
        logger.debug(f"[Tool] Запуск {tool_name} с парам: {tool_args_str}")
        if tool_name not in self.tool_handlers:
            return {
                "tool_call_id": tool_call.id,
                "tool_name": tool_name,
                "result": {"error": f"Неизвестный инструмент: {tool_name}"},
                "success": False
            }
            
        try:
            args_dict = json.loads(tool_args_str) if isinstance(tool_args_str, str) and tool_args_str.strip() else {}
            func = self.tool_handlers[tool_name]
            result = func(**args_dict) if args_dict else func()
            return {
                "tool_call_id": tool_call.id,
                "tool_name": tool_name,
                "result": result,
                "success": True
            }
        except Exception as e:
            logger.error(f"[Tool] Ошибка {tool_name}: {e}")
            return {
                "tool_call_id": tool_call.id,
                "tool_name": tool_name,
                "result": {"error": str(e)},
                "success": False
            }

    def process_tool_calls_parallel(self, tool_calls: List[Any]) -> List[Dict[str, Any]]:
        """Параллельное выполнение списка инструментов"""
        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self.execute_tool, tc, tc.function.name): tc 
                for tc in tool_calls
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    logger.error(f"Ошибка в пуле потоков инструментов: {e}")
        return results

    @staticmethod
    def _extract_reasoning_text(delta: Any) -> str:
        """Пытается извлечь reasoning текст из delta в разных форматах провайдеров."""
        reasoning_text = ""

        for attr_name in ("reasoning", "reasoning_content"):
            attr_value = getattr(delta, attr_name, None)
            if isinstance(attr_value, str):
                reasoning_text += attr_value
            elif isinstance(attr_value, list):
                for item in attr_value:
                    if isinstance(item, str):
                        reasoning_text += item
                    elif isinstance(item, dict):
                        reasoning_text += str(item.get("text", ""))
                    else:
                        reasoning_text += str(getattr(item, "text", ""))

        # Фолбэк: некоторые SDK кладут reasoning в model_dump()
        if not reasoning_text and hasattr(delta, "model_dump"):
            try:
                data = delta.model_dump()
                for key in ("reasoning", "reasoning_content"):
                    value = data.get(key)
                    if isinstance(value, str):
                        reasoning_text += value
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict):
                                reasoning_text += str(item.get("text", ""))
                            else:
                                reasoning_text += str(item)
            except Exception:
                pass

        return reasoning_text

    def send_message_stream(
        self,
        messages: List[Dict[str, Any]],
        tools_definitions: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        cancellation_token: Optional[Callable[[], bool]] = None,
        max_depth: int = 5
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Отправляет запрос к модели и потоком отдает токены.
        Если ответ требует вызов функции - делает вызов, рекурсивно обращается к API, и стримит финальный результат.
        В случае прерывания стрима по cancellation_token возвращает yield {"finish_reason": "cancelled"}.
        """
        if max_depth <= 0:
            logger.warning("Достигнут предел рекурсии (max_depth).")
            yield {"content": "\n[Превышен лимит вызовов инструментов]", "finish_reason": "length"}
            return

        request_kwargs = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "timeout": 60
        }
        if tools_definitions:
            request_kwargs["tools"] = tools_definitions
        if tool_choice is not None:
            request_kwargs["tool_choice"] = tool_choice

        start_time = 0
        tokens_generated = 0
        full_content = ""
        full_reasoning = ""
        
        # Инструменты из стрима
        is_tool_call_stream = False
        tool_calls_buffer = {}  # { index: {"id":..., "type":"function", "function":{"name":..., "arguments":""}} }

        try:
            logger.debug(f"[LLM] Отправка запроса к {self.model} (depth={max_depth})")
            logger.debug(f"[LLM] Base URL = {self.base_url}, API KEY Length = {len(self.api_key) if self.api_key else 0}")

            response = self.client.chat.completions.create(**request_kwargs)
            
            for chunk in response:
                # 1. Проверяем прерывание (Soft Interrupt / PTT)
                if cancellation_token and cancellation_token():
                    logger.debug("[LLM Generator] Получен сигнал прерывания. Закрытие Socket/Response...")
                    response.close() # Закрываем TCP-соединение
                    yield {
                        "type": "status",
                        "content": "",
                        "finish_reason": "cancelled"
                    }
                    return
                
                if start_time == 0:
                    start_time = time.time()
                
                if not chunk.choices:
                    continue
                    
                delta = chunk.choices[0].delta

                reasoning_delta = self._extract_reasoning_text(delta)
                if reasoning_delta:
                    full_reasoning += reasoning_delta
                    yield {
                        "type": "reasoning_delta",
                        "reasoning_content": reasoning_delta,
                        "content": "",
                        "finish_reason": None,
                    }
                
                # Обработка инструмента
                if delta.tool_calls:
                    if not is_tool_call_stream:
                        logger.debug("[LLM Stream] Распознан старт 'tool_calls'")
                    is_tool_call_stream = True
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_buffer:
                            tool_calls_buffer[idx] = {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.function.name or "", "arguments": ""}
                            }
                        if tc.id and not tool_calls_buffer[idx].get("id"):
                            tool_calls_buffer[idx]["id"] = tc.id
                        if tc.function.name:
                            tool_calls_buffer[idx]["function"]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls_buffer[idx]["function"]["arguments"] += tc.function.arguments
                            
                # Обработка текста
                elif delta.content is not None:
                    txt = delta.content
                    full_content += txt
                    tokens_generated += 1
                    
                    # Отдаем текст кусками по мере поступления
                    yield {
                        "type": "content_delta",
                        "content": txt,
                        "finish_reason": None,
                    }
                
                # Причина окончания
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
                    
                    # В конце подсчитываем статистику
                    elapsed = time.time() - start_time
                    if elapsed > 0 and tokens_generated > 0:
                        tps = tokens_generated / elapsed
                        self.token_stats["generation_speed_tps"] = tps
                    
                    # Если закончилось просто текстом
                    if finish_reason == "stop" and not is_tool_call_stream:
                        yield {
                            "type": "status",
                            "content": "",
                            "finish_reason": "stop",
                            "reasoning_content": full_reasoning,
                        }
                        return
                    
                    # Если закончилось вызовом функции(и) - выходим из цикла стриминга для их обработки
                    if finish_reason == "tool_calls" or is_tool_call_stream:
                        break

            # Если мы оказались тут, значит либо сработал timeout/error, либо нужно вызвать инструмент.
            if is_tool_call_stream:
                logger.info(f"Обнаружен вызов {len(tool_calls_buffer)} инструментов в потоке.")

                tool_calls_payload = list(tool_calls_buffer.values())
                yield {
                    "type": "assistant_tool_calls",
                    "tool_calls": tool_calls_payload,
                    "content": "",
                    "finish_reason": None,
                }
                
                # Конвертируем буфер в формат совместимый с API
                assistant_tool_message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls_payload
                }
                messages.append(assistant_tool_message)
                
                # Эмуляция объекта из потока в объекты для параллельного обработчика
                class DummyFunction:
                    def __init__(self, name, arguments):
                        self.name = name
                        self.arguments = arguments
                class DummyToolCall:
                    def __init__(self, id, fn_obj):
                        self.id = id
                        self.function = fn_obj

                tool_calls_objects = []
                for tc in tool_calls_buffer.values():
                    tool_calls_objects.append(
                        DummyToolCall(tc["id"], DummyFunction(tc["function"]["name"], tc["function"]["arguments"]))
                    )

                # Выполняем параллельно
                tool_results = self.process_tool_calls_parallel(tool_calls_objects)
                
                # Добавляем результаты в историю контекста
                for res in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": res["tool_call_id"],
                        "name": res["tool_name"],
                        "content": json.dumps(res["result"], ensure_ascii=False)
                    })

                    yield {
                        "type": "tool_result",
                        "tool_call_id": res["tool_call_id"],
                        "tool_name": res["tool_name"],
                        "result": res["result"],
                        "success": res.get("success", False),
                        "content": "",
                        "finish_reason": None,
                    }

                # Терминальный кейс: если вызваны только озвучивающие инструменты,
                # считаем ход завершенным и НЕ уходим в рекурсивный повтор.
                # Это предотвращает циклы tool_calls -> tool_calls -> ...
                tool_names = {str(res.get("tool_name", "")) for res in tool_results}
                if tool_names and tool_names == {"text_to_audio"}:
                    yield {
                        "type": "status",
                        "content": "",
                        "finish_reason": "stop",
                    }
                    return
                
                # Запускаем рекурсию для получения финального ответа
                yield from self.send_message_stream(
                    messages=messages,
                    tools_definitions=tools_definitions,
                    tool_choice=tool_choice,
                    cancellation_token=cancellation_token,
                    max_depth=max_depth - 1
                )

        except Exception as e:
            logger.error(f"Ошибка стриминга API: {e}", exc_info=True)
            yield {
                "type": "status",
                "content": "",
                "finish_reason": "error",
                "error": str(e),
            }

    def ping_fast_interrupt(self, context_text: str) -> bool:
        """
        Soft Interrupt Classifier.
        Отправляет очень быстрый запрос, чтобы понять, является ли звук "угу", смехом, или перебиванием.
        """
        logger.debug(f"Анализ перебивания: '{context_text}'")
        try:
            # Ограничиваем контекст, это должен быть очень быстрый ответ
            messages = [
                {"role": "system", "content": "You check if user interrupt is a real question/command (return '1') or just background noise/agreement like 'ok', 'aha' (return '0'). Output only '1' or '0'."},
                {"role": "user", "content": context_text}
            ]
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=1,
                temperature=0.0,
                timeout=2.0  # Ограничение по времени 2с
            )
            result = response.choices[0].message.content.strip()
            logger.debug(f"Результат классификации перебивания: {result}")
            return result == '1'
        except Exception as e:
            # В случае любой ошибки (таймаут и т.д.) - не считаем перебиванием (fail open to not interrupt)
            logger.warning(f"Ошибка классификатора перебиваний: {e}")
            return False