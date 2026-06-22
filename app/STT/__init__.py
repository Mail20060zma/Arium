from pathlib import Path

__all__ = ["create_stt", "VoskSTT", "WhisperSTT", "GoogleSTT"]

def __getattr__(name):
    if name == 'VoskSTT':
        from .vosk_backend import VoskSTT
        return VoskSTT
    if name == 'WhisperSTT':
        from .whisper_backend import WhisperSTT
        return WhisperSTT
    if name == 'GoogleSTT':
        from .google_backend import GoogleSTT
        return GoogleSTT
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def _get_project_root() -> Path:
    """
    Определяет корневую директорию проекта (родитель app/).
    Работает при запуске из любой поддиректории.
    """
    current_file = Path(__file__).resolve()
    return current_file.parent.parent

def create_stt(backend: str, model_name: str | None, device: str | None = None):
    """
    Фабричная функция для создания экземпляра STT с выбранным бэкендом.
    
    Args:
        backend: 'vosk', 'whisper' или 'google'
        model_name: имя модели для vosk/whisper, None для google
        device: устройство ('cuda', 'cuda:1', 'cpu') для whisper
    
    Returns:
        Экземпляр соответствующего класса STT
    
    Raises:
        ValueError: при неизвестном бэкенде или некорректном model_name
    """
    project_root = _get_project_root()
    model_dir = project_root / "model"
    
    if backend == "vosk":
        if model_name is None:
            raise ValueError("Vosk backend requires a valid model_name (not None)")
        from .vosk_backend import VoskSTT
        return VoskSTT(model_name, model_dir=model_dir / "vosk")
    elif backend == "whisper":
        if model_name is None:
            raise ValueError("Whisper backend requires a valid model_name (not None)")
        from .whisper_backend import WhisperSTT
        return WhisperSTT(model_name, model_dir=model_dir / "whisper", device=device)
    elif backend == "google":
        if model_name is not None:
            raise ValueError("Google backend requires model_name=None")
        from .google_backend import GoogleSTT
        return GoogleSTT(model_name)
    else:
        raise ValueError(f"Unknown backend: {backend}. Supported: 'vosk', 'whisper', 'google'")