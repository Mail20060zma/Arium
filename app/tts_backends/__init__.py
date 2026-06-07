from .base import BaseTTS
from .silero import SileroTTS
from .xtts_backend import XTTSBackend
import os
from pathlib import Path
import torch

def get_tts_engine(settings) -> BaseTTS:
    backend = settings.get('tts_backend', 'silero')
    
    if backend == 'xtts':
        model_dir = str(Path(__file__).parent.parent / "model" / "xtts")
        os.makedirs(model_dir, exist_ok=True)
        device = settings.get('system_device', 'cuda' if torch.cuda.is_available() else 'cpu')
        return XTTSBackend(model_dir=model_dir, language='ru', device=device)
    elif backend == 'silero':
        speaker = settings.get('tts_speaker', 'kseniya')
        model_id = settings.get('tts_silero_model', 'v5_ru')
        device = settings.get('system_device', 'cuda' if torch.cuda.is_available() else 'cpu')
        return SileroTTS(language='ru', model_id=model_id, speaker=speaker, device=device)
    else:
        raise ValueError(f"Unknown TTS backend: {backend}")

def get_tts_engine_by_name(backend_name: str, **kwargs) -> BaseTTS:
    """
    Get a TTS engine instance by backend name (without Settings object).
    Used by download_models.py for selective model downloading.
    
    Args:
        backend_name: 'silero' or 'xtts'
        **kwargs: Backend-specific parameters (speaker, model_id, device, model_dir)
    
    Returns:
        BaseTTS instance
    """
    from app.utils.settings import Settings
    settings = Settings().get_all()
    device = kwargs.get('device', settings.get("system_device", 'cuda' if torch.cuda.is_available() else 'cpu'))
    
    if backend_name == 'silero':
        speaker = kwargs.get('speaker', 'kseniya')
        model_id = kwargs.get('model_id', 'v5_ru')
        return SileroTTS(language='ru', model_id=model_id, speaker=speaker, device=device)
    elif backend_name == 'xtts':
        model_dir = kwargs.get('model_dir', str(Path(__file__).parent.parent / "model" / "xtts"))
        os.makedirs(model_dir, exist_ok=True)
        return XTTSBackend(model_dir=model_dir, language='ru', device=device)
    else:
        raise ValueError(f"Unknown TTS backend: {backend_name}. Choose 'silero' or 'xtts'")

__all__ = ['BaseTTS', 'SileroTTS', 'XTTSBackend', 'get_tts_engine', 'get_tts_engine_by_name']
