import os
from pathlib import Path

def __getattr__(name):
    if name == 'BaseTTS':
        from .base import BaseTTS
        return BaseTTS
    if name == 'SileroTTS':
        from .silero import SileroTTS
        return SileroTTS
    if name == 'XTTSBackend':
        from .xtts_backend import XTTSBackend
        return XTTSBackend
    if name == 'EdgeTTS':
        from .edge import EdgeTTS
        return EdgeTTS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def get_tts_engine(settings) -> 'BaseTTS':
    backend = settings.get('tts_backend', 'silero')
    
    if backend == 'xtts':
        import torch
        from .xtts_backend import XTTSBackend
        model_dir = str(Path(__file__).parent.parent / "model" / "xtts")
        os.makedirs(model_dir, exist_ok=True)
        device = settings.get('system_device', 'cuda' if torch.cuda.is_available() else 'cpu')
        return XTTSBackend(model_dir=model_dir, language='ru', device=device)
    elif backend == 'silero':
        import torch
        from .silero import SileroTTS
        speaker = settings.get('tts_speaker', 'kseniya')
        model_id = settings.get('tts_silero_model', 'v5_ru')
        device = settings.get('system_device', 'cuda' if torch.cuda.is_available() else 'cpu')
        return SileroTTS(language='ru', model_id=model_id, speaker=speaker, device=device)
    elif backend == 'edge':
        from .edge import EdgeTTS
        return EdgeTTS(settings)
    else:
        raise ValueError(f"Unknown TTS backend: {backend}")

def get_tts_engine_by_name(backend_name: str, **kwargs) -> 'BaseTTS':
    """
    Get a TTS engine instance by backend name (without Settings object).
    Used by download_models.py for selective model downloading.
    
    Args:
        backend_name: 'silero', 'xtts', or 'edge'
        **kwargs: Backend-specific parameters (speaker, model_id, device, model_dir, proxy)
    
    Returns:
        BaseTTS instance
    """
    import torch
    from app.utils.settings import Settings
    settings = Settings().all
    device = kwargs.get('device', settings.get("system_device", 'cuda' if torch.cuda.is_available() else 'cpu'))
    
    if backend_name == 'silero':
        from .silero import SileroTTS
        speaker = kwargs.get('speaker', 'kseniya')
        model_id = kwargs.get('model_id', 'v5_ru')
        return SileroTTS(language='ru', model_id=model_id, speaker=speaker, device=device)
    elif backend_name == 'xtts':
        from .xtts_backend import XTTSBackend
        model_dir = kwargs.get('model_dir', str(Path(__file__).parent.parent / "model" / "xtts"))
        os.makedirs(model_dir, exist_ok=True)
        return XTTSBackend(model_dir=model_dir, language='ru', device=device)
    elif backend_name == 'edge':
        from .edge import EdgeTTS
        edge_settings = {
            'tts_speaker': kwargs.get('speaker', settings.get('tts_speaker', 'ru-RU-SvetlanaNeural'))
        }
        return EdgeTTS(edge_settings)
    else:
        raise ValueError(f"Unknown TTS backend: {backend_name}. Choose 'silero', 'xtts', or 'edge'")

__all__ = ['BaseTTS', 'SileroTTS', 'XTTSBackend', 'EdgeTTS', 'get_tts_engine', 'get_tts_engine_by_name']
