import os
import sys
import threading
import torch
from pathlib import Path

# Добавляем корневую папку проекта в sys.path, чтобы импорты из app работали
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from app.tts_backends import SileroTTS, XTTSBackend

def play_audio(tts, text):
    import sounddevice as sd
    import threading
    
    abort_event = threading.Event()
    try:
        # Для начала получаем первый чанк, чтобы понять sample_rate если нужно
        # Но у BaseTTS уже есть свойство sample_rate
        stream = sd.OutputStream(samplerate=tts.sample_rate, channels=1, dtype='float32')
        stream.start()
        
        try:
            for chunk in tts.stream_audio(text, abort_event):
                stream.write(chunk)
        finally:
            stream.stop()
            stream.close()
    except Exception as e:
        print(f"❌ Ошибка вывода звука: {e}")

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def main():
    default_device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
    print(f"--- Тестирование TTS ---")
    print(f"Доступное устройство по умолчанию: {default_device}")
    
    device_choice = 'cuda:1' #input(f"Введите устройство (cuda/cpu) [По умолчанию: {default_device}]: ").strip().lower()
    device = device_choice if device_choice in ['cuda:0', 'cuda:1', 'cpu'] else default_device
    print(f"Используемое устройство: {device}")
    
    # Инициализация бэкендов по выбору
    print("\nВыберите TTS бэкенд:")
    print("1. Silero (Быстрый, русский голос 'kseniya')")
    print("2. XTTS (Более качественный, требует больше ресурсов)")
    
    choice = "2" #input("Ваш выбор (1/2, по умолчанию 1): ").strip() or "1"
    
    try:
        if choice == "1":
            print("Загрузка Silero TTS...")
            tts = SileroTTS(language='ru', model_id='v5_ru', speaker='kseniya', device=device)
        elif choice == "2":
            os.environ["DS_BUILD_OPS"] = "0"
            os.environ["DS_BUILD_AIO"] = "0"
            os.environ["DS_BUILD_EVOFORMER_ATTN"] = "0"
            os.environ["DS_BUILD_SPARSE_ATTN"] = "0"
            os.environ["DS_BUILD_FP_QUANTIZER"] = "0"
            # Также можно запретить setuptools искать Visual Studio (на всякий случай)
            os.environ["DISTUTILS_USE_SDK"] = "0"
            
            print("Загрузка XTTS v2...")
            model_dir = str(project_root / "app" / "model" / "xtts")
            os.makedirs(model_dir, exist_ok=True)
            tts = XTTSBackend(model_dir=model_dir, language='ru', device=device)
        else:
            print("Неверный выбор. Используем Silero.")
            tts = SileroTTS(language='ru', model_id='v5_ru', speaker='kseniya', device=device)
            
        print("✅ Бэкенд готов! Прогрев модели...")
        # Прогрев модели для предотвращения лагов на первой фразе
        list(tts.stream_audio("Прогрев", threading.Event()))

        while True:
            print("\nВведите текст для синтеза (или 'exit' для выхода, 'file' для чтения из tests/data/test_samples.txt):")
            text = input("> ").strip()
            
            if text.lower() == 'exit':
                break
            
            if text.lower() == 'file':
                file_path = project_root / "tests" / "data" / "test_samples.txt"
                if file_path.exists():
                    with open(file_path, 'r', encoding='utf-8') as f:
                        text = f.read()
                    print(f"Читаем из файла:\n{text}")
                else:
                    print(f"Файл {file_path} не найден.")
                    continue
            
            if not text:
                continue
                
            print("🔊 Синтез и воспроизведение...")
            try:
                play_audio(tts, text)
            except Exception as e:
                print(f"❌ Ошибка при воспроизведении: {e}")
                
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
    
    print("\nЗавершение работы.")

if __name__ == "__main__":
    main()
