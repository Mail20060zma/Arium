import queue
import sys
import threading
import time
from pathlib import Path

import keyboard


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.STT import create_stt
from app.utils.settings import Settings


def load_runtime_config() -> tuple[str, str | None, str]:
    """Load STT and PTT settings from the project config."""
    settings_path = PROJECT_ROOT / "app" / "utils" / "settings.json"
    settings = Settings(str(settings_path))

    backend = settings.get("stt_provider", "whisper")
    model_name = settings.get("stt_model", "small")
    ptt_key = settings.get("controls.ptt_keys", "alt")

    if backend == "google":
        model_name = None

    if not isinstance(ptt_key, str) or not ptt_key.strip():
        ptt_key = "alt"

    return backend, model_name, ptt_key


def stt_listener(stt, output_queue: queue.Queue, stop_event: threading.Event) -> None:
    """Read phrases from STT backend and forward them to the main thread."""
    try:
        for phrase in stt.stream():
            if stop_event.is_set():
                break

            text = phrase.strip()
            if text:
                output_queue.put(("text", text))
    except Exception as exc:  # noqa: BLE001
        output_queue.put(("error", exc))


def main() -> None:
    backend, model_name, ptt_key = load_runtime_config()
    exit_key = "esc"

    print("🎤 Тест Push-to-Talk + STT")
    print(f"STT backend: {backend}")
    print(f"STT model: {model_name}")
    print(f"PTT key: {ptt_key}")
    print(f"Удерживайте '{ptt_key}' и говорите в микрофон.")
    print(f"Нажмите '{exit_key}' или Ctrl+C для выхода.\n")

    try:
        stt = create_stt(backend, model_name)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Ошибка инициализации STT: {exc}")
        return

    output_queue: queue.Queue[tuple[str, object]] = queue.Queue()
    stop_event = threading.Event()

    listener = threading.Thread(
        target=stt_listener,
        args=(stt, output_queue, stop_event),
        daemon=True,
        name="PTT_STT_Test",
    )
    listener.start()

    accepted = 0
    skipped = 0
    last_ptt_state = None

    try:
        while not stop_event.is_set():
            if keyboard.is_pressed(exit_key):
                print("\nВыход из теста.")
                stop_event.set()
                break

            ptt_pressed = keyboard.is_pressed(ptt_key)
            if ptt_pressed != last_ptt_state:
                state = "зажата" if ptt_pressed else "отпущена"
                print(f"\r[PTT] Клавиша '{ptt_key}' {state}.", end="", flush=True)
                last_ptt_state = ptt_pressed

            try:
                item_type, payload = output_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            if item_type == "error":
                raise RuntimeError(f"Ошибка STT потока: {payload}")

            if item_type == "text":
                # Same core logic: phrase is accepted only when PTT key is pressed.
                if keyboard.is_pressed(ptt_key):
                    accepted += 1
                    print(f"\n[PTT+STT] ✅ Принято ({accepted}): {payload}")
                else:
                    skipped += 1
                    print(f"\n[PTT+STT] ⏭️ Пропуск ({skipped}): {payload}")

    except KeyboardInterrupt:
        print("\nПрервано пользователем.")
    finally:
        stop_event.set()

        # Best-effort stop for current STT implementations.
        if hasattr(stt, "_running"):
            setattr(stt, "_running", False)

        print(f"\nИтог: принято={accepted}, пропущено={skipped}")


if __name__ == "__main__":
    main()