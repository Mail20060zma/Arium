"""
Утилиты для разбиения текста на предложения.
Корректно обрабатывает обычный текст и SSML разметку.
"""
import re
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


def is_ssml(text: str) -> bool:
    """Проверяет, содержит ли текст SSML разметку."""
    return '<speak' in text or '<prosody' in text or '<break' in text or '</speak>' in text


def strip_ssml_tags(text: str) -> str:
    """Удаляет SSML-теги, оставляя только текст."""
    return re.sub(r"<[^>]+>", " ", text)


def normalize_words(text: str) -> List[str]:
    """Возвращает список слов без пунктуации и SSML-тегов."""
    if not text:
        return []
    if is_ssml(text):
        text = strip_ssml_tags(text)
    return re.findall(r"[^\W_]+", text, flags=re.UNICODE)


def compute_word_timings_from_weight(words: List[str], ms_per_weight: float) -> List[Dict[str, object]]:
    """Оценивает тайминги слов из веса (длина слова) и ms_per_weight."""
    timings: List[Dict[str, object]] = []
    if not words:
        return timings

    cursor = 0.0
    for word in words:
        weight = max(len(word), 1)
        start_ms = int(round(cursor))
        cursor += weight * ms_per_weight
        end_ms = int(round(cursor))
        timings.append({
            "word": word,
            "start_ms": start_ms,
            "end_ms": end_ms,
        })
    return timings


def split_ssml_preserving(text: str) -> List[str]:
    """
    Разбивает SSML текст на предложения, сохраняя теги.
    
    Args:
        text: SSML текст для разбиения
    
    Returns:
        Список предложений (каждый может содержать SSML теги)
    """
    # Защищаем SSML теги чтобы не разбить их
    protected_text = text
    
    # Заменяем SSML теги на плейсхолдеры
    tags_map = {}
    tag_pattern = r'<[^>]+>'
    
    for match in re.finditer(tag_pattern, text):
        tag = match.group()
        placeholder = f"__TAG_{len(tags_map)}__"
        tags_map[placeholder] = tag
        protected_text = protected_text.replace(tag, placeholder)
    
    # Разбиваем на предложения
    sentences = re.split(r'(?<=[.!?])\s+', protected_text.strip())
    
    # Восстанавливаем теги
    restored_sentences = []
    for sent in sentences:
        if sent.strip():
            restored = sent
            for placeholder, tag in tags_map.items():
                restored = restored.replace(placeholder, tag)
            restored_sentences.append(restored.strip())
    
    return restored_sentences


def split_text_to_sentences(text: str) -> List[str]:
    """
    Разбивает текст на предложения.
    Корректно обрабатывает SSML и обычный текст.
    
    Args:
        text: Текст для разбиения
    
    Returns:
        Список предложений
    """
    if not text or not text.strip():
        return []
    
    text = text.strip()
    
    # Если это SSML - используем специальное разбиение
    if is_ssml(text):
        return split_ssml_preserving(text)
    
    # Обычное разбиение на предложения
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    # Фильтруем пустые предложения и очищаем
    result = [s.strip() for s in sentences if s.strip()]
    
    return result


def filter_history_messages(messages: List[Dict[str, Any]], 
                            max_messages: int = 100) -> List[Dict[str, Any]]:
    """
    Фильтрует историю сообщений, оставляя только последние N сообщений.
    
    Args:
        messages: Полная история сообщений
        max_messages: Максимальное количество сообщений
    
    Returns:
        Отфильтрованная история (последние max_messages)
    """
    if len(messages) <= max_messages:
        return messages
    
    logger.info(f"📊 Фильтрование истории: {len(messages)} → {max_messages} сообщений")
    return messages[-max_messages:]


def prepare_messages_for_api(messages: List[Dict[str, Any]], 
                             system_prompt: str) -> List[Dict[str, Any]]:
    """
    Подготавливает сообщения для отправки в API.
    
    Args:
        messages: История сообщений
        system_prompt: Системный промпт
    
    Returns:
        Список сообщений в формате для API
    """
    api_messages = []
    
    # Добавляем системный промпт
    if system_prompt:
        api_messages.append({
            "role": "system",
            "content": system_prompt
        })
    
    # Добавляем отфильтрованные сообщения
    filtered = filter_history_messages(messages, max_messages=100)
    
    for msg in filtered:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        # Пропускаем служебные роли
        if role not in ["user", "assistant", "tool"]:
            continue
        
        # Для tool сообщений используем специальный формат
        if role == "tool":
            api_messages.append({
                "role": role,
                "tool_call_id": msg.get("tool_call_id"),
                "content": content
            })
        else:
            api_messages.append({
                "role": role,
                "content": content
            })
    
    return api_messages
