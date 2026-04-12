from .base import BaseTTS
from .silero import SileroTTS
from .xtts_backend import XTTSBackend

def get_tts_engine(settings) -> BaseTTS:
    backend = settings.get('tts_backend', 'silero')
    
    if backend == 'xtts':
        # Ensure we have the path configured correctly
        # xtts puts stuff in app/model/xtts
        # For simplicity we'll just initialize it with default params
        import os
        from pathlib import Path
        model_dir = str(Path(__file__).parent.parent / "model" / "xtts")
        os.makedirs(model_dir, exist_ok=True)
        return XTTSBackend(model_dir=model_dir, language='ru')
    elif backend == 'silero':
        speaker = settings.get('tts_speaker', 'kseniya')
        model_id = settings.get('tts_silero_model', 'v5_ru')
        device = 'cuda' if __import__('torch').cuda.is_available() else 'cpu'
        return SileroTTS(language='ru', model_id=model_id, speaker=speaker, device=device)
    else:
        raise ValueError(f"Unknown TTS backend: {backend}")

__all__ = ['BaseTTS', 'SileroTTS', 'XTTSBackend', 'get_tts_engine']
