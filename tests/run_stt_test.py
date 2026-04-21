#!/usr/bin/env python3
"""Interactive STT test runner.

Запускает интерактивный тест STT с выбором бэкенда и модели.
После выбора начинает слушать микрофон и выводит распознанный текст в терминал.
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from app.STT import create_stt


def transcribe_file_whisper(stt, file_path: Path) -> str:
    import whisper

    audio = whisper.load_audio(str(file_path))
    # model attribute is available on WhisperSTT
    result = stt.model.transcribe(audio, language="ru", fp16=getattr(stt, "use_fp16", False))
    return result.get("text", "").strip()


def transcribe_file_vosk(stt, file_path: Path) -> str:
    import wave
    import json
    from vosk import KaldiRecognizer

    with wave.open(str(file_path), "rb") as wf:
        sr = wf.getframerate()
        rec = KaldiRecognizer(stt.model, sr)
        text_parts = []
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                t = res.get("text", "").strip()
                if t:
                    text_parts.append(t)
        # final
        final = json.loads(rec.FinalResult())
        t = final.get("text", "").strip()
        if t:
            text_parts.append(t)
        return " ".join(text_parts).strip()


def main():
    print("=== Интерактивный тест STT ===")
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
            print("Неверный выбор. Попробуйте снова.")

    model_name = None
    if backend == "whisper":
        print("\nДоступные модели Whisper: tiny, base, small, medium, large, large-v2, large-v3")
        model_name = input("Введите имя модели (по умолчанию 'tiny'): ").strip() or "tiny"
    elif backend == "vosk":
        print("\nДоступные модели Vosk: vosk-model-small-ru-0.22, vosk-model-ru-0.42 и др.")
        model_name = input("Введите имя модели (по умолчанию 'vosk-model-small-ru-0.22'): ").strip() or "vosk-model-small-ru-0.22"

    try:
        print(f"\nИнициализация {backend} с моделью {model_name}...")
        stt = create_stt(backend, model_name)
        print("✅ Бэкенд готов!")
    except Exception as e:
        print(f"❌ Ошибка создания STT бэкенда: {e}")
        return

    print("\n🎤 Начинаю слушать микрофон. Говорите в микрофон, текст будет выводиться в терминал.")
    print("Нажмите Ctrl+C для выхода.\n")

    try:
        for phrase in stt.stream():
            print(f"Распознано: {phrase}")
    except KeyboardInterrupt:
        print("\nВыход по запросу пользователя.")
    except Exception as e:
        print(f"❌ Ошибка потокового распознавания: {e}")


if __name__ == "__main__":
    main()
