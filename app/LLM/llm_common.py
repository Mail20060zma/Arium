"""
Общие утилиты для работы с LLM.
Используются всеми классами обработки LLM (OpenRouter, OpenAI, и т.д.)
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

logger = logging.getLogger(__name__)


class StreamChunkBuffer:
    """Буфер для сборки чанков streaming ответа в полное сообщение."""
    
    def __init__(self):
        self.tool_calls_buffer: Dict = {}
        self.content = ""
        self.usage = {}
    
    def add_delta(self, delta: Dict[str, Any]) -> None:
        """Добавляет delta из streaming чанка."""
        if content := delta.get("content"):
            self.content += content
        
        for tool_call_chunk in delta.get("tool_calls", []):
            index = tool_call_chunk.get("index")
            if index is not None:
                if index not in self.tool_calls_buffer:
                    self.tool_calls_buffer[index] = {
                        "id": tool_call_chunk.get("id"),
                        "type": tool_call_chunk.get("type"),
                        "function": {
                            "name": tool_call_chunk.get("function", {}).get("name", ""),
                            "arguments": ""
                        }
                    }
                
                if "id" in tool_call_chunk and not self.tool_calls_buffer[index]["id"]:
                    self.tool_calls_buffer[index]["id"] = tool_call_chunk["id"]
                if "type" in tool_call_chunk and not self.tool_calls_buffer[index]["type"]:
                    self.tool_calls_buffer[index]["type"] = tool_call_chunk["type"]
                if "name" in tool_call_chunk.get("function", {}):
                    self.tool_calls_buffer[index]["function"]["name"] = tool_call_chunk["function"]["name"]
                
                args_chunk = tool_call_chunk.get("function", {}).get("arguments", "")
                if args_chunk:
                    self.tool_calls_buffer[index]["function"]["arguments"] += args_chunk
        
        if usage := delta.get("usage"):
            self.usage = usage
    
    def get_tool_calls(self) -> List[Dict]:
        """Возвращает отсортированный список tool calls."""
        return [self.tool_calls_buffer[k] for k in sorted(self.tool_calls_buffer.keys())]
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертирует буфер в словарь результата."""
        return {
            "content": self.content,
            "tool_calls": self.get_tool_calls(),
            "usage": self.usage
        }


class ToolExecutor:
    """Выполнитель tool calls с параллельной обработкой."""
    
    def __init__(self, tool_handlers: Dict[str, callable]):
        """
        Args:
            tool_handlers: Словарь {имя_функции: обработчик}
        """
        self.tool_handlers = tool_handlers
    
    def execute_tool_call(self, call: Dict[str, Any]) -> Dict[str, Any]:
        """Выполняет один tool call."""
        call_id = call.get("id")
        func_spec = call.get("function", {})
        func_name = func_spec.get("name")
        args_raw = func_spec.get("arguments", "{}")
        
        try:
            func_args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except Exception as e:
            logger.error(f"Ошибка парсинга аргументов для {func_name}: {e}")
            func_args = {}
        
        logger.info(f"Вызов функции: {func_name}")
        
        handler = self.tool_handlers.get(func_name)
        if handler:
            try:
                tool_result = handler(**func_args)
            except Exception as e:
                tool_result = {"error": f"Ошибка при выполнении {func_name}: {str(e)}"}
                logger.error(tool_result["error"])
        else:
            tool_result = {"error": f"Неизвестная функция: {func_name}"}
            logger.warning(tool_result["error"])
        
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(tool_result, ensure_ascii=False)
        }
    
    def process_tool_calls_parallel(self, tool_calls: List[Dict[str, Any]], 
                                    max_workers: int = 5) -> Dict[str, Any]:
        """Выполняет tool calls параллельно и возвращает dict {call_id: result}."""
        if not tool_calls:
            logger.info("Нет tool calls для выполнения")
            return {}
        
        results = {}
        
        with ThreadPoolExecutor(max_workers=min(max_workers, len(tool_calls))) as executor:
            futures = {executor.submit(self.execute_tool_call, call): call.get("id") for call in tool_calls}
            
            for future in futures:
                try:
                    tool_msg = future.result(timeout=30)
                    call_id = tool_msg.get("tool_call_id")
                    results[call_id] = json.loads(tool_msg.get("content", "{}"))
                except Exception as e:
                    logger.error(f"Ошибка в потоке выполнения: {e}")
                    call_id = futures[future]
                    results[call_id] = {"error": str(e)}
        
        return results


def build_history_message(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """Строит сообщение ассистента для истории из данных ответа."""
    message = {
        "role": "assistant",
        "content": response_data.get("content", ""),
        "timestamp": datetime.now().isoformat()
    }
    
    if tool_calls := response_data.get("tool_calls"):
        message["tool_calls"] = tool_calls
    
    return message


def create_error_message(error_text: str) -> Dict[str, Any]:
    """Создает сообщение об ошибке для истории."""
    return {
        "role": "error",
        "content": error_text,
        "timestamp": datetime.now().isoformat()
    }
