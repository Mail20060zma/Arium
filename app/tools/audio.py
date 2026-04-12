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
_tts_streaming = False
_ai_response_queue = None
_abort_event = None

def set_tts_streaming(val: bool) -> None:
    """Включает/выключает потоковую генерацию аудио."""
    global _tts_streaming
    _tts_streaming = val

def set_ai_response_queue(q) -> None:
    """Устанавливает ссылку на очередь вывода."""
    global _ai_response_queue
    _ai_response_queue = q

def set_abort_event(ev) -> None:
    """Устанавливает объект Event для прерывания генерации."""
    global _abort_event
    _abort_event = ev

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
        
        if _tts_streaming and _ai_response_queue and _abort_event:
            logger.info("🔊 Начинаем streaming аудио в очередь...")
            sample_rate = _tts_model.sample_rate
            try:
                for chunk in _tts_model.stream_audio(text, _abort_event, speaker=speaker):
                    if _abort_event.is_set():
                        break
                    _ai_response_queue.put({"type": "audio_chunk", "chunk": chunk, "sample_rate": sample_rate})
            except Exception as e:
                logger.error(f"❌ Ошибка потокового синтеза: {e}")
            
            return {
                "status": "success",
                "text": text,
                "speaker": speaker,
                "streamed": True
            }
            
        else:
            # Генерируем уникальное имя файла
            file_id = str(uuid.uuid4())[:8]
            audio_file = _audio_output_dir / f"audio_{file_id}.wav"
            
            _tts_model.synthesize_to_file(text, output_path=str(audio_file), speaker=speaker)
            
            # TODO get duration accurately if needed, placeholder below
            duration = 0.0
            
            logger.info(f"✅ Аудио создано: {audio_file.name}")
            
            return {
                "status": "success",
                "audio_file": str(audio_file),
                "duration": duration,
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
