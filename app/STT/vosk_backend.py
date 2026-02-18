import json
import queue
import sys
import threading
import urllib.request
import zipfile
import time
from pathlib import Path
from typing import Generator

import pyaudio
from vosk import Model, KaldiRecognizer


class VoskSTT:
    MODEL_URLS = {
        "vosk-model-ru-0.42": "https://alphacephei.com/vosk/models/vosk-model-ru-0.42.zip",
        "vosk-model-small-ru-0.22": "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip",
        "vosk-recasepunc-ru-0.22": "https://alphacephei.com/vosk/models/vosk-recasepunc-ru-0.22.zip",
        "vosk-model-small-ru-0.15": "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.15.zip",
    }

    # Оптимизированные параметры для скорости
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000  # Vosk требует 16 кГц
    CHUNK = 128  # Меньше = быстрее отклик (2048 ~ 128 мс)
    SILENCE_THRESHOLD = 0.01
    MIN_SILENCE_DURATION = 0.5  # секунды тишины для завершения фразы

    def __init__(self, model_name: str, model_dir: Path | str):
        """
        Инициализация Vosk STT с автоматической загрузкой модели.
        
        Использует прямой интерфейс PyAudio + Vosk (без speech_recognition)
        для максимальной скорости и низкой задержки.
        """
        if model_name is None:
            raise ValueError("Vosk backend requires a valid model_name (not None)")
        
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.model_path = self.model_dir / model_name
        
        if not self.model_path.exists():
            self._download_model(model_name)
        
        try:
            print(f"📥 Загрузка модели Vosk: {model_name}")
            self.model = Model(str(self.model_path))
            print("✅ Модель Vosk готова")
        except Exception as e:
            raise RuntimeError(f"Failed to load Vosk model from {self.model_path}: {e}")
        
        # Потокобезопасные очереди
        self._audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=100)
        self._result_queue: queue.Queue[str | None] = queue.Queue()
        
        # Флаги управления потоками
        self._running = False
        self._threads: list[threading.Thread] = []
        self._pyaudio: pyaudio.PyAudio | None = None
        self._stream: pyaudio.Stream | None = None

    def _download_model(self, model_name: str):
        if model_name not in self.MODEL_URLS:
            raise ValueError(
                f"Unknown Vosk model '{model_name}'. Supported models: {list(self.MODEL_URLS.keys())}"
            )
        
        url = self.MODEL_URLS[model_name]
        archive_name = url.split("/")[-1]
        archive_path = self.model_dir / archive_name
        
        print(f"📥 Скачивание модели Vosk: {model_name}")
        print(f"   Источник: {url}")
        
        try:
            def reporthook(block_num, block_size, total_size):
                downloaded = block_num * block_size
                if total_size > 0:
                    percent = min(100, downloaded * 100 / total_size)
                    print(f"\r   Загружено: {downloaded / 1024 / 1024:.1f} MB / {total_size / 1024 / 1024:.1f} MB ({percent:.0f}%)", end="")
                else:
                    print(f"\r   Загружено: {downloaded / 1024 / 1024:.1f} MB", end="")
            
            urllib.request.urlretrieve(url, archive_path, reporthook)
            print("\n✅ Загрузка завершена")
            
            print(f"📦 Распаковка архива в {self.model_dir}...")
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                zip_ref.extractall(self.model_dir)
            
            archive_path.unlink()
            print(f"✅ Модель готова: {self.model_path}")
            
        except Exception as e:
            if archive_path.exists():
                archive_path.unlink()
            raise RuntimeError(f"Failed to download/extract Vosk model '{model_name}': {e}")

    def stream(self) -> Generator[str, None, None]:
        """
        Генератор распознанных фраз в реальном времени.
        
        Запускает 3 потока:
        1. Запись аудио с микрофона → очередь
        2. Обработка аудио + распознавание → очередь результатов
        3. (неявно) основной поток: yield из очереди результатов
        
        Yields:
            str: распознанный текст фразы
            
        Пример использования:
            stt = VoskSTT("vosk-model-small-ru-0.22", Path("model/vosk"))
            for text in stt.stream():
                print(f"Распознано: {text}")
        """
        if self._running:
            raise RuntimeError("Stream already running. Create a new instance for parallel streams.")
        
        self._running = True
        
        try:
            # Инициализация PyAudio
            self._pyaudio = pyaudio.PyAudio()
            self._stream = self._pyaudio.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                frames_per_buffer=self.CHUNK,
                stream_callback=self._audio_callback
            )
            
            # Создание распознавателя (отдельный экземпляр для потока обработки)
            recognizer = KaldiRecognizer(self.model, self.RATE)
            recognizer.SetWords(False)  # Отключаем слова для скорости
            
            # Запуск потоков
            process_thread = threading.Thread(
                target=self._processing_thread,
                args=(recognizer,),
                daemon=True,
                name="VoskProcessing"
            )
            
            self._threads = [process_thread]
            self._stream.start_stream()
            process_thread.start()
            
            # Генератор: читаем из очереди результатов
            while self._running:
                try:
                    item = self._result_queue.get(timeout=0.1)
                    if item is None:  # Сигнал завершения
                        break
                    yield item
                except queue.Empty:
                    continue
                    
        finally:
            # Очистка ресурсов
            self._running = False
            
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
            
            if self._pyaudio:
                self._pyaudio.terminate()
            
            # Ожидание завершения потоков
            for t in self._threads:
                t.join(timeout=1.0)
            
            # Очистка очередей
            while not self._audio_queue.empty():
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    break
            
            while not self._result_queue.empty():
                try:
                    self._result_queue.get_nowait()
                except queue.Empty:
                    break

    def _audio_callback(self, in_data, frame_count, time_info, status):
        """Callback для микрофона — максимально быстрый захват аудио."""
        if self._running:
            try:
                self._audio_queue.put_nowait(in_data)
            except queue.Full:
                # Пропускаем фрейм при переполнении (лучше потерять данные, чем блокировать)
                pass
        return (in_data, pyaudio.paContinue)

    def _processing_thread(self, recognizer: KaldiRecognizer):
        """Поток обработки аудио с детектированием пауз (VAD)."""
        last_audio_chunks: list[bytes] = []
        silence_start: float | None = None
        buffer_duration = 0.4  # Собираем аудио за 0.4 сек для баланса скорость/точность
        
        try:
            while self._running:
                # Собираем аудио за короткий интервал
                audio_chunks: list[bytes] = []
                start_time = time.time()
                
                while time.time() - start_time < buffer_duration and self._running:
                    try:
                        data = self._audio_queue.get(timeout=0.01)
                        audio_chunks.append(data)
                    except queue.Empty:
                        continue
                
                if not audio_chunks:
                    continue
                
                # Анализ тишины (оптимизированный)
                if self._is_silence(audio_chunks):
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start > self.MIN_SILENCE_DURATION:
                        # Длительная пауза — завершаем текущую фразу
                        if last_audio_chunks:
                            self._recognize_and_queue(recognizer, b''.join(last_audio_chunks))
                            last_audio_chunks.clear()
                        silence_start = None
                else:
                    # Речь обнаружена — сбрасываем таймер тишины
                    silence_start = None
                    last_audio_chunks.extend(audio_chunks)
                    
                    # Распознавание текущего буфера (частичное)
                    if recognizer.AcceptWaveform(b''.join(audio_chunks)):
                        result = json.loads(recognizer.Result())
                        text = result.get("text", "").strip()
                        if text:
                            self._result_queue.put(text)
                            last_audio_chunks.clear()  # Сбрасываем буфер после распознавания
                
        except Exception as e:
            print(f"⚠️ Ошибка обработки Vosk: {e}", file=sys.stderr)
        finally:
            self._result_queue.put(None)  # Сигнал завершения

    def _is_silence(self, chunks: list[bytes]) -> bool:
        """Быстрая проверка на тишину через RMS."""
        try:
            import numpy as np
            audio_data = b''.join(chunks)
            audio_np = np.frombuffer(audio_data, dtype=np.int16)
            if len(audio_np) == 0:
                return True
            rms = np.sqrt(np.mean(audio_np.astype(np.float32) ** 2))
            return rms < self.SILENCE_THRESHOLD
        except Exception:
            return False

    def _recognize_and_queue(self, recognizer: KaldiRecognizer, audio_data: bytes):
        """Распознавание полной фразы и помещение в очередь."""
        try:
            # Сброс распознавателя для новой фразы
            temp_recognizer = KaldiRecognizer(self.model, self.RATE)
            temp_recognizer.SetWords(False)
            
            if temp_recognizer.AcceptWaveform(audio_data):
                result = json.loads(temp_recognizer.Result())
            else:
                result = json.loads(temp_recognizer.FinalResult())
            
            text = result.get("text", "").strip()
            if text:
                self._result_queue.put(text)
        except Exception as e:
            print(f"⚠️ Ошибка финального распознавания: {e}", file=sys.stderr)