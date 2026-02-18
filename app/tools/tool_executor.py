"""
Парсер и выполнитель инструментов (функций) из ответов ИИ.
Работает с tool_calls из OpenAI API response.
"""
import json
import logging
from typing import Dict, Any, List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from .instructions import TOOLS


logger = logging.getLogger(__name__)


class ToolCallParser:
    """Парсит и валидирует tool calls из ответов ИИ."""
    
    @staticmethod
    def parse_tool_calls(response_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Парсит tool_calls из ответа ИИ.
        
        Args:
            response_data: Данные ответа от ИИ (со streaming)
            
        Returns:
            Список распарсенных tool calls
        """
        tool_calls = response_data.get("tool_calls", [])
        parsed_calls = []
        
        for tc in tool_calls:
            try:
                func_name = tc.get("function", {}).get("name")
                if not func_name:
                    logger.warning(f"Tool call без имени функции: {tc}")
                    continue
                
                # Парсим аргументы если они в JSON
                args_str = tc.get("function", {}).get("arguments", "{}")
                if isinstance(args_str, str):
                    try:
                        args = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        logger.warning(f"Невалидный JSON в arguments для {func_name}: {args_str}")
                        args = {}
                else:
                    args = args_str if isinstance(args_str, dict) else {}
                
                parsed_calls.append({
                    "id": tc.get("id"),
                    "function": func_name,
                    "arguments": args
                })
                
                logger.debug(f"Распарсен tool call: {func_name}({args})")
            
            except Exception as e:
                logger.error(f"Ошибка парсинга tool call: {e}")
                continue
        
        return parsed_calls
    
    @staticmethod
    def validate_tool_call(func_name: str) -> bool:
        """Проверяет, есть ли функция в доступных инструментах."""
        return func_name in TOOLS


class ToolExecutor:
    """Выполняет инструменты (функции) из ответов ИИ."""
    
    def __init__(self, tool_handlers: Dict[str, Callable]):
        """
        Args:
            tool_handlers: Словарь {имя_функции: функция-обработчик}
        """
        self.tool_handlers = tool_handlers
        self.logger = logging.getLogger(__name__)
    
    def execute_tool(self, func_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Выполняет одну функцию.
        
        Args:
            func_name: Имя функции
            arguments: Аргументы функции
            
        Returns:
            Результат выполнения функции
        """
        if func_name not in self.tool_handlers:
            error_msg = f"Функция '{func_name}' не найдена"
            self.logger.error(f"❌ {error_msg}")
            return {
                "status": "error",
                "function": func_name,
                "error": error_msg
            }
        
        try:
            self.logger.info(f"⚙️ Выполнение функции: {func_name}({arguments})")
            
            func = self.tool_handlers[func_name]
            result = func(**arguments)
            
            self.logger.info(f"✅ Функция {func_name} выполнена успешно")
            
            return {
                "status": "success",
                "function": func_name,
                "result": result
            }
        
        except TypeError as e:
            error_msg = f"Неверные аргументы для {func_name}: {str(e)}"
            self.logger.error(f"❌ {error_msg}")
            return {
                "status": "error",
                "function": func_name,
                "error": error_msg
            }
        
        except Exception as e:
            error_msg = f"Ошибка выполнения {func_name}: {str(e)}"
            self.logger.error(f"❌ {error_msg}")
            return {
                "status": "error",
                "function": func_name,
                "error": error_msg
            }
    
    def execute_tool_calls_sequential(self, tool_calls: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """
        Выполняет tool calls последовательно.
        
        Args:
            tool_calls: Список tool calls в формате OpenAI API
                [{
                    "id": "...",
                    "function": {"name": "...", "arguments": "..."}
                }]
            
        Returns:
            Словарь {tool_id: результат}
        """
        results = {}
        
        for tc in tool_calls:
            tool_id = tc.get("id")
            func_data = tc.get("function", {})
            func_name = func_data.get("name")
            
            # Парсим аргументы
            args_str = func_data.get("arguments", "{}")
            if isinstance(args_str, str):
                try:
                    arguments = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    self.logger.warning(f"Невалидный JSON в arguments для {func_name}: {args_str}")
                    arguments = {}
            else:
                arguments = args_str if isinstance(args_str, dict) else {}
            
            result = self.execute_tool(func_name, arguments)
            results[tool_id] = result
        
        return results
    
    def execute_tool_calls_parallel(self, tool_calls: List[Dict[str, Any]], max_workers: int = 3) -> Dict[str, Dict[str, Any]]:
        """
        Выполняет tool calls параллельно.
        
        Args:
            tool_calls: Список tool calls в формате OpenAI API
                [{
                    "id": "...",
                    "function": {"name": "...", "arguments": "..."}
                }]
            max_workers: Максимальное количество потоков
            
        Returns:
            Словарь {tool_id: результат}
        """
        results = {}
        
        if not tool_calls:
            return results
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            
            for tc in tool_calls:
                tool_id = tc.get("id")
                func_data = tc.get("function", {})
                func_name = func_data.get("name")
                
                # Парсим аргументы
                args_str = func_data.get("arguments", "{}")
                if isinstance(args_str, str):
                    try:
                        arguments = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        self.logger.warning(f"Невалидный JSON в arguments для {func_name}: {args_str}")
                        arguments = {}
                else:
                    arguments = args_str if isinstance(args_str, dict) else {}
                
                future = executor.submit(self.execute_tool, func_name, arguments)
                futures[future] = tool_id
            
            for future in as_completed(futures):
                tool_id = futures[future]
                try:
                    result = future.result()
                    results[tool_id] = result
                except Exception as e:
                    self.logger.error(f"❌ Ошибка в потоке выполнения: {e}")
                    results[tool_id] = {
                        "status": "error",
                        "error": str(e)
                    }
        
        return results
    
    def process_response_with_tools(self, response_data: Dict[str, Any], parallel: bool = True) -> Dict[str, Any]:
        """
        Обрабатывает ответ ИИ и выполняет все tool calls.
        
        Args:
            response_data: Данные ответа от ИИ
            parallel: Выполнять ли функции параллельно
            
        Returns:
            Словарь с результатами выполнения
        """
        # Парсим tool calls
        tool_calls = ToolCallParser.parse_tool_calls(response_data)
        
        if not tool_calls:
            return {
                "has_tools": False,
                "response": response_data,
                "tool_results": {}
            }
        
        self.logger.info(f"🔧 Найдено {len(tool_calls)} tool calls для выполнения")
        
        # Выполняем функции
        if parallel:
            tool_results = self.execute_tool_calls_parallel(tool_calls)
        else:
            tool_results = self.execute_tool_calls_sequential(tool_calls)
        
        return {
            "has_tools": True,
            "response": response_data,
            "tool_calls": tool_calls,
            "tool_results": tool_results
        }


def extract_text_for_tts(processed_response: Dict[str, Any], include_tool_results: bool = False) -> Optional[str]:
    """
    Извлекает текст для озвучивания из обработанного ответа.
    
    Args:
        processed_response: Результат от process_response_with_tools
        include_tool_results: Включать ли результаты функций в озвучку
        
    Returns:
        Текст для озвучивания или None
    """
    text_parts = []
    
    # Основной ответ ИИ
    response_data = processed_response.get("response", {})
    if response_data.get("content"):
        text_parts.append(response_data["content"])
    
    # Опционально добавляем результаты функций
    if include_tool_results and processed_response.get("has_tools"):
        tool_results = processed_response.get("tool_results", {})
        for tool_id, result in tool_results.items():
            if result.get("status") == "success":
                result_data = result.get("result", {})
                if isinstance(result_data, dict):
                    # Форматируем результат для озвучивания
                    result_text = json.dumps(result_data, ensure_ascii=False, indent=2)
                    text_parts.append(f"Результат: {result_text}")
                else:
                    text_parts.append(f"Результат: {str(result_data)}")
            else:
                error = result.get("error", "Неизвестная ошибка")
                text_parts.append(f"Ошибка: {error}")
    
    if text_parts:
        return "\n".join(text_parts)
    
    return None
