import queue
import threading
from typing import Generator

import speech_recognition as sr


class GoogleSTT:
    def __init__(self, model_name: None):
        if model_name is not None:
            raise ValueError("Google backend requires model_name=None")
        
        self._recognizer = sr.Recognizer()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._running = False

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
            with sr.Microphone() as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=0.5)
                
                while self._running:
                    try:
                        audio = self._recognizer.listen(source)
                        text = self._recognizer.recognize_google(
                            audio,
                            language="ru-RU",
                            show_all=False
                        ).strip()
                        
                        if text:
                            self._queue.put(text)
                    
                    # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: игнорируем UnknownValueError (тишина/нераспознанная речь — норма)
                    except sr.UnknownValueError:
                        continue  # Просто продолжаем слушать
                    
                    except sr.RequestError as e:
                        # Критическая ошибка (сеть, лимиты) — пробрасываем
                        raise sr.RequestError(f"Google Speech Recognition request failed: {e}")
                    except Exception as e:
                        raise RuntimeError(f"Audio capture error: {e}")
        except Exception:
            self._queue.put(None)
            raise
        finally:
            self._queue.put(None)