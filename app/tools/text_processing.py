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


def split_long_sentence(sentence: str, max_len: int = 160) -> List[str]:
    """Разбивает длинное предложение по запятым или другим разделителям, чтобы уложиться в max_len."""
    if len(sentence) <= max_len:
        return [sentence]
        
    parts = []
    # Пытаемся разбить по запятым, двоеточиям, точкам с запятой
    chunks = re.split(r'(?<=[,;:—])\s+', sentence)
    
    current_part = ""
    for chunk in chunks:
        if not current_part:
            current_part = chunk
        elif len(current_part) + 1 + len(chunk) <= max_len:
            current_part += " " + chunk
        else:
            parts.append(current_part)
            current_part = chunk
            
    if current_part:
        parts.append(current_part)
        
    # Если даже после этого есть куски больше max_len, рубим по пробелам
    final_parts = []
    for part in parts:
        if len(part) <= max_len:
            final_parts.append(part)
        else:
            words = part.split()
            cur = ""
            for word in words:
                if not cur:
                    cur = word
                elif len(cur) + 1 + len(word) <= max_len:
                    cur += " " + word
                else:
                    final_parts.append(cur)
                    cur = word
            if cur:
                final_parts.append(cur)
                
    return final_parts


def split_text_to_sentences(text: str, max_len: int = 160) -> List[str]:
    """
    Разбивает текст на предложения.
    Корректно обрабатывает SSML и обычный текст.
    Обеспечивает, что длина каждого куска не превышает max_len (важно для XTTS).
    
    Args:
        text: Текст для разбиения
        max_len: Максимальная длина одного куска
    
    Returns:
        Список предложений
    """
    if not text or not text.strip():
        return []
    
    text = text.strip()
    
    # Если это SSML - используем специальное разбиение
    if is_ssml(text):
        base_sentences = split_ssml_preserving(text)
    else:
        # Обычное разбиение на предложения
        base_sentences = re.split(r'(?<=[.!?])\s+', text)
    
    # Дополнительно разбиваем длинные предложения
    result = []
    for s in base_sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) > max_len:
            result.extend(split_long_sentence(s, max_len))
        else:
            result.append(s)
            
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


def extract_clean_text(text: str) -> str:
    """Очищает текст от служебных тегов локальных tool call-ов."""
    if not text:
        return ""
    
    cleaned = text.strip()
    
    # 1. Удаляем префикс <|tool_call>call:text_to_audio{text:<|...
    # Например: <|tool_call>call:text_to_audio{text:<|"|> или <|tool_call>call:text_to_audio{text:<|\"|>
    cleaned = re.sub(r'^<\|tool_call\|?>\s*call:text_to_audio\s*\{\s*text\s*:\s*<\|[\\\"\'\s\|><]*', '', cleaned, flags=re.IGNORECASE)
    
    # 2. Удаляем постфикс ... |>}<tool_call|>
    cleaned = re.sub(r'[\\\"\'\s\|><]*\s*\}\s*<tool_call\|?>$', '', cleaned, flags=re.IGNORECASE)
    
    return cleaned.strip()
