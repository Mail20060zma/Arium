import time
import logging
import threading
import queue
import sounddevice as sd
from typing import Optional, List

from app.utils.settings import Settings
from app.data.history_manager import HistoryManager
from app.engine.event_dispatcher import EventDispatcher
from app.LLM.universal_openai import UniversalOpenAIHandler
from app.tools.text_processing import split_text_to_sentences

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
        
        logger.info("🔧 Инициализация Универсального LLM...")
        # Улучшенный сбор тулсов можно будет встроить позже, пока заглушка {}
        self.llm = UniversalOpenAIHandler(
            api_key=self.settings.get_api_key_for_model(),
            base_url=self.settings.get_base_url_for_model(),
            model=self.settings.get_model_id_for_model(),
            tool_handlers={} 
        )
        
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
        
        # Настройка PTT
        self.ptt_mode = self.settings.get('controls.ptt_mode', False)
        self.ptt_keys = self.settings.get('controls.ptt_keys', 'alt+space')
        self.keyboard_module = None
        if self.ptt_mode:
            try:
                import keyboard
                self.keyboard_module = keyboard
                logger.info(f"🎤 Push-To-Talk включен. Горячая клавиша: {self.ptt_keys}")
            except ImportError:
                logger.warning("Модуль keyboard не установлен. Возврат к голосовой активации.")
                self.ptt_mode = False

    def _cancellation_callback(self) -> bool:
        """Метод для передачи в LLM, определяющий нужно ли прервать стрим."""
        return self.cancellation_token

    def start(self):
        """Запуск главных потоков."""
        self.running = True
        self.history_manager.start_worker()
        
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
        self.history_manager.stop_worker()
        self.dispatcher.cancel()

    # ================= WORKERS =================

    def _stt_worker(self):
        """Поток прослушивания микрофона."""
        logger.info("🎤 Слушаю...")
        for text in self.stt.stream():
            if not self.running:
                break
                
            text = text.strip()
            if not text:
                continue
                
            # Проверка Push-to-Talk
            if self.ptt_mode and self.keyboard_module:
                is_pressed = self.keyboard_module.is_pressed(self.ptt_keys)
                if not is_pressed:
                    logger.debug(f"[PTT] Пропуск речи ({text}), так как '{self.ptt_keys}' не зажата.")
                    continue  # Игнорируем речь, если кнопка не нажата
                else:
                    logger.debug(f"[PTT] Кнопка '{self.ptt_keys}' зажата. Обработка речи: {text}")
                    
            logger.info(f"🗣️ Вы сказали: {text}")
            
            # --- SOFT INTERRUPT LOGIC ---
            if self.is_speaking:
                logger.debug("Обнаружена речь во время работы ИИ. Анализ перебивания...")
                is_interrupt = self.llm.ping_fast_interrupt(text)
                
                if is_interrupt:
                    logger.info("🛑 Зафиксировано перебивание (Soft Interrupt)!")
                    self.cancellation_token = True       # Останавливаем генерацию LLM
                    self.abort_playback_event.set()      # Останавливаем динамик
                    
                    # Обрезаем историю в базе до того, что ИИ реально успел сказать
                    if self.last_ai_message_id:
                        self.history_manager.truncate_message(self.last_ai_message_id, self.current_ai_text_spoken)
                        self.last_ai_message_id = None
                else:
                    logger.info("💨 Шум / Угуканье (игнорируем)")
                    continue
            
            # Передаем речь на обработку (Debouncing)
            self.dispatcher.dispatch("user_speech", text)

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
            
            try:
                if hasattr(self.tts, 'stream_audio'):
                    out = _get_audio_stream(self.tts.sample_rate)
                    for chunk in self.tts.stream_audio(sentence, self.abort_playback_event):
                        if self.abort_playback_event.is_set():
                            break
                        out.write(chunk)
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
                    
            except Exception as e:
                logger.error(f"Ошибка воспроизведения звука: {e}")
                
            # Добавим предложение в память о том, что ИИ уже сказал вслух
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
        
        # Добавляем наш запрос в базу
        self.history_manager.append_message("user", combined_text)
        
        # Сброс сигналов прерывания
        logger.debug("[Dispatcher] Сигналы прерывания сброшены, запуск LLM-генерации...")
        self.cancellation_token = False
        self.abort_playback_event.clear()
        self.current_ai_text_spoken = ""
        
        # Запускаем в отдельном потоке, чтобы не блокировать диспетчер
        threading.Thread(target=self._generate_ai_response, daemon=True, name="AI_Response_Thread").start()

    def _generate_ai_response(self):
        """Работа с Universal LLM и стримингом."""
        # Подготовка контекста
        context_limit = self.settings.get('memory.context_window_size', 25)
        raw_history = self.history_manager.get_context_window(limit=context_limit)
        
        # В будущем здесь добавится SYSTEM PROMPT
        messages = [{"role": msg["role"], "content": msg["content"]} for msg in raw_history]
        
        print("\nАссистент: ", end="", flush=True)
        
        buffer = ""
        last_sentence_idx = 0
        ai_msg_id = None
        
        # Получаем стрим от LLM
        logger.debug(f"[LLM] Вызов стрим-ответчика ({self.settings.get_model_id_for_model()})")
        stream = self.llm.send_message_stream(
            messages=messages,
            cancellation_token=self._cancellation_callback
        )
        
        for chunk in stream:
            if self.cancellation_token:
                logger.info("[LLM] Полная остановка стрима из-за внешнего флага CancellationToken.")
                break
                
            if chunk.get("finish_reason") == "cancelled":
                print("\n[Генерация прервана]\n")
                logger.info("[LLM] Генерация официально прервана сервером или обработчиком.")
                break
                
            content = chunk.get("content", "")
            if content:
                print(content, end="", flush=True)
                buffer += content
                
                # В реальном времени сохраняем текущий буфер в БД
                if ai_msg_id is None:
                    # Создаем запись как только пошли первые символы
                    ai_msg_id = self.history_manager.append_message("assistant", buffer)
                    self.last_ai_message_id = ai_msg_id
                else:
                    self.history_manager.update_message(ai_msg_id, buffer)

                # Проверка на окончание предложений
                sentences = split_text_to_sentences(buffer)
                # Если у нас есть закрытые предложения, которые мы еще не отправили на TTS
                if len(sentences) > last_sentence_idx:
                    # Последнее предложение может быть не дописано (нет точки), так что отправляем всё кроме последнего
                    for i in range(last_sentence_idx, len(sentences) - 1):
                        logger.debug(f"[LLM->TTS] Закусывание в очередь: {sentences[i]}")
                        self.tts_sentence_queue.put(sentences[i])
                    last_sentence_idx = len(sentences) - 1
                    
        # Отправляем самый остаток при завершении стрима
        logger.debug("[LLM] Стрим завершён, выталкиваем остатки в TTS...")
        sentences = split_text_to_sentences(buffer)
        if len(sentences) > last_sentence_idx:
            for i in range(last_sentence_idx, len(sentences)):
                logger.debug(f"[LLM->TTS] Финальный пуш в очередь: {sentences[i]}")
                self.tts_sentence_queue.put(sentences[i])
                
        print() # Newline после завершения генерации
