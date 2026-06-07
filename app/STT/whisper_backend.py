import queue
import threading
import wave
import io
import numpy as np
import sys
from pathlib import Path
from typing import Generator

import speech_recognition as sr
import whisper
import torch


class WhisperSTT:
    # Официальные имена моделей Whisper
    OFFICIAL_MODELS = {"tiny", "base", "small", "medium", "large", "large-v2", "large-v3", "large-v3-turbo"}

    def __init__(self, model_name: str, model_dir: Path | str, device: str | None = None):
        if model_name is None:
            raise ValueError("Whisper backend requires a valid model_name (not None)")
        
        # Валидация имени модели (предотвращает ошибки с несуществующими моделями)
        if model_name not in self.OFFICIAL_MODELS:
            print(f"⚠️  Предупреждение: модель '{model_name}' не в списке официальных ({self.OFFICIAL_MODELS})")
            print(f"    Возможные варианты: 'tiny', 'base', 'small', 'medium', 'large', 'large-v2', 'large-v3'")
        
        # Определение устройства
        self.device = device if device else self._get_device()
        self.use_fp16 = self._should_use_fp16(self.device)
        
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            print(f"📥 Загрузка/загрузка из кэша модели Whisper: {model_name}")
            print(f"   Кэш: {self.model_dir}")
            print(f"   Устройство: {self.device} {'(FP16)' if self.use_fp16 else '(FP32)'}")
            
            # 🔑 ИСПРАВЛЕНИЕ: НЕ вызываем .half() вручную — только загрузка на устройство
            self.model = whisper.load_model(
                model_name,
                device=self.device,
                download_root=str(self.model_dir)
            )
            
            print(f"✅ Модель Whisper готова на {self.device}")
        except ImportError:
            raise RuntimeError(
                "PyTorch не установлен. Установите зависимости:\n"
                "  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121\n"
                "  pip install openai-whisper"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to load Whisper model '{model_name}': {e}")
        
        # Инициализация атрибутов (критически важна!)
        self._recognizer = sr.Recognizer()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._running = False

    @staticmethod
    def _get_device() -> str:
        """Определяет доступное устройство: CUDA → CPU."""
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    @staticmethod
    def _should_use_fp16(device: str) -> bool:
        """Определяет, следует ли использовать FP16 (только для современных GPU)."""
        if device.startswith("cuda"):
            try:
                # Get index if specified (e.g., cuda:1)
                idx = int(device.split(":")[1]) if ":" in device else 0
                major, minor = torch.cuda.get_device_capability(idx)
                # FP16 стабилен на архитектурах >= 7.0 (Turing и новее)
                return (major > 7) or (major == 7 and minor >= 5)
            except:
                return False  # Безопасный режим: отключаем FP16 при ошибках
        return False

    def stream(self) -> Generator[str, None, None]:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Stream already running. Create a new instance for parallel streams.")
        
        self._running = True
        self._thread = threading.Thread(target=self._audio_capture_loop, daemon=True)
        self._thread.start()
        
        try:
            while self._running:
                item = self._queue.get()
                if item is None:
                    break
                yield item
        finally:
            self._running = False
            if self._thread:
                self._thread.join(timeout=1.0)

    def _audio_capture_loop(self):
        try:
            with sr.Microphone(sample_rate=16000) as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=0.5)
                
                while self._running:
                    try:
                        audio = self._recognizer.listen(source, phrase_time_limit=10)
                        
                        # Конвертация в WAV 16 кГц моно
                        wav_data = audio.get_wav_data(convert_rate=16000, convert_width=2)
                        audio_array = self._wav_bytes_to_numpy(wav_data)
                        
                        # 🔑 ИСПРАВЛЕНИЕ: передаём fp16 только в transcribe(), НЕ конвертируем модель вручную
                        result = self.model.transcribe(
                            audio_array,
                            language="ru",
                            fp16=self.use_fp16,  # Управление точностью здесь
                            temperature=0.0,
                            compression_ratio_threshold=2.4,
                            logprob_threshold=-1.0
                        )
                        text = result.get("text", "").strip()
                        
                        if text and len(text) > 2:
                            self._queue.put(text)
                    except sr.UnknownValueError:
                        continue
                    except Exception as e:
                        raise RuntimeError(f"Audio capture/transcription error: {e}")
        except Exception:
            self._queue.put(None)
            raise
        finally:
            self._queue.put(None)

    @staticmethod
    def _wav_bytes_to_numpy(wav_bytes: bytes) -> np.ndarray:
        """Конвертация WAV байтов в numpy array (моно, 16 кГц, float32)."""
        with io.BytesIO(wav_bytes) as wav_buffer:
            with wave.open(wav_buffer, "rb") as wav_file:
                if wav_file.getnchannels() != 1:
                    raise ValueError(f"Whisper requires mono audio, got {wav_file.getnchannels()} channels")
                if wav_file.getframerate() != 16000:
                    raise ValueError(f"Whisper requires 16 kHz audio, got {wav_file.getframerate()} Hz")
                
                frames = wav_file.readframes(wav_file.getnframes())
                audio_array = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                return audio_array