from pathlib import Path
from .vosk_backend import VoskSTT
from .whisper_backend import WhisperSTT
from .google_backend import GoogleSTT

__all__ = ["create_stt", "VoskSTT", "WhisperSTT", "GoogleSTT"]


def _get_project_root() -> Path:
    """
    Определяет корневую директорию проекта (родитель app/).
    Работает при запуске из любой поддиректории.
    """
    # Текущий файл (__init__.py) находится в app/STT/
    current_file = Path(__file__).resolve()
    # Поднимаемся: STT/ → app/ → корень проекта
    return current_file.parent.parent


def create_stt(backend: str, model_name: str | None):
    """
    Фабричная функция для создания экземпляра STT с выбранным бэкендом.
    
    Args:
        backend: 'vosk', 'whisper' или 'google'
        model_name: имя модели для vosk/whisper, None для google
    
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
        return VoskSTT(model_name, model_dir=model_dir / "vosk")
    elif backend == "whisper":
        if model_name is None:
            raise ValueError("Whisper backend requires a valid model_name (not None)")
        return WhisperSTT(model_name, model_dir=model_dir / "whisper")
    elif backend == "google":
        if model_name is not None:
            raise ValueError("Google backend requires model_name=None")
        return GoogleSTT(model_name)
    else:
        raise ValueError(f"Unknown backend: {backend}. Supported: 'vosk', 'whisper', 'google'")