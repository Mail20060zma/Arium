#!/usr/bin/env python3
"""
Airi 2.0 - Speech Processing System
Три потока: Запись > ИИ > Воспроизведение (с разбиением на предложения)
"""
import os
import sys
import json
import logging
import threading
import time
import queue
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
import tempfile
import numpy as np
from scipy.io import wavfile

# Добавляем путь к app модулям
sys.path.insert(0, str(Path(__file__).parent))

from STT import create_stt
from TTS.silero import SileroTTS
from LLM.openrouter_handler import OpenRouterHandler
from utils.settings import Settings
from tools.text_processing import split_text_to_sentences, prepare_messages_for_api
from tools.instructions import SYSTEM_PROMPT
from tools.tool_executor import ToolExecutor, extract_text_for_tts
from tools import audio as audio_tools

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(Path(__file__).parent / 'logs' / 'app.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Создаем папки
Path(__file__).parent.joinpath('logs').mkdir(exist_ok=True)
Path(__file__).parent.joinpath('audio_output').mkdir(exist_ok=True)

# ============ ГЛОБАЛЬНОЕ СОСТОЯНИЕ ============
class AppState:
    def __init__(self):
        self.running = True
        self.recording = False
        self.processing = False
        self.speaking = False
        self.playback_stop_event = threading.Event()  # Для прерывания воспроизведения
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.history: List[Dict[str, Any]] = []

app_state = AppState()

# Очереди между потоками
voice_input_queue = queue.Queue(maxsize=10)
ai_response_queue = queue.Queue(maxsize=5)
state_lock = threading.Lock()

# Глобальные обработчики
settings: Optional[Settings] = None
llm_handler: Optional[OpenRouterHandler] = None
tts_model: Optional[SileroTTS] = None
stt_backend = None
tool_executor: Optional[ToolExecutor] = None


# ============ ИНИЦИАЛИЗАЦИЯ ============
def initialize_settings() -> bool:
    """Загружает настройки из файла."""
    global settings
    try:
        settings = Settings(str(Path(__file__).parent / 'utils' / 'settings.json'))
        logger.info(f"✅ Настройки загружены: {settings}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки настроек: {e}")
        return False


def initialize_llm(tool_handlers: Dict[str, callable]) -> bool:
    """Инициализирует LLM обработчик и tool executor."""
    global llm_handler, tool_executor, settings
    try:
        api_key = settings.get_api_key_for_model()
        model_id = settings.get_model_id_for_model()
        
        if not api_key:
            logger.error("❌ API ключ не найден в settings.json или переменной окружения OPENROUTER_API_KEY")
            return False
        
        # Инициализируем LLM
        llm_handler = OpenRouterHandler(
            api_key=api_key,
            model=model_id,
            tool_handlers=tool_handlers
        )
        
        # Инициализируем Tool Executor
        tool_executor = ToolExecutor(tool_handlers)
        
        logger.info(f"✅ LLM инициализирован: {model_id}")
        logger.info(f"✅ Tool Executor инициализирован с функциями: {list(tool_handlers.keys())}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации LLM: {e}")
        return False


def initialize_tts() -> bool:
    """Инициализирует TTS."""
    global tts_model, settings
    try:
        speaker = settings.get('tts_speaker', 'kseniya')
        model_id = settings.get('tts_silero_model', 'v5_ru')
        
        tts_model = SileroTTS(
            language='ru',
            model_id=model_id,
            speaker=speaker,
            device='cuda' if __import__('torch').cuda.is_available() else 'cpu'
        )
        logger.info(f"✅ TTS инициализирован: {model_id}, спикер: {speaker}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации TTS: {e}")
        return False


def initialize_stt() -> bool:
    """Инициализирует STT."""
    global stt_backend, settings
    try:
        stt_provider = settings.get('stt_provider', 'whisper')
        stt_model = settings.get('stt_model', 'small')
        
        stt_backend = create_stt(stt_provider, stt_model)
        logger.info(f"✅ STT инициализирован: {stt_provider}/{stt_model}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации STT: {e}")
        return False


# ============ TOOL HANDLERS (Обработчики функций) ============
def get_tool_handlers() -> Dict[str, callable]:
    """Возвращает словарь обработчиков функций для LLM."""
    
    def search_web(query: str) -> Dict[str, Any]:
        """Поиск в интернете."""
        logger.info(f"🔍 Поиск: {query}")
        return {
            "status": "success",
            "query": query,
            "results": [
                {"title": "Результат 1", "url": "https://example.com/1"},
                {"title": "Результат 2", "url": "https://example.com/2"}
            ]
        }
    
    def get_weather(location: str) -> Dict[str, Any]:
        """Получить погоду для города."""
        logger.info(f"🌤️ Погода для: {location}")
        return {
            "status": "success",
            "location": location,
            "temperature": 20,
            "condition": "Облачно",
            "humidity": 65
        }
    
    def get_current_time() -> Dict[str, Any]:
        """Получить текущее время."""
        logger.info("⏰ Запрос текущего времени")
        return {
            "status": "success",
            "time": datetime.now().isoformat(),
            "timezone": "UTC+3"
        }
    
    return {
        "search_web": search_web,
        "get_weather": get_weather,
        "get_current_time": get_current_time,
        "text_to_audio": audio_tools.text_to_audio
    }


# ============ ИСТОРИЯ ============
def load_history_from_file(history_file: Path) -> List[Dict[str, Any]]:
    """Загружает историю из файла."""
    try:
        if history_file.exists():
            with open(history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('messages', [])
    except Exception as e:
        logger.warning(f"⚠️ Не удалось загрузить историю: {e}")
    return []


def save_history_to_file(history_file: Path, messages: List[Dict[str, Any]]) -> None:
    """Сохраняет историю в файл."""
    try:
        history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump({
                "session_id": app_state.session_id,
                "messages": messages,
                "last_updated": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения истории: {e}")


# ============ ПОТОКИ ============
def thread_record_voice():
    """Поток 1: Запись голоса и распознавание STT."""
    logger.info(f"🎤 Запуск потока записи голоса ({settings.get('stt_provider')}/{settings.get('stt_model')})...")
    
    while app_state.running:
        try:
            with state_lock:
                app_state.recording = True
            
            # Используем генератор STT
            for recognized_text in stt_backend.stream():
                if not app_state.running:
                    break
                
                # Обнаруживаем речь - прерываем воспроизведение
                if recognized_text and recognized_text.strip():
                    logger.info(f"🔤 Распознано: {recognized_text}")
                    
                    # Если идет воспроизведение - прерываем его
                    if app_state.speaking:
                        logger.info("⏸️ Прерывание воспроизведения (обнаружена речь)")
                        app_state.playback_stop_event.set()
                    
                    try:
                        voice_input_queue.put_nowait(recognized_text)
                    except queue.Full:
                        logger.warning("⚠️ Очередь входа переполнена, пропускаем")
        
        except Exception as e:
            logger.error(f"❌ Ошибка в потоке записи: {e}")
            time.sleep(1)
        finally:
            with state_lock:
                app_state.recording = False


def thread_process_ai(history_file: Path):
    """Поток 2: Обработка ИИ, выполнение tool_calls, переотправка результатов."""
    logger.info("🤖 Запуск потока обработки ИИ...")
    
    # Загружаем историю
    app_state.history = load_history_from_file(history_file)
    logger.info(f"✅ История загружена ({len(app_state.history)} сообщений)")
    
    while app_state.running:
        try:
            # Получаем голос из очереди
            try:
                user_input = voice_input_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            
            with state_lock:
                app_state.processing = True
            
            logger.info(f"📝 Добавляем в историю: {user_input}")
            
            # Добавляем в историю
            user_message = {
                "role": "user",
                "content": user_input,
                "timestamp": datetime.now().isoformat()
            }
            app_state.history.append(user_message)
            save_history_to_file(history_file, app_state.history)
            
            # ============ ОСНОВНОЙ ЦИКЛ ИИ С TOOL CALLS ============
            api_messages = prepare_messages_for_api(app_state.history, SYSTEM_PROMPT)
            iteration = 0
            max_iterations = 5
            
            while iteration < max_iterations:
                iteration += 1
                logger.info(f"📤 Итерация {iteration}: Отправка запроса к ИИ...")
                
                # Отправляем запрос
                response = llm_handler.send_message(api_messages)
                
                # Обработка ошибок
                if response.get("error"):
                    logger.error(f"❌ Ошибка ИИ: {response.get('content')}")
                    error_msg = {
                        "role": "error",
                        "content": response.get('content'),
                        "timestamp": datetime.now().isoformat()
                    }
                    app_state.history.append(error_msg)
                    save_history_to_file(history_file, app_state.history)
                    break
                
                # Обработка успешного ответа
                response_text = response.get('content', '')
                tool_calls = response.get('tool_calls', [])
                
                # Сохраняем ответ ИИ
                ai_message = {
                    "role": "assistant",
                    "content": response_text,
                    "timestamp": datetime.now().isoformat()
                }
                
                # ============ ОБРАБОТКА TOOL CALLS ============
                if tool_calls:
                    logger.info(f"🔧 Обнаружены функции: {[tc['function']['name'] for tc in tool_calls]}")
                    ai_message['tool_calls'] = tool_calls
                    
                    # Разделяем функции
                    text_to_audio_calls = [tc for tc in tool_calls if tc.get('function', {}).get('name') == 'text_to_audio']
                    other_calls = [tc for tc in tool_calls if tc.get('function', {}).get('name') != 'text_to_audio']
                    
                    # -------- ОЗВУЧКА --------
                    if text_to_audio_calls:
                        logger.info(f"🔊 Выполняем озвучку ({len(text_to_audio_calls)} функций)...")
                        tts_results = tool_executor.execute_tool_calls_parallel(text_to_audio_calls)
                        
                        # Добавляем результаты озвучки в очередь воспроизведения
                        for tool_id, result in tts_results.items():
                            if result.get('status') == 'success':
                                audio_file = result.get('result', {}).get('audio_file')
                                if audio_file:
                                    logger.info(f"🔊 Добавляем в очередь воспроизведения: {audio_file}")
                                    try:
                                        # Передаем информацию о tool_call_id и аудиофайле
                                        ai_response_queue.put_nowait({
                                            'type': 'audio',
                                            'audio_file': audio_file,
                                            'tool_call_id': tool_id,
                                            'text': response_text
                                        })
                                    except queue.Full:
                                        logger.warning("⚠️ Очередь вывода переполнена")
                            else:
                                logger.error(f"❌ Ошибка озвучки: {result.get('error')}")
                    
                    # -------- ОБРАБОТКА ДРУГИХ ФУНКЦИЙ --------
                    if other_calls:
                        logger.info(f"⚙️ Выполняем функции ({len(other_calls)} шт)...")
                        
                        # Добавляем в историю ответ с tool calls
                        app_state.history.append(ai_message)
                        save_history_to_file(history_file, app_state.history)
                        
                        # Выполняем функции
                        func_results = tool_executor.execute_tool_calls_parallel(other_calls)
                        
                        # Добавляем assistant сообщение с tool_calls в API messages
                        api_messages.append({
                            "role": "assistant",
                            "content": response_text,
                            "tool_calls": [
                                {
                                    "id": tc["id"],
                                    "type": "function",
                                    "function": {
                                        "name": tc["function"]["name"],
                                        "arguments": tc["function"]["arguments"]
                                    }
                                }
                                for tc in tool_calls
                            ]
                        })
                        
                        # Добавляем результаты в API messages
                        for tool_id, result in func_results.items():
                            api_messages.append({
                                "role": "tool",
                                "tool_call_id": tool_id,
                                "content": json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
                            })
                            
                            status = result.get('status', 'unknown')
                            func_name = result.get('function', 'unknown')
                            logger.info(f"  ✅ {func_name}: {status}")
                        
                        # Продолжаем цикл - отправляем результаты обратно ИИ
                        logger.info("📤 Отправляем результаты функций обратно в ИИ для финального ответа...")
                        continue
                    
                    else:
                        # Только озвучка - выходим из цикла
                        app_state.history.append(ai_message)
                        save_history_to_file(history_file, app_state.history)
                        break
                
                else:
                    # ============ БЕЗ TOOL CALLS ============
                    # Молчим - не озвучиваем
                    logger.info("🤐 ИИ не вызывала функции - молчим")
                    app_state.history.append(ai_message)
                    save_history_to_file(history_file, app_state.history)
                    break
        
        except Exception as e:
            logger.error(f"❌ Ошибка в потоке ИИ: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(1)
        finally:
            with state_lock:
                app_state.processing = False


def thread_play_response():
    """Поток 3: Воспроизведение аудио или TTS с разбиением на предложения и отслеживанием прерываний."""
    logger.info("🔊 Запуск потока воспроизведения...")
    
    pygame_available = False
    try:
        import pygame
        pygame.mixer.init(frequency=48000, size=-16, channels=1, buffer=512)
        pygame_available = True
        logger.info("✅ pygame.mixer инициализирован")
    except Exception as e:
        logger.warning(f"⚠️ pygame.mixer недоступен: {e}")
    
    history_file = Path(__file__).parent / 'data' / 'chat_history.json'
    
    def play_audio_file(filepath: str) -> bool:
        """Воспроизводит аудиофайл с несколькими методами fallback."""
        if not pygame_available:
            logger.warning("⚠️ pygame недоступен, пропускаем воспроизведение")
            return False
        
        try:
            sound = pygame.mixer.Sound(filepath)
            sound.play()
            logger.info(f"🔊 Воспроизведение: {Path(filepath).name} ({sound.get_length():.2f}s)")
            
            # Ждем окончания с проверкой на прерывание
            while pygame.mixer.get_busy():
                if app_state.playback_stop_event.is_set():
                    logger.info("⏸️ Воспроизведение прервано")
                    pygame.mixer.stop()
                    return False
                time.sleep(0.1)
            
            return True
        
        except pygame.error as e:
            logger.warning(f"⚠️ pygame ошибка: {e}")
            # Fallback: пробуем встроенные средства Windows/Linux
            try:
                import subprocess
                import platform
                
                if platform.system() == 'Windows':
                    import winsound
                    winsound.PlaySound(filepath, winsound.SND_FILENAME)
                    logger.info(f"🔊 Воспроизведение (winsound): {Path(filepath).name}")
                    return True
                else:
                    # Linux/Mac
                    subprocess.run(['ffplay', '-nodisp', '-autoexit', filepath], 
                                 check=False, timeout=60)
                    logger.info(f"🔊 Воспроизведение (ffplay): {Path(filepath).name}")
                    return True
            except Exception as e2:
                logger.error(f"❌ Не удалось воспроизвести: {e2}")
                return False
    
    while app_state.running:
        try:
            # Получаем из очереди
            try:
                item = ai_response_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            
            if not item:
                continue
            
            with state_lock:
                app_state.speaking = True
                app_state.playback_stop_event.clear()
            
            # ============ ОБРАБОТКА АУДИОФАЙЛА ============
            if isinstance(item, dict) and item.get('type') == 'audio':
                audio_file = item.get('audio_file')
                tool_call_id = item.get('tool_call_id')
                response_text = item.get('text', '')
                
                logger.info(f"🔊 Очередь: {audio_file}")
                
                played_text = response_text
                interrupted = False
                
                try:
                    # Пробуем воспроизвести
                    success = play_audio_file(audio_file)
                    if not success:
                        interrupted = True
                
                except Exception as e:
                    logger.error(f"⚠️ Ошибка воспроизведения: {e}")
                    interrupted = True
                
                # Сохраняем результат в историю с tool_call_id
                tool_response = {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps({
                        "status": "completed" if not interrupted else "interrupted",
                        "text": response_text,
                        "audio_file": audio_file,
                        "played": played_text
                    }, ensure_ascii=False),
                    "timestamp": datetime.now().isoformat()
                }
                app_state.history.append(tool_response)
                logger.info(f"✅ Результат озвучки сохранен ({tool_call_id})")
                
                # Сохраняем историю
                save_history_to_file(history_file, app_state.history)
            
            # ============ ОБРАБОТКА ТЕКСТА (старый формат) ============
            elif isinstance(item, str):
                response_text = item
                
                if not response_text or not response_text.strip():
                    continue
                
                logger.info(f"🔊 Начало TTS синтеза: {response_text[:100]}...")
                
                # Разбиваем на предложения
                sentences = split_text_to_sentences(response_text)
                
                if not sentences:
                    logger.warning("⚠️ Не удалось разбить текст на предложения")
                    continue
                
                logger.info(f"📝 Разбито на {len(sentences)} предложений")
                
                played_text = ""
                interrupted = False
                
                if tts_model:
                    for i, sentence in enumerate(sentences):
                        # Проверяем, нужно ли прерваться
                        if app_state.playback_stop_event.is_set():
                            logger.info("⏸️ Воспроизведение прервано (обнаружена речь)")
                            interrupted = True
                            break
                        
                        logger.info(f"🎵 Синтез {i+1}/{len(sentences)}: {sentence[:50]}...")
                        
                        try:
                            # Синтезируем аудио для предложения
                            audio = tts_model.synthesize_auto(sentence)
                            
                            # Сохраняем во временный файл
                            audio_output_dir = Path(__file__).parent / 'audio_files'
                            audio_output_dir.mkdir(parents=True, exist_ok=True)
                            fd, tmp_path = tempfile.mkstemp(suffix='.wav', dir=str(audio_output_dir))
                            os.close(fd)
                            
                            try:
                                time.sleep(0.1)
                                audio_np = audio.cpu().numpy()
                                wavfile.write(tmp_path, 48000, (audio_np * 32767).astype(np.int16))
                                
                                # Воспроизводим
                                sound = pygame.mixer.Sound(tmp_path)
                                sound.play()
                                
                                # Ждем окончания с проверкой на прерывание
                                while pygame.mixer.get_busy():
                                    if app_state.playback_stop_event.is_set():
                                        pygame.mixer.stop()
                                        interrupted = True
                                        break
                                    time.sleep(0.1)
                                
                                played_text += sentence + " "
                                
                                if interrupted:
                                    break
                            
                            finally:
                                try:
                                    os.unlink(tmp_path)
                                except FileNotFoundError:
                                    pass
                        
                        except Exception as e:
                            logger.error(f"⚠️ Ошибка синтеза предложения: {e}")
                            continue
                    
                    # Сохраняем результат в историю
                    if played_text.strip():
                        tool_response = {
                            "role": "tool",
                            "tool_call_id": "audio_synthesis",
                            "content": json.dumps({
                                "status": "completed" if not interrupted else "interrupted",
                                "text": response_text,
                                "played": played_text.strip()
                            }, ensure_ascii=False),
                            "timestamp": datetime.now().isoformat()
                        }
                        app_state.history.append(tool_response)
                        logger.info(f"✅ Синтез завершен")
                        
                        # Сохраняем историю
                        save_history_to_file(history_file, app_state.history)
                else:
                    logger.warning("⚠️ TTS не инициализирован")
        
        except Exception as e:
            logger.error(f"❌ Ошибка в потоке воспроизведения: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(1)
        finally:
            with state_lock:
                app_state.speaking = False


# ============ ОСНОВНАЯ ФУНКЦИЯ ============
def main():
    """Главная функция."""
    global llm_handler, tts_model
    
    logger.info("=" * 60)
    logger.info("🎙️ Запуск AIRI 2.0")
    logger.info("=" * 60)
    
    # Инициализация
    if not initialize_settings():
        logger.error("❌ Не удалось загрузить настройки")
        return
    
    tool_handlers = get_tool_handlers()
    
    if not initialize_llm(tool_handlers):
        logger.error("❌ Не удалось инициализировать LLM")
        return
    
    if not initialize_tts():
        logger.warning("⚠️ TTS недоступен, речь не будет воспроизводиться")
    else:
        # Инициализируем audio tools с моделью TTS
        audio_tools.set_tts_model(tts_model)
        audio_tools.set_audio_output_dir(Path(__file__).parent / 'audio_output')
    
    if not initialize_stt():
        logger.error("❌ Не удалось инициализировать STT")
        return
    
    # История
    history_file = Path(__file__).parent / 'data' / 'chat_history.json'
    history_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Запуск потоков
    threads = [
        threading.Thread(target=thread_record_voice, daemon=False),
        threading.Thread(target=thread_process_ai, args=(history_file,), daemon=False),
        threading.Thread(target=thread_play_response, daemon=False) if tts_model else None
    ]
    threads = [t for t in threads if t]
    
    for thread in threads:
        thread.start()
    
    logger.info(f"✅ Запущено {len(threads)} потоков")
    logger.info("✅ История загружена")
    logger.info("✅ STT backend инициализирован")
    logger.info("\n🎤 Слушаю... (Ctrl+C для выхода)\n")
    
    # Основной цикл
    try:
        while app_state.running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("\n⏹️ Получена команда выхода (Ctrl+C)")
    
    # Остановка
    app_state.running = False
    
    # Ожидание завершения потоков
    for thread in threads:
        thread.join(timeout=5)
    
    logger.info("✅ Все потоки завершены")
    logger.info(f"📊 Всего сообщений в истории: {len(app_state.history)}")


if __name__ == "__main__":
    main()

