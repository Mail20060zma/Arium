import sys
import os
import queue
import threading
import time
import numpy as np
import sounddevice as sd
import torch
import whisper
from collections import deque

# Добавляем корень проекта, чтобы если нужно было, импортировать модули
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from app.STT import create_stt

# Настройки аудио
SAMPLE_RATE = 16000
CHUNK_SIZE = 512 # Около 32 мс
VAD_THRESHOLD_START = 0.5
VAD_THRESHOLD_END = 0.3
ENDPOINT_SILENCE_SEC = 1.0 # Ждем 1 секунду тишины, чтобы понять, что человек закончил фразу
MIN_AUDIO_SEC = 0.5 # Минимальная длина аудио для транскрибации

class LiveSTT:
    def __init__(self, backend="whisper", model_name="small"):
        self.device = "cuda:1" if torch.cuda.is_available() else "cpu"
        print(f"[Init] Загрузка VAD (Silero) на {self.device}...")
        
        # Загружаем Silero VAD
        self.vad_model, _ = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            trust_repo=True
        )
        self.vad_model.to(self.device)
        self.backend = backend
        print(f"[Init] Инициализация STT ({backend}, модель: {model_name})...")
        self.stt = create_stt(backend, model_name)
        
        # Очередь для чанков аудио с микрофона
        self.audio_queue = queue.Queue()
        
        # Состояние записи
        self.is_recording = False
        self.audio_buffer = []
        self.silence_chunks = 0
        self.max_silence_chunks = int(ENDPOINT_SILENCE_SEC * SAMPLE_RATE / CHUNK_SIZE)
        
        # Событие прерывания (Barge-in)
        self.abort_playback_event = threading.Event()
        
        # Для эмуляции ответа ИИ
        self.is_ai_speaking = False

    def audio_callback(self, indata, frames, time_info, status):
        if status:
            print(status, file=sys.stderr)
        # Кладем чанк (копия) в очередь
        self.audio_queue.put(indata.copy())

    def start_listening(self):
        print("\n=== Готов к работе! Говорите. ===")
        print("💡 Подсказка: Я буду эмулировать ответ ИИ через 3 секунды после вашей фразы.")
        print("💡 Попробуйте перебить меня, пока я 'говорю'!\n")
        
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            blocksize=CHUNK_SIZE,
            callback=self.audio_callback
        )
        
        with stream:
            while True:
                chunk = self.audio_queue.get()
                
                # Обработка чанка через VAD
                # Silero VAD ожидает тензор формы [1, N]
                audio_tensor = torch.from_numpy(chunk.squeeze()).unsqueeze(0).to(self.device)
                
                with torch.no_grad():
                    prob = self.vad_model(audio_tensor, SAMPLE_RATE).item()
                
                # Обнаружение речи
                if prob > VAD_THRESHOLD_START:
                    if not self.is_recording:
                        # Начало новой фразы
                        self.is_recording = True
                        self.audio_buffer = [chunk]
                        self.silence_chunks = 0
                        
                        # --- МЕХАНИЗМ ПРЕРЫВАНИЯ (BARGE-IN) ---
                        if self.is_ai_speaking:
                            print("\n[BARGE-IN] Вы перебили ИИ! Останавливаем генерацию/воспроизведение.")
                            self.abort_playback_event.set()
                            self.is_ai_speaking = False
                        else:
                            print("\n[🎤] Слышу голос, начинаю запись...", end="", flush=True)
                    else:
                        # Продолжаем запись
                        self.audio_buffer.append(chunk)
                        self.silence_chunks = 0
                        print(".", end="", flush=True)
                        
                elif self.is_recording:
                    # Речь сейчас не обнаружена (возможно, пауза)
                    self.audio_buffer.append(chunk)
                    if prob < VAD_THRESHOLD_END:
                        self.silence_chunks += 1
                        
                    if self.silence_chunks >= self.max_silence_chunks:
                        # Тишина длится достаточно долго -> конец фразы
                        self.is_recording = False
                        self.process_audio_buffer()

    @staticmethod
    def _float32_to_pcm16(audio: np.ndarray) -> bytes:
        clipped = np.clip(audio, -1.0, 1.0)
        int16 = (clipped * 32767.0).astype(np.int16)
        return int16.tobytes()

    def process_audio_buffer(self):
        print("\n[⏳] Обработка аудио...", end="", flush=True)
        # Объединяем чанки
        audio_data = np.concatenate(self.audio_buffer, axis=0).squeeze()
        self.audio_buffer = []
        
        duration = len(audio_data) / SAMPLE_RATE
        if duration < MIN_AUDIO_SEC:
            print(f" (Игнор: слишком коротко {duration:.1f}с)")
            return
            
        # Транскрибация через выбранный бэкенд
        text = ""
        try:
            if self.backend == 'whisper':
                result = self.stt.model.transcribe(
                    audio_data,
                    language='ru',
                    fp16=getattr(self.stt, 'use_fp16', False),
                    temperature=0.0,
                )
                text = result.get('text', '')

            elif self.backend == 'vosk':
                import json
                from vosk import KaldiRecognizer
                pcm = self._float32_to_pcm16(audio_data)
                recognizer = KaldiRecognizer(self.stt.model, SAMPLE_RATE)
                recognizer.SetWords(False)
                
                step = 4000 * 2
                text_parts = []
                for i in range(0, len(pcm), step):
                    chunk = pcm[i:i + step]
                    if recognizer.AcceptWaveform(chunk):
                        parsed = json.loads(recognizer.Result())
                        t = parsed.get('text', '').strip()
                        if t: text_parts.append(t)
                        
                final_parsed = json.loads(recognizer.FinalResult())
                final_text = final_parsed.get('text', '').strip()
                if final_text:
                    text_parts.append(final_text)
                text = ' '.join(text_parts)

            elif self.backend == 'google':
                import speech_recognition as sr
                pcm = self._float32_to_pcm16(audio_data)
                audio_obj = sr.AudioData(pcm, SAMPLE_RATE, 2)
                text = self.stt._recognizer.recognize_google(
                    audio_obj,
                    language='ru-RU',
                    show_all=False,
                )
        except Exception as e:
            print(f"\n[Ошибка STT] {e}")
            text = ""
        
        text = text.strip()
        if text:
            print(f"\n[👤 ВЫ]: {text}")
            # Запускаем ответ ИИ
            self.mock_ai_response(text)
        else:
            print("\n[Отмена] Текст не распознан.")

    def mock_ai_response(self, text):
        """Эмулирует ответ ИИ (генерация LLM + TTS), который можно перебить."""
        def ai_worker():
            self.is_ai_speaking = True
            self.abort_playback_event.clear()
            print("[🤖 ИИ начинает говорить...]")
            
            # Эмуляция стриминга по словам
            response_words = f"Здравствуйте! Я услышал, что вы сказали: '{text}'. Это очень интересно, но если хотите, вы можете меня перебить в любой момент.".split()
            
            for word in response_words:
                if self.abort_playback_event.is_set():
                    print("\n[🤖 ИИ замолчал из-за прерывания!]")
                    return
                print(f"{word} ", end="", flush=True)
                time.sleep(0.3) # Эмуляция задержки произношения слова
            
            print("\n[🤖 ИИ закончил говорить]")
            self.is_ai_speaking = False

        # Запускаем в фоне, чтобы основной поток мог продолжать слушать микрофон
        threading.Thread(target=ai_worker, daemon=True).start()

if __name__ == "__main__":
    print("=== Тест новой системы Live STT (VAD + Транскрибация) ===")
    print("Выберите бэкенд:")
    print("1. Whisper (OpenAI, поддерживает разные модели)")
    print("2. Vosk (оффлайн, быстрый)")
    print("3. Google (онлайн, требует интернет)")

    while True:
        choice = input("Ваш выбор (1/2/3): ").strip()
        if choice == "1":
            backend = "whisper"
            break
        elif choice == "2":
            backend = "vosk"
            break
        elif choice == "3":
            backend = "google"
            break
        else:
            print("Неверный выбор.")

    model_name = None
    if backend == "whisper":
        model_name = input("Введите имя модели Whisper (по умолчанию 'small'): ").strip() or "small"
    elif backend == "vosk":
        model_name = input("Введите имя модели Vosk (по умолчанию 'vosk-model-small-ru-0.22'): ").strip() or "vosk-model-small-ru-0.22"

    try:
        live_stt = LiveSTT(backend=backend, model_name=model_name)
        live_stt.start_listening()
    except KeyboardInterrupt:
        print("\nВыход.")
