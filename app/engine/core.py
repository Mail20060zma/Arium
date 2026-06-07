import time
import json
import logging
import threading
import queue
from collections import deque
import sounddevice as sd
import numpy as np
from typing import Optional, List
from pathlib import Path

from app.utils.settings import Settings
from app.data.history_manager import HistoryManager
from app.engine.event_dispatcher import EventDispatcher
from app.LLM.universal_openai import UniversalOpenAIHandler
from app.tools.text_processing import (
    split_text_to_sentences,
    normalize_words,
    compute_word_timings_from_weight,
)
from app.tools.instructions import get_system_prompt, build_tools_use
from app.tools import get_tool_handlers

from app.STT import create_stt
from app.tts_backends import get_tts_engine

logger = logging.getLogger(__name__)

class AriumEngine:
    """
    Основной оркестратор ядра Arium.
    Связывает STT, LLM, TTS и History Manager посредством событийно-ориентированной архитектуры.
    """
    def __init__(self, settings_path: str):
        self.settings = Settings(settings_path)
        self.running = False
        
        # Состояние системы
        self.is_speaking = False
        self.abort_playback_event = threading.Event()
        self.cancellation_token = False
        
        # Очереди
        self.tts_sentence_queue = queue.Queue()
        
        self.last_ai_message_id: Optional[str] = None
        self.current_ai_text_spoken = ""
        self.current_reasoning_content = ""
        self.current_turn_tool_results = []
        self._turn_tts_tool_results: List[dict] = []
        self._turn_state_lock = threading.Lock()
        self._ai_request_queue = queue.Queue()
        self._ai_worker_thread: Optional[threading.Thread] = None

        # Word-level spoken tracking state
        self._spoken_words: List[str] = []
        self._spoken_words_lock = threading.RLock()
        self._tts_ms_per_weight = float(self.settings.get('tts.word_ms_per_weight_ms', 85.0))
        self._tts_ms_per_weight_alpha = float(self.settings.get('tts.word_ms_per_weight_ema_alpha', 0.2))
        self._tts_ms_per_weight_min = 20.0
        self._tts_ms_per_weight_max = 300.0

        # Word-level spoken tracking state (precise tracking for interrupt)
        self._current_playback_start_time = None
        self._current_word_timings = []
        self._current_word_index = 0

        # TTS output stream handle for immediate abort
        self._tts_stream_lock = threading.Lock()
        self._tts_stream_out: Optional[sd.OutputStream] = None
        
        self._init_components()

    def _init_components(self):
        logger.info("🔧 Инициализация History Manager...")
        
        # Получаем абсолютный или правильный относительный путь для истории
        from pathlib import Path
        base_dir = Path(self.settings._filepath).parent.parent
        history_path = str(base_dir / "data" / "chat_history.json")
        
        self.history_manager = HistoryManager(
            filepath=self.settings.get('memory.history_file', history_path),
            max_context_window=self.settings.get('memory.context_window_size', 25)
        )
        
        logger.info("🔧 Инициализация Event Dispatcher...")
        self.dispatcher = EventDispatcher(
            debounce_ms=self.settings.get('controls.debounce_ms', 200),
            on_trigger=self._on_user_speech_batched
        )
        
        enabled_tools_raw = self.settings.get('llm.enabled_tools', ['text_to_audio'])
        if isinstance(enabled_tools_raw, str):
            self.enabled_tools = [enabled_tools_raw]
        elif isinstance(enabled_tools_raw, list):
            self.enabled_tools = [str(name) for name in enabled_tools_raw if name]
        else:
            self.enabled_tools = ['text_to_audio']

        if not self.enabled_tools:
            self.enabled_tools = ['text_to_audio']

        self.tool_only_voice_output = bool(self.settings.get('llm.tool_only_voice_output', True))
        backend = self.settings.get('tts_backend', 'silero')
        self.system_prompt = get_system_prompt(backend).strip()
        user_prompt_raw = self.settings.get('llm.user_system_prompt', '')
        self.user_system_prompt = (str(user_prompt_raw).strip() if user_prompt_raw is not None else "")
        self.tools_definitions = build_tools_use(self.enabled_tools)
        
        logger.info("🔧 Инициализация TTS...")
        self.tts = get_tts_engine(self.settings)
        self.tts.load_model()
        # Опционально: проверить поддержку stream_audio
        if not hasattr(self.tts, 'stream_audio'):
            logger.warning("TTS движок не поддерживает `stream_audio`. Чанкинг может не работать должным образом.")
        
        logger.info("🔧 Инициализация STT...")
        stt_provider = self.settings.get('stt_provider', 'whisper')
        stt_model = self.settings.get('stt_model', 'small')
        system_device = self.settings.get('system_device', 'cuda' if __import__('torch').cuda.is_available() else 'cpu')
        
        if stt_provider == 'google':
            if stt_model is not None:
                normalized = str(stt_model).strip().lower()
                if normalized not in {"", "none", "null"}:
                    self.settings.set('stt_model', None)
            stt_model = None

        self.stt = create_stt(stt_provider, stt_model, device=system_device)

        logger.info("🔧 Инициализация VAD (Silero)...")
        import torch
        self.device = system_device
        self.vad_model, _ = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            trust_repo=True
        )
        self.vad_model.to(self.device)

        logger.info("🔧 Инициализация Универсального LLM...")
        tool_handlers = get_tool_handlers(
            text_to_audio_handler=self._tool_text_to_audio,
            enabled_tools=self.enabled_tools,
        )
        self.llm = UniversalOpenAIHandler(
            api_key=self.settings.get_api_key_for_model(),
            base_url=self.settings.get_base_url_for_model(),
            model=self.settings.get_model_id_for_model(),
            tool_handlers=tool_handlers
        )
        logger.info(f"🔧 Активные инструменты LLM: {list(tool_handlers.keys())}")
        
        # Настройка PTT
        ptt_mode_raw = self.settings.get('controls.ptt_mode', False)
        if isinstance(ptt_mode_raw, str):
            self.ptt_mode = ptt_mode_raw.strip().lower() in {
                '1', 'true', 'yes', 'on', 'ptt', 'push_to_talk'
            }
        else:
            self.ptt_mode = bool(ptt_mode_raw)
        self.ptt_keys = self.settings.get('controls.ptt_keys', 'alt+space')
        self.ptt_pre_roll_seconds = float(self.settings.get('controls.ptt_pre_roll_seconds', 1.0))
        self.ptt_post_roll_seconds = float(self.settings.get('controls.ptt_post_roll_seconds', 1.0))
        self.ptt_chunk_seconds = float(self.settings.get('controls.ptt_chunk_seconds', 0.1))
        self.ptt_ring_buffer_seconds = float(self.settings.get('controls.ptt_ring_buffer_seconds', 6.0))
        self.ptt_sample_rate = int(self.settings.get('controls.ptt_sample_rate', 16000))

        # Базовая защита от некорректных значений в настройках
        self.ptt_pre_roll_seconds = max(0.0, self.ptt_pre_roll_seconds)
        self.ptt_post_roll_seconds = max(0.0, self.ptt_post_roll_seconds)
        self.ptt_chunk_seconds = max(0.02, self.ptt_chunk_seconds)
        self.ptt_ring_buffer_seconds = max(1.0, self.ptt_ring_buffer_seconds)
        self.ptt_sample_rate = max(8000, self.ptt_sample_rate)

        self.keyboard_module = None
        self._ptt_key_variants = []
        if self.ptt_mode:
            try:
                import keyboard
                self.keyboard_module = keyboard

                ptt_keys_normalized = str(self.ptt_keys).strip().lower()
                if ptt_keys_normalized in {"right alt", "ralt", "altgr", "alt gr"}:
                    # На Windows/keyboard правый Alt может называться по-разному.
                    self._ptt_key_variants = ["right alt", "alt gr", "altgr"]
                elif "|" in str(self.ptt_keys):
                    self._ptt_key_variants = [part.strip() for part in str(self.ptt_keys).split("|") if part.strip()]
                else:
                    self._ptt_key_variants = [str(self.ptt_keys)]

                logger.info(
                    f"🎤 Push-To-Talk включен. Клавиша: {self.ptt_keys}. "
                    f"Окно захвата: -{self.ptt_pre_roll_seconds:.1f}с/+{self.ptt_post_roll_seconds:.1f}с"
                )
            except ImportError:
                logger.warning("Модуль keyboard не установлен. Возврат к голосовой активации.")
                self.ptt_mode = False

    def _cancellation_callback(self) -> bool:
        """Метод для передачи в LLM, определяющий нужно ли прервать стрим."""
        return self.cancellation_token

    def _wait_for_tts_completion(self, timeout: float = 30.0) -> None:
        """Ждет завершения очереди TTS (или до таймаута)."""
        start_time = time.monotonic()
        while self.running:
            if self.tts_sentence_queue.empty() and not self.is_speaking:
                return
            if time.monotonic() - start_time >= timeout:
                logger.warning("[TTS] Таймаут ожидания завершения очереди озвучки")
                return
            if self.cancellation_token or self.abort_playback_event.is_set():
                return
            time.sleep(0.02)

    def _reset_spoken_words_state(self) -> None:
        """Сбрасывает пословный буфер текущего ответа."""
        with self._spoken_words_lock:
            self._spoken_words = []
        self.current_ai_text_spoken = ""
        self._current_playback_start_time = None
        self._current_word_timings = []
        self._current_word_index = 0

    def _append_spoken_words(self, words: List[str]) -> None:
        """Добавляет реально произнесенные слова в историю текущего ответа."""
        if not words:
            return
        with self._spoken_words_lock:
            self._spoken_words.extend(words)
            self.current_ai_text_spoken = " ".join(self._spoken_words)

    def _update_ms_per_weight(self, duration_ms: int, total_weight: int) -> None:
        """Обновляет оценку ms_per_weight на основе фактической длительности."""
        if duration_ms <= 0 or total_weight <= 0:
            return
        target = duration_ms / max(total_weight, 1)
        target = max(self._tts_ms_per_weight_min, min(self._tts_ms_per_weight_max, target))
        alpha = min(max(self._tts_ms_per_weight_alpha, 0.0), 1.0)
        self._tts_ms_per_weight = (alpha * target) + ((1.0 - alpha) * self._tts_ms_per_weight)

    def _stop_tts_output_stream(self) -> None:
        """Немедленно останавливает вывод TTS, чтобы прерывание было мгновенным."""
        with self._tts_stream_lock:
            if self._tts_stream_out is None:
                return
            try:
                self._tts_stream_out.abort()
            except Exception:
                pass
            try:
                self._tts_stream_out.stop()
            except Exception:
                pass
            try:
                self._tts_stream_out.close()
            except Exception:
                pass
            self._tts_stream_out = None

    def _interrupt_ai_output(self, source: str = "user_interrupt") -> bool:
        """Немедленно прерывает текущую озвучку и очищает очередь TTS."""
        has_pending_output = self.is_speaking or (not self.tts_sentence_queue.empty())
        if not has_pending_output:
            return False

        self.cancellation_token = True
        self.abort_playback_event.set()
        
        # Обновляем произнесенные слова прямо в момент прерывания (высокая точность)
        if getattr(self, '_current_playback_start_time', None) is not None:
            elapsed_ms = (time.perf_counter() - self._current_playback_start_time) * 1000.0
            with self._spoken_words_lock:
                while self._current_word_index < len(self._current_word_timings) and elapsed_ms >= self._current_word_timings[self._current_word_index]["start_ms"]:
                    self._spoken_words.append(self._current_word_timings[self._current_word_index]["word"])
                    self._current_word_index += 1
                self.current_ai_text_spoken = " ".join(self._spoken_words)
                
        # self._stop_tts_output_stream() # Убрано: вызов abort() из другого потока крашит PortAudio
        with self.tts_sentence_queue.mutex:
            self.tts_sentence_queue.queue.clear()

        # Фиксируем только реально озвученный текст, если assistant-сообщение уже создано.
        if self.last_ai_message_id:
            spoken = self.current_ai_text_spoken.strip()
            self.history_manager.truncate_message(self.last_ai_message_id, spoken)
            self.history_manager.update_message(
                self.last_ai_message_id,
                finish_reason="cancelled"
            )

        return True

    def _tool_text_to_audio(self, text: str, speaker: str = "kseniya") -> dict:
        """Инструмент озвучки для LLM: ставит текст в очередь TTS.

        В strict tool-only режиме только этот путь может привести к озвучке пользователя.
        """
        clean_text = (text or "").strip()
        if not clean_text:
            return {
                "status": "error",
                "tool": "text_to_audio",
                "error": "Пустой текст для озвучки",
                "text_requested": "",
                "text_spoken": "",
                "interrupted": False,
            }

        sentences = split_text_to_sentences(clean_text)
        if not sentences:
            sentences = [clean_text]

        for sentence in sentences:
            self.tts_sentence_queue.put(sentence)

        with self._turn_state_lock:
            self.current_turn_tool_results.append({
                "tool": "text_to_audio",
                "text_requested": clean_text,
                "speaker": speaker,
                "queued_sentences": len(sentences),
                "timestamp": time.time(),
            })

        return {
            "status": "success",
            "tool": "text_to_audio",
            "text_requested": clean_text,
            "queued_sentences": len(sentences),
            "speaker": speaker,
            "interrupted": False,
        }

    @staticmethod
    def _float32_to_pcm16(audio: np.ndarray) -> bytes:
        """Преобразование float32 [-1..1] в PCM16 для STT backend-ов, которым нужны байты."""
        clipped = np.clip(audio, -1.0, 1.0)
        int16 = (clipped * 32767.0).astype(np.int16)
        return int16.tobytes()

    def _transcribe_audio_segment(self, audio: np.ndarray) -> str:
        """Транскрибация готового аудио-сегмента в зависимости от выбранного STT backend-а."""
        stt_provider = self.settings.get('stt_provider', 'whisper')

        if stt_provider == 'whisper':
            result = self.stt.model.transcribe(
                audio,
                language='ru',
                fp16=getattr(self.stt, 'use_fp16', False),
                temperature=0.0,
            )
            return result.get('text', '').strip()

        if stt_provider == 'vosk':
            from vosk import KaldiRecognizer

            pcm = self._float32_to_pcm16(audio)
            recognizer = KaldiRecognizer(self.stt.model, self.ptt_sample_rate)
            recognizer.SetWords(False)

            step = 4000 * 2  # 4000 samples * 2 bytes
            text_parts = []
            for i in range(0, len(pcm), step):
                chunk = pcm[i:i + step]
                if recognizer.AcceptWaveform(chunk):
                    parsed = json.loads(recognizer.Result())
                    text = parsed.get('text', '').strip()
                    if text:
                        text_parts.append(text)

            final_parsed = json.loads(recognizer.FinalResult())
            final_text = final_parsed.get('text', '').strip()
            if final_text:
                text_parts.append(final_text)

            return ' '.join(text_parts).strip()

        if stt_provider == 'google':
            import speech_recognition as sr

            pcm = self._float32_to_pcm16(audio)
            audio_data = sr.AudioData(pcm, self.ptt_sample_rate, 2)
            try:
                return self.stt._recognizer.recognize_google(
                    audio_data,
                    language='ru-RU',
                    show_all=False,
                ).strip()
            except sr.UnknownValueError:
                return ''
            except sr.RequestError:
                return ''

        raise ValueError(f"Неизвестный STT провайдер для buffered PTT: {stt_provider}")

    def _handle_recognized_text(self, text: str) -> None:
        """Единая точка обработки распознанной фразы (interrupt + dispatch)."""
        text = (text or '').strip()
        if not text:
            return

        logger.info(f"🗣️ Вы сказали: {text}")

        # --- SOFT INTERRUPT LOGIC ---
        if self.is_speaking or not self.tts_sentence_queue.empty():
            logger.debug("Обнаружена речь во время работы ИИ. Анализ перебивания...")
            is_interrupt = self.llm.ping_fast_interrupt(text)

            if is_interrupt:
                logger.info("🛑 Зафиксировано перебивание (Soft Interrupt)!")
                self._interrupt_ai_output(source="voice_interrupt")
            else:
                logger.info("💨 Шум / Угуканье (игнорируем)")
                return

        # Передаем речь на обработку (Debouncing)
        self.dispatcher.dispatch("user_speech", text)

    def _transcriber_worker(self, in_queue: queue.Queue, out_queue: queue.Queue, stop_event: threading.Event):
        """Фоновый воркер транскрибации buffered-PTT сегментов."""
        while self.running and not stop_event.is_set():
            try:
                segment = in_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if segment is None:
                break

            try:
                text = self._transcribe_audio_segment(segment)
                out_queue.put(("text", text))
            except Exception as exc:
                out_queue.put(("error", exc))
            finally:
                in_queue.task_done()

    def _stt_worker_buffered_ptt(self):
        """PTT-режим с непрерывным захватом коротких чанков и отложенной транскрибацией.

        Сегмент формируется как: 1) pre-roll до нажатия, 2) удержание кнопки, 3) post-roll после отпускания.
        """
        logger.info("🎤 Слушаю (Buffered PTT mode)...")

        ring_buffer_max = max(1, int(self.ptt_ring_buffer_seconds / self.ptt_chunk_seconds))
        pre_roll_count = max(0, int(self.ptt_pre_roll_seconds / self.ptt_chunk_seconds))
        blocksize = max(1, int(self.ptt_sample_rate * self.ptt_chunk_seconds))

        ring_buffer = deque(maxlen=ring_buffer_max)
        state_lock = threading.Lock()

        capture_state = 'idle'   # idle | recording | post
        active_chunks = []
        post_deadline = 0.0
        last_ptt_state = None

        segment_queue = queue.Queue(maxsize=10)
        result_queue = queue.Queue(maxsize=10)
        stop_transcriber = threading.Event()

        transcriber = threading.Thread(
            target=self._transcriber_worker,
            args=(segment_queue, result_queue, stop_transcriber),
            daemon=True,
            name="PTT_Transcriber",
        )
        transcriber.start()

        def audio_callback(indata, _frames, _time_info, status):
            nonlocal active_chunks
            if status:
                logger.debug(f"[PTT] Audio callback status: {status}")

            chunk = indata[:, 0].copy().astype(np.float32)
            with state_lock:
                ring_buffer.append(chunk)
                if capture_state in ('recording', 'post'):
                    active_chunks.append(chunk)

        try:
            with sd.InputStream(
                samplerate=self.ptt_sample_rate,
                channels=1,
                dtype='float32',
                blocksize=blocksize,
                callback=audio_callback,
            ):
                while self.running:
                    try:
                        ptt_pressed = any(
                            self.keyboard_module.is_pressed(key_variant)
                            for key_variant in self._ptt_key_variants
                        )
                    except Exception:
                        logger.debug("[PTT] Ошибка чтения состояния горячей клавиши", exc_info=True)
                        ptt_pressed = False

                    if ptt_pressed != last_ptt_state:
                        state_text = "зажата" if ptt_pressed else "отпущена"
                        logger.debug(f"[PTT] Клавиша '{self.ptt_keys}' {state_text}.")

                        if ptt_pressed and not last_ptt_state:
                            if self.is_speaking or not self.tts_sentence_queue.empty():
                                logger.info("🛑 PTT нажат: прерываю текущую озвучку и очищаю очередь")
                                self._interrupt_ai_output(source="ptt_key_down")

                        last_ptt_state = ptt_pressed

                    now = time.monotonic()
                    to_process = None

                    with state_lock:
                        if ptt_pressed and capture_state == 'idle':
                            capture_state = 'recording'
                            active_chunks = list(ring_buffer)[-pre_roll_count:]
                            post_deadline = 0.0
                            logger.debug(f"[PTT] Старт сегмента, pre-roll чанков: {len(active_chunks)}")

                        elif (not ptt_pressed) and capture_state == 'recording':
                            capture_state = 'post'
                            post_deadline = now + self.ptt_post_roll_seconds
                            logger.debug(f"[PTT] Отпускание клавиши, post-roll: {self.ptt_post_roll_seconds:.2f}s")

                        if capture_state == 'post' and now >= post_deadline:
                            capture_state = 'idle'
                            if active_chunks:
                                to_process = np.concatenate(active_chunks)
                            active_chunks = []

                    if to_process is not None and to_process.size > 0:
                        duration = to_process.size / self.ptt_sample_rate
                        logger.debug(f"[PTT] Готов сегмент: {duration:.2f}s, отправка в STT")
                        try:
                            segment_queue.put_nowait(to_process)
                        except queue.Full:
                            logger.warning("[PTT] Очередь сегментов переполнена, сегмент пропущен")

                    while self.running:
                        try:
                            result_type, payload = result_queue.get_nowait()
                        except queue.Empty:
                            break

                        if result_type == 'error':
                            logger.error(f"Ошибка buffered STT: {payload}")
                            continue

                        self._handle_recognized_text(str(payload))

                    time.sleep(0.01)

        except Exception as e:
            logger.error(f"Ошибка buffered PTT режима: {e}", exc_info=True)
        finally:
            stop_transcriber.set()
            try:
                segment_queue.put_nowait(None)
            except queue.Full:
                pass
            transcriber.join(timeout=1.0)

    def start(self):
        """Запуск главных потоков."""
        self.running = True
        self.history_manager.start_worker()

        logger.debug("Запуск потока AI_Worker_Thread...")
        self._ai_worker_thread = threading.Thread(
            target=self._ai_worker,
            daemon=True,
            name="AI_Worker_Thread",
        )
        self._ai_worker_thread.start()
        
        logger.debug("Запуск потока STT_Thread...")
        threading.Thread(target=self._stt_worker, daemon=True, name="STT_Thread").start()
        
        logger.debug("Запуск потока TTS_Thread...")
        threading.Thread(target=self._tts_worker, daemon=True, name="TTS_Thread").start()
        
        logger.info("🚀 Arium Engine успешно запущен!")
        try:
            while self.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            logger.info("Остановка Arium Engine...")
            self.stop()

    def stop(self):
        """Плавная остановка."""
        self.running = False
        self.cancellation_token = True
        self.abort_playback_event.set()
        self._ai_request_queue.put(None)
        if self._ai_worker_thread:
            self._ai_worker_thread.join(timeout=2.0)
        self.history_manager.stop_worker()
        self.dispatcher.cancel()

    # ================= WORKERS =================

    def _ai_worker(self):
        """Последовательный воркер генерации ответов ИИ (без гонок между потоками)."""
        while self.running:
            try:
                request_item = self._ai_request_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if request_item is None:
                self._ai_request_queue.task_done()
                break

            try:
                self._generate_ai_response()
            except Exception as exc:
                logger.error(f"[AI Worker] Ошибка генерации ответа: {exc}", exc_info=True)
            finally:
                self._ai_request_queue.task_done()

    def _stt_worker(self):
        """Поток прослушивания микрофона."""
        if self.ptt_mode and self.keyboard_module:
            self._stt_worker_buffered_ptt()
        else:
            self._stt_worker_live_vad()

    def _process_result_queue(self, result_queue: queue.Queue):
        while True:
            try:
                result_type, payload = result_queue.get_nowait()
                if result_type == 'error':
                    logger.error(f"Ошибка транскрибации: {payload}")
                else:
                    self._handle_recognized_text(str(payload))
            except queue.Empty:
                break

    def _stt_worker_live_vad(self):
        """Режим Continuous STT (Live VAD)."""
        logger.info("🎤 Слушаю (Live VAD mode)...")
        
        chunk_size = 512
        vad_threshold_start = float(self.settings.get('controls.vad_threshold_start', 0.5))
        vad_threshold_end = float(self.settings.get('controls.vad_threshold_end', 0.3))
        endpoint_silence_sec = float(self.settings.get('controls.vad_endpointing_seconds', 1.0))
        max_silence_chunks = int(endpoint_silence_sec * self.ptt_sample_rate / chunk_size)
        min_audio_sec = 0.5
        
        audio_queue = queue.Queue()
        
        def audio_callback(indata, frames, time_info, status):
            if status:
                logger.debug(f"[VAD] Audio status: {status}")
            audio_queue.put(indata.copy())
            
        segment_queue = queue.Queue(maxsize=10)
        result_queue = queue.Queue(maxsize=10)
        stop_transcriber = threading.Event()
        
        transcriber = threading.Thread(
            target=self._transcriber_worker,
            args=(segment_queue, result_queue, stop_transcriber),
            daemon=True,
            name="VAD_Transcriber",
        )
        transcriber.start()

        is_recording = False
        audio_buffer = []
        silence_chunks = 0

        try:
            with sd.InputStream(
                samplerate=self.ptt_sample_rate,
                channels=1,
                dtype='float32',
                blocksize=chunk_size,
                callback=audio_callback
            ):
                while self.running:
                    try:
                        chunk = audio_queue.get(timeout=0.1)
                    except queue.Empty:
                        self._process_result_queue(result_queue)
                        continue
                    
                    import torch
                    audio_tensor = torch.from_numpy(chunk.squeeze()).unsqueeze(0).to(self.device)
                    with torch.no_grad():
                        prob = self.vad_model(audio_tensor, self.ptt_sample_rate).item()

                    if prob > vad_threshold_start:
                        if not is_recording:
                            is_recording = True
                            audio_buffer = [chunk]
                            silence_chunks = 0
                            logger.debug("[VAD] Речь началась")
                            
                            # --- BARGE-IN (Прерывание ИИ) ---
                            if self.is_speaking or not self.tts_sentence_queue.empty():
                                logger.info("🛑 VAD перебивание (Barge-in): прерываю ИИ")
                                self._interrupt_ai_output(source="voice_interrupt")
                        else:
                            audio_buffer.append(chunk)
                            silence_chunks = 0
                    elif is_recording:
                        audio_buffer.append(chunk)
                        if prob < vad_threshold_end:
                            silence_chunks += 1
                            
                        if silence_chunks >= max_silence_chunks:
                            is_recording = False
                            logger.debug("[VAD] Речь закончилась (Endpointing)")
                            
                            audio_data = np.concatenate(audio_buffer, axis=0).squeeze()
                            audio_buffer = []
                            duration = len(audio_data) / self.ptt_sample_rate
                            if duration >= min_audio_sec:
                                try:
                                    segment_queue.put_nowait(audio_data)
                                except queue.Full:
                                    logger.warning("[VAD] Очередь переполнена, пропускаем")
                            else:
                                logger.debug(f"[VAD] Слишком короткая фраза ({duration:.1f}с), игнорируем")
                                
                    self._process_result_queue(result_queue)

        except Exception as e:
            logger.error(f"Ошибка Live VAD режима: {e}", exc_info=True)
        finally:
            stop_transcriber.set()
            try:
                segment_queue.put_nowait(None)
            except queue.Full:
                pass
            transcriber.join(timeout=1.0)

    def _tts_worker(self):
        """Поток проигрывания аудио."""
        stream_out = None
        
        def _get_audio_stream(samplerate):
            nonlocal stream_out
            with self._tts_stream_lock:
                if self._tts_stream_out and self._tts_stream_out.samplerate != samplerate:
                    try:
                        self._tts_stream_out.close()
                    except Exception:
                        pass
                    self._tts_stream_out = None
                if not self._tts_stream_out:
                    self._tts_stream_out = sd.OutputStream(
                        samplerate=samplerate,
                        channels=1,
                        dtype='float32',
                    )
                    self._tts_stream_out.start()
                stream_out = self._tts_stream_out
            return stream_out

        while self.running:
            try:
                sentence = self.tts_sentence_queue.get(timeout=0.5)
            except queue.Empty:
                continue
                
            if not sentence:
                logger.debug("[TTS] Пустое предложение, пропускаем")
                continue
                
            self.is_speaking = True
            logger.info(f"🔊 Начинаем синтез: {sentence}")
            sentence_completed = False
            
            try:
                if hasattr(self.tts, 'stream_audio'):
                    out = _get_audio_stream(self.tts.sample_rate)
                    words = normalize_words(sentence)
                    
                    with self._spoken_words_lock:
                        self._current_word_timings = compute_word_timings_from_weight(words, self._tts_ms_per_weight)
                        self._current_word_index = 0
                        self._current_playback_start_time = None
                    
                    total_samples = 0
                    total_weight = sum(max(len(word), 1) for word in words)

                    for chunk in self.tts.stream_audio(sentence, self.abort_playback_event):
                        if self.abort_playback_event.is_set():
                            break
                        if chunk is None:
                            continue
                        
                        if self._current_playback_start_time is None:
                            self._current_playback_start_time = time.perf_counter()
                            
                        try:
                            out.write(chunk)
                        except Exception:
                            # Stream was aborted
                            pass

                        if words:
                            chunk_arr = np.asarray(chunk, dtype=np.float32)
                            if chunk_arr.ndim > 1:
                                chunk_arr = chunk_arr.reshape(-1)
                            total_samples += chunk_arr.size
                            
                            elapsed_ms = (time.perf_counter() - self._current_playback_start_time) * 1000.0

                            with self._spoken_words_lock:
                                while self._current_word_index < len(self._current_word_timings) and elapsed_ms >= self._current_word_timings[self._current_word_index]["start_ms"]:
                                    self._append_spoken_words([self._current_word_timings[self._current_word_index]["word"]])
                                    self._current_word_index += 1

                    sentence_completed = not self.abort_playback_event.is_set()
                    if sentence_completed and words:
                        if self._current_word_index < len(words):
                            self._append_spoken_words(words[self._current_word_index:])
                        if total_samples > 0 and total_weight > 0:
                            duration_ms = int(round((total_samples / self.tts.sample_rate) * 1000.0))
                            self._update_ms_per_weight(duration_ms, total_weight)
                else:
                    # Фолбэк для TTS движков без stream_audio
                    file_path = self.tts.synthesize_to_file(sentence, str(Path(self.settings._filepath).parent.parent / "audio_output" / "temp.wav"))
                    import pygame
                    if not pygame.mixer.get_init():
                        pygame.mixer.init()
                    snd = pygame.mixer.Sound(file_path)
                    snd.play()
                    while pygame.mixer.get_busy() and not self.abort_playback_event.is_set():
                        time.sleep(0.05)
                    if self.abort_playback_event.is_set():
                        pygame.mixer.stop()
                    sentence_completed = not self.abort_playback_event.is_set()
                    if sentence_completed:
                        self._append_spoken_words(normalize_words(sentence))
                    
            except Exception as e:
                logger.error(f"Ошибка воспроизведения звука: {e}")
            
            # Добавляем в spoken-текст только реально произнесенные слова (по факту).
            
            if self.abort_playback_event.is_set():
                logger.debug("[TTS] Сброс воспроизведения по флагу (Abort/Interrupt)...")
                self._stop_tts_output_stream()
                # Сброс флагов после полной остановки
                self.is_speaking = False
                # Очистка очереди (выкидываем оставшиеся предложения)
                with self.tts_sentence_queue.mutex:
                    self.tts_sentence_queue.queue.clear()
            
            # Проверяем не закончилась ли очередь (тогда выключаем флаг)
            if self.tts_sentence_queue.empty():
                logger.debug("[TTS] Очередь предложений пуста, флаг is_speaking снят.")
                self.is_speaking = False

    # ================= LOGIC =================

    def _on_user_speech_batched(self, events: List[dict]):
        """Вызывается EventDispatcher после окончания буферизации/Debouncing речи."""
        combined_text = " ".join([e["data"] for e in events if e["data"]])
        combined_text = combined_text.strip()
        if not combined_text:
            return
        
        # Добавляем наш запрос в базу
        self.history_manager.append_message("user", combined_text)
        self.history_manager.wait_until_idle(timeout=1.0)

        logger.debug("[Dispatcher] Сообщение пользователя поставлено в очередь AI-воркера")
        self._ai_request_queue.put({"text": combined_text, "timestamp": time.time()})

    def _generate_ai_response(self):
        """Работа с Universal LLM и стримингом (strict tool-first voice)."""
        # Подготовка состояния хода (делаем только в одном AI-воркере, поэтому гонок нет).
        logger.debug("[AI] Старт новой генерации, сброс turn-state")
        self.cancellation_token = False
        self.abort_playback_event.clear()
        self._reset_spoken_words_state()
        self.current_reasoning_content = ""
        self.last_ai_message_id = None
        with self._turn_state_lock:
            self.current_turn_tool_results = []
            self._turn_tts_tool_results = []

        # Гарантируем, что все события истории (включая user) уже применены.
        self.history_manager.wait_until_idle(timeout=2.0)

        # Подготовка контекста
        context_limit = self.settings.get('memory.context_window_size', 25)
        include_reasoning = bool(self.settings.get('memory.include_reasoning_in_context', True))
        reasoning_max_chars = int(self.settings.get('memory.reasoning_max_chars', 4000))
        raw_history = self.history_manager.get_context_window(
            limit=context_limit,
            include_reasoning=include_reasoning,
            reasoning_max_chars=reasoning_max_chars,
        )

        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        if self.user_system_prompt:
            messages.append({
                "role": "system",
                "content": (
                    "Дополнительные предпочтения пользователя. "
                    "Не отменяют базовые правила безопасности и контракта ответа.\n"
                    f"{self.user_system_prompt}"
                ),
            })
        messages.extend(raw_history)

        # Когда доступен только text_to_audio, многие небольшие модели плохо справляются
        # с forced function-calling и склонны к шаблонным ответам. В этом режиме получаем
        # обычный текстовый ответ и уже локально отправляем его в text_to_audio.
        single_tts_tool_mode = self.tool_only_voice_output and set(self.enabled_tools) == {"text_to_audio"}
        if single_tts_tool_mode:
            messages.insert(
                1,
                {
                    "role": "system",
                    "content": (
                        "Технический режим: не вызывай инструменты. "
                        "Сформируй только финальный краткий ответ пользователю обычным текстом."
                    ),
                },
            )

        raw_assistant_text = ""
        reasoning_buffer = ""
        finish_reason = None
        tool_events_count = 0
        had_error = False
        error_details = ""
        error_public_text = "Извините, сейчас возникла проблема с подключением к ИИ. Попробуйте еще раз."
        
        # Получаем стрим от LLM
        logger.debug(f"[LLM] Вызов стрим-ответчика ({self.settings.get_model_id_for_model()})")
        tool_choice = None
        tools_definitions = self.tools_definitions
        if single_tts_tool_mode:
            tools_definitions = None
        elif self.tools_definitions:
            tool_choice = "auto" # Используем auto вместо required, чтобы избежать ошибок API на некоторых моделях

        stream = self.llm.send_message_stream(
            messages=messages,
            tools_definitions=tools_definitions,
            tool_choice=tool_choice,
            cancellation_token=self._cancellation_callback
        )
        
        for chunk in stream:
            chunk_type = chunk.get("type")

            if chunk_type == "status" and chunk.get("finish_reason") == "error":
                had_error = True
                finish_reason = "error"
                details = chunk.get("error") or chunk.get("content") or ""
                if details:
                    error_details = str(details)
                continue

            if chunk_type == "reasoning_delta":
                reasoning_delta = chunk.get("reasoning_content", "")
                if reasoning_delta:
                    reasoning_buffer += reasoning_delta
                continue

            if chunk_type == "tool_result":
                tool_name = chunk.get("tool_name", "")
                tool_call_id = chunk.get("tool_call_id", "")
                raw_result = chunk.get("result", {})
                if isinstance(raw_result, dict):
                    tool_result = dict(raw_result)
                else:
                    tool_result = {"value": str(raw_result)}

                if tool_name == "text_to_audio":
                    tool_result["interrupted"] = bool(self.cancellation_token or self.abort_playback_event.is_set())
                    with self._turn_state_lock:
                        self._turn_tts_tool_results.append({
                            "tool_call_id": tool_call_id,
                            "result": tool_result,
                            "success": chunk.get("success", False),
                        })
                    self.history_manager.append_message(
                        "tool",
                        str(tool_result.get("text_requested", "")),
                        tool_call_id=tool_call_id,
                        name="text_to_audio",
                    )
                    tool_events_count += 1
                    continue

                self.history_manager.append_message(
                    "tool",
                    json.dumps(tool_result, ensure_ascii=False),
                    tool_call_id=tool_call_id,
                    name=tool_name,
                )
                tool_events_count += 1
                continue

            if chunk_type == "assistant_tool_calls":
                if single_tts_tool_mode:
                    # В single_tts_tool_mode не ожидаем tool_calls от модели.
                    continue

                tool_calls = chunk.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    self.last_ai_message_id = self.history_manager.append_message(
                        "assistant",
                        None,
                        tool_calls=tool_calls,
                    )
                continue

            if self.cancellation_token:
                logger.info("[LLM] Полная остановка стрима из-за внешнего флага CancellationToken.")
                break
                
            if chunk.get("finish_reason") == "cancelled":
                logger.info("[LLM] Генерация официально прервана сервером или обработчиком.")
                finish_reason = "cancelled"
                break
                
            content = chunk.get("content", "")
            if content:
                raw_assistant_text += content

            if chunk.get("finish_reason"):
                finish_reason = chunk.get("finish_reason")

        if had_error and tool_events_count == 0:
            if error_details:
                logger.warning(f"[LLM] Ошибка генерации: {error_details}")

            local_tool_call_id = f"local_tts_error_{int(time.time() * 1000)}"
            local_tool_call = {
                "id": local_tool_call_id,
                "type": "function",
                "function": {
                    "name": "text_to_audio",
                    "arguments": json.dumps({"text": error_public_text}, ensure_ascii=False),
                },
            }

            # Каноничная пара assistant(tool_calls) -> tool(tool_call_id)
            self.last_ai_message_id = self.history_manager.append_message(
                "assistant",
                None,
                tool_calls=[local_tool_call],
            )

            tool_result = self._tool_text_to_audio(error_public_text)
            self.history_manager.append_message(
                "tool",
                error_public_text,
                tool_call_id=local_tool_call_id,
                name="text_to_audio",
            )
            tool_events_count += 1
            raw_assistant_text = ""

        # Fallback: Если модель (особенно локальная) не смогла вызвать инструмент
        # и ответила обычным текстом, принудительно озвучиваем её ответ.
        if self.tool_only_voice_output and raw_assistant_text.strip() and tool_events_count == 0:
            from app.tools.text_processing import extract_clean_text
            local_text = extract_clean_text(raw_assistant_text.strip())
            if not local_text:
                local_text = raw_assistant_text.strip()
            local_tool_call_id = f"local_tts_{int(time.time() * 1000)}"
            local_tool_call = {
                "id": local_tool_call_id,
                "type": "function",
                "function": {
                    "name": "text_to_audio",
                    "arguments": json.dumps({"text": local_text}, ensure_ascii=False),
                },
            }

            # Каноничная пара assistant(tool_calls) -> tool(tool_call_id)
            self.last_ai_message_id = self.history_manager.append_message(
                "assistant",
                None,
                tool_calls=[local_tool_call],
            )

            tool_result = self._tool_text_to_audio(local_text)
            self.history_manager.append_message(
                "tool",
                local_text,
                tool_call_id=local_tool_call_id,
                name="text_to_audio",
            )
            tool_events_count += 1

        # В strict tool-only режиме итог ассистента формируется только из реально сгенерированного текста
        self._wait_for_tts_completion(timeout=30.0)
        self.current_reasoning_content = reasoning_buffer

        assistant_content = raw_assistant_text.strip()

        if finish_reason is None:
            finish_reason = "cancelled" if self.cancellation_token else "stop"

        # Всегда фиксируем assistant-ход, убираем нестандартные поля.
        if self.last_ai_message_id:
            self.history_manager.update_message(
                self.last_ai_message_id,
                text=assistant_content,
                reasoning_content=reasoning_buffer,
                finish_reason=finish_reason,
            )
        else:
            self.last_ai_message_id = self.history_manager.append_message(
                "assistant",
                assistant_content,
                reasoning_content=reasoning_buffer,
            )
