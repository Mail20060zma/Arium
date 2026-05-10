"""Регистрация инструментов для LLM runtime."""

from datetime import datetime
from typing import Callable, Dict, Iterable, Optional

from app.tools.audio import TOOL_HANDLERS as AUDIO_TOOL_HANDLERS


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
		"search_web": lambda query: _not_implemented_tool("search_web"),
		"get_weather": lambda location: _not_implemented_tool("get_weather"),
		"get_current_time": _get_current_time,
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
