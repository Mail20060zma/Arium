"""
Tool для преобразования текста в аудио.
Используется для tool calling из LLM.
"""
import logging
import json
from typing import Dict, Any, Optional
from pathlib import Path
import uuid

logger = logging.getLogger(__name__)

# Глобальная référence на TTS модель (устанавливается из main.py)
_tts_model = None
_audio_output_dir = None


def set_tts_model(model) -> None:
    """Устанавливает глобальную ссылку на TTS модель."""
    global _tts_model
    _tts_model = model


def set_audio_output_dir(output_dir: Path) -> None:
    """Устанавливает директорию для сохранения аудиофайлов."""
    global _audio_output_dir
    _audio_output_dir = output_dir
    _audio_output_dir.mkdir(parents=True, exist_ok=True)


def text_to_audio(text: str, speaker: str = "kseniya") -> Dict[str, Any]:
    """
    Преобразует текст в аудио и сохраняет в файл.
    
    Args:
        text: Текст для синтеза (валидный текст или SSML)
        speaker: Спикер для синтеза (по умолчанию kseniya)
    
    Returns:
        Словарь с информацией о созданном аудиофайле:
        {
            "status": "success" или "error",
            "audio_file": путь к файлу (если успешно),
            "duration": длительность в секундах (если успешно),
            "text": исходный текст,
            "error": сообщение об ошибке (если ошибка)
        }
    """
    try:
        if not _tts_model:
            logger.error("❌ TTS модель не инициализирована")
            return {
                "status": "error",
                "text": text,
                "error": "TTS модель не инициализирована"
            }
        
        if not text or not text.strip():
            logger.error("❌ Пустой текст для синтеза")
            return {
                "status": "error",
                "text": text,
                "error": "Пустой текст"
            }
        
        logger.info(f"🎵 Синтез аудио ({speaker}): {text[:50]}...")
        
        # Синтезируем аудио с использованием synthesize_auto
        audio_tensor = _tts_model.synthesize_auto(text, speaker=speaker)
        
        # Генерируем уникальное имя файла
        file_id = str(uuid.uuid4())[:8]
        audio_file = _audio_output_dir / f"audio_{file_id}.wav"
        
        # Сохраняем аудио
        from scipy.io import wavfile
        import numpy as np
        
        audio_np = audio_tensor.cpu().numpy()
        wavfile.write(str(audio_file), 48000, (audio_np * 32767).astype(np.int16))
        
        # Приблизительная длительность в секундах
        duration = len(audio_np) / 48000
        
        logger.info(f"✅ Аудио создано: {audio_file.name} ({duration:.2f}s)")
        
        return {
            "status": "success",
            "audio_file": str(audio_file),
            "duration": round(duration, 2),
            "text": text,
            "speaker": speaker
        }
    
    except Exception as e:
        logger.error(f"❌ Ошибка синтеза аудио: {e}")
        return {
            "status": "error",
            "text": text,
            "error": str(e)
        }


# Регистрация tool handler для LLM
TOOL_HANDLERS = {
    "text_to_audio": text_to_audio
}
