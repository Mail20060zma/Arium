"""Регистрация инструментов для LLM runtime."""

from datetime import datetime
from typing import Callable, Dict, Iterable, Optional, Any

from app.tools.audio import TOOL_HANDLERS as AUDIO_TOOL_HANDLERS
from app.tools.weather import get_weather
from app.tools.search import search_web
from app.data.vector_memory import vector_memory
from app.tools.screen_control import screen_view, screen_type, screen_list_windows, screen_window_controls, screen_window_control_action, screen_open_app


def _not_implemented_tool(tool_name: str) -> Dict[str, str]:
	return {
		"status": "not_implemented",
		"tool": tool_name,
		"message": f"Инструмент '{tool_name}' пока не реализован в этом runtime.",
	}


def _get_current_time() -> Dict[str, str]:
	now = datetime.now()
	return {
		"status": "success",
		"time": now.strftime("%H:%M:%S"),
		"date": now.strftime("%d.%m.%Y"),
		"datetime": now.strftime("%d.%m.%Y %H:%M:%S"),
	}


def _ignore_user_handler(reason: str = "") -> Dict[str, str]:
	return {
		"status": "success",
		"message": f"Ок, я промолчу. Причина: {reason}"
	}


def _memory_save_handler(content: str, category: str = "general") -> Dict[str, str]:
    res = vector_memory.save_memory(content, category)
    return {"status": "success", "message": res}


def _memory_search_handler(query: str) -> Dict[str, Any]:
    res = vector_memory.search_memory(query)
    if not res:
        return {"status": "success", "message": "Ничего не найдено по этому запросу."}
    return {"status": "success", "results": res}


def _memory_delete_handler(memory_id: str) -> Dict[str, str]:
    res = vector_memory.delete_memory(memory_id)
    return {"status": "success", "message": res}


def _memory_list_handler() -> Dict[str, Any]:
    res = vector_memory.list_memories()
    if not res:
        return {"status": "success", "message": "Память пуста."}
    return {"status": "success", "results": res}


def get_tool_handlers(
	text_to_audio_handler: Optional[Callable] = None,
	enabled_tools: Optional[Iterable[str]] = None,
) -> Dict[str, Callable]:
	"""Возвращает словарь обработчиков инструментов для LLM.

	Args:
		text_to_audio_handler: Переопределение обработчика text_to_audio для конкретного runtime.
		enabled_tools: Если задан, возвращаются только перечисленные инструменты.
	"""
	handlers: Dict[str, Callable] = {
		"search_web": search_web,
		"get_weather": get_weather,
		"get_current_time": _get_current_time,
		"ignore_user": _ignore_user_handler,
		"memory_save": _memory_save_handler,
		"memory_search": _memory_search_handler,
		"memory_delete": _memory_delete_handler,
		"memory_list": _memory_list_handler,
		"screen_view": screen_view,
		"screen_type": screen_type,
		"screen_list_windows": screen_list_windows,
		"screen_window_controls": screen_window_controls,
		"screen_window_control_action": screen_window_control_action,
		"screen_open_app": screen_open_app,
	}

	# По умолчанию используем реализацию из audio.py, но core может подменить своим хендлером.
	handlers.update(AUDIO_TOOL_HANDLERS)
	if text_to_audio_handler is not None:
		handlers["text_to_audio"] = text_to_audio_handler

	if enabled_tools is not None:
		enabled_set = set(enabled_tools)
		handlers = {name: func for name, func in handlers.items() if name in enabled_set}

	return handlers


__all__ = ["get_tool_handlers"]
