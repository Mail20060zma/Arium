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
from app.tools.text_processing import split_text_to_sentences
from app.tools.instructions import SYSTEM_PROMPT, build_tools_use
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
        self._turn_state_lock = threading.Lock()
        self._ai_request_queue = queue.Queue()
        self._ai_worker_thread: Optional[threading.Thread] = None
        
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
        self.system_prompt = SYSTEM_PROMPT.strip()
        self.tools_definitions = build_tools_use(self.enabled_tools)
        
        logger.info("🔧 Инициализация TTS...")
        self.tts = get_tts_engine(self.settings)
        self.tts.load_model()
        # Опционально: проверить поддержку stream_audio
        if not hasattr(self.tts, 'stream_audio'):
            logger.warning("TTS движок не поддерживает `stream_audio`. Чанкинг может не работать должным образом.")
        
        logger.info("🔧 Инициализация STT...")
        self.stt = create_stt(
            self.settings.get('stt_provider', 'whisper'),
            self.settings.get('stt_model', 'small')
        )

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

    def _interrupt_ai_output(self, source: str = "user_interrupt") -> bool:
        """Немедленно прерывает текущую озвучку и очищает очередь TTS."""
        has_pending_output = self.is_speaking or (not self.tts_sentence_queue.empty())
        if not has_pending_output:
            return False

        self.cancellation_token = True
        self.abort_playback_event.set()
        with self.tts_sentence_queue.mutex:
            self.tts_sentence_queue.queue.clear()

        # Фиксируем только реально озвученный текст, если assistant-сообщение уже создано.
        if self.last_ai_message_id:
            spoken = self.current_ai_text_spoken.strip()
            self.history_manager.update_message(
                self.last_ai_message_id,
                text=spoken,
                spoken_text=spoken,
                interrupted=True,
                finish_reason="cancelled",
                interrupt_source=source,
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
                        last_ptt_state = ptt_pressed

                    now = time.monotonic()
                    to_process = None

                    with state_lock:
                        if ptt_pressed and capture_state == 'idle':
                            if self.is_speaking or not self.tts_sentence_queue.empty():
                                logger.info("🛑 PTT нажат: прерываю текущую озвучку и очищаю очередь")
                                self._interrupt_ai_output(source="ptt_key_down")

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
            return

        logger.info("🎤 Слушаю...")
        for text in self.stt.stream():
            if not self.running:
                break
                
            self._handle_recognized_text(text)

    def _tts_worker(self):
        """Поток проигрывания аудио."""
        stream_out = None
        
        def _get_audio_stream(samplerate):
            nonlocal stream_out
            if stream_out and stream_out.samplerate != samplerate:
                stream_out.close()
                stream_out = None
            if not stream_out:
                stream_out = sd.OutputStream(samplerate=samplerate, channels=1, dtype='float32')
                stream_out.start()
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
                    for chunk in self.tts.stream_audio(sentence, self.abort_playback_event):
                        if self.abort_playback_event.is_set():
                            break
                        out.write(chunk)
                    sentence_completed = not self.abort_playback_event.is_set()
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
                    
            except Exception as e:
                logger.error(f"Ошибка воспроизведения звука: {e}")
                
            # Добавляем в spoken-текст только полностью завершенные предложения.
            if sentence_completed:
                self.current_ai_text_spoken += sentence + " "
            
            if self.abort_playback_event.is_set():
                logger.debug("[TTS] Сброс воспроизведения по флагу (Abort/Interrupt)...")
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
        self.current_ai_text_spoken = ""
        self.current_reasoning_content = ""
        self.last_ai_message_id = None
        with self._turn_state_lock:
            self.current_turn_tool_results = []

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
            tool_choice = "required" if self.tool_only_voice_output else "auto"

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
                    self.history_manager.append_message(
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
            self.history_manager.append_message(
                "assistant",
                None,
                tool_calls=[local_tool_call],
            )

            tool_result = self._tool_text_to_audio(error_public_text)
            self.history_manager.append_message(
                "tool",
                json.dumps(tool_result, ensure_ascii=False),
                tool_call_id=local_tool_call_id,
                name="text_to_audio",
            )
            tool_events_count += 1
            raw_assistant_text = ""

        if single_tts_tool_mode and raw_assistant_text.strip() and tool_events_count == 0:
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
            self.history_manager.append_message(
                "assistant",
                None,
                tool_calls=[local_tool_call],
            )

            tool_result = self._tool_text_to_audio(local_text)
            self.history_manager.append_message(
                "tool",
                json.dumps(tool_result, ensure_ascii=False),
                tool_call_id=local_tool_call_id,
                name="text_to_audio",
            )
            tool_events_count += 1

        # В strict tool-only режиме итог ассистента формируется только из реально озвученного текста.
        self._wait_for_tts_completion(timeout=30.0)
        spoken_text = self.current_ai_text_spoken.strip()
        self.current_reasoning_content = reasoning_buffer

        if self.tool_only_voice_output:
            assistant_content = spoken_text
            if not assistant_content and raw_assistant_text.strip():
                # Fallback на случай, если модель нарушила контракт и не вызвала text_to_audio.
                logger.warning("[LLM] Нет озвученного текста в strict-режиме, сохраняем raw assistant text как fallback")
                assistant_content = raw_assistant_text.strip()
        else:
            assistant_content = spoken_text or raw_assistant_text.strip()

        if finish_reason is None:
            finish_reason = "cancelled" if self.cancellation_token else "stop"

        with self._turn_state_lock:
            tool_snapshot = list(self.current_turn_tool_results)

        # Всегда фиксируем assistant-ход, даже если ответ пустой/прерванный.
        self.last_ai_message_id = self.history_manager.append_message(
            "assistant",
            assistant_content,
            spoken_text=spoken_text,
            reasoning_content=reasoning_buffer,
            finish_reason=finish_reason,
            model=self.settings.get_model_id_for_model(),
            tool_results=tool_snapshot,
            interrupted=bool(self.cancellation_token or self.abort_playback_event.is_set()),
            tool_events_count=tool_events_count,
        )
