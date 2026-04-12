import abc
import threading
from typing import Generator, Tuple, Optional
import numpy as np

class BaseTTS(abc.ABC):
    """
    Базовый класс для систем синтеза речи.
    Поддерживает как генерацию файла целиком, так и потоковую генерацию чанков.
    """
    
    @abc.abstractmethod
    def load_model(self):
        """Загружает модель в память."""
        pass
        
    @abc.abstractmethod
    def synthesize_to_file(self, text: str, output_path: str, speaker: Optional[str] = None) -> str:
        """
        Синтезирует полный текст и сохраняет в WAV файл.
        Возвращает путь к сгенерированному файлу.
        """
        pass
        
    @abc.abstractmethod
    def stream_audio(self, text: str, abort_event: threading.Event, speaker: Optional[str] = None) -> Generator[np.ndarray, None, None]:
        """
        Генерирует аудио потоково (чанками).
        Если abort_event.is_set() становится True, генерация должна быть немедленно прервана.
        Yields numpy arrays (float32).
        """
        pass

    @property
    @abc.abstractmethod
    def sample_rate(self) -> int:
        """Возвращает частоту дискретизации сгенерированного аудио."""
        pass
