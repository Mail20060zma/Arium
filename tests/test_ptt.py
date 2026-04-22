import json
import queue
import sys
import threading
import time
from collections import deque
from pathlib import Path

import keyboard
import numpy as np
import sounddevice as sd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.STT import create_stt
from app.utils.settings import Settings


SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SECONDS = 0.1
PRE_ROLL_SECONDS = 1.0
POST_ROLL_SECONDS = 1.0
RING_BUFFER_SECONDS = 6.0


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


def float32_to_pcm16(audio: np.ndarray) -> bytes:
    """Convert mono float32 [-1, 1] to raw PCM16 bytes."""
    clipped = np.clip(audio, -1.0, 1.0)
    int16 = (clipped * 32767.0).astype(np.int16)
    return int16.tobytes()


def transcribe_whisper(stt, audio: np.ndarray) -> str:
    result = stt.model.transcribe(
        audio,
        language="ru",
        fp16=getattr(stt, "use_fp16", False),
        temperature=0.0,
    )
    return result.get("text", "").strip()


def transcribe_vosk(stt, audio: np.ndarray) -> str:
    from vosk import KaldiRecognizer

    pcm = float32_to_pcm16(audio)
    recognizer = KaldiRecognizer(stt.model, SAMPLE_RATE)
    recognizer.SetWords(False)

    step = 4000 * 2  # 4000 samples * 2 bytes
    texts: list[str] = []
    for i in range(0, len(pcm), step):
        chunk = pcm[i : i + step]
        if recognizer.AcceptWaveform(chunk):
            data = json.loads(recognizer.Result())
            text = data.get("text", "").strip()
            if text:
                texts.append(text)

    final_data = json.loads(recognizer.FinalResult())
    final_text = final_data.get("text", "").strip()
    if final_text:
        texts.append(final_text)

    return " ".join(t for t in texts if t).strip()


def transcribe_google(stt, audio: np.ndarray) -> str:
    import speech_recognition as sr

    pcm = float32_to_pcm16(audio)
    audio_data = sr.AudioData(pcm, SAMPLE_RATE, 2)
    return stt._recognizer.recognize_google(audio_data, language="ru-RU", show_all=False).strip()


def transcribe_segment(stt, backend: str, audio: np.ndarray) -> str:
    if backend == "whisper":
        return transcribe_whisper(stt, audio)
    if backend == "vosk":
        return transcribe_vosk(stt, audio)
    if backend == "google":
        return transcribe_google(stt, audio)
    raise ValueError(f"Неизвестный backend: {backend}")


def transcriber_worker(
    stt,
    backend: str,
    in_queue: queue.Queue,
    out_queue: queue.Queue,
    stop_event: threading.Event,
) -> None:
    """Consume captured segments and run STT only on demand (PTT events)."""
    while not stop_event.is_set():
        try:
            segment = in_queue.get(timeout=0.1)
        except queue.Empty:
            continue

        if segment is None:
            break

        try:
            text = transcribe_segment(stt, backend, segment)
            out_queue.put(("text", text))
        except Exception as exc:  # noqa: BLE001
            out_queue.put(("error", exc))
        finally:
            in_queue.task_done()


def main() -> None:
    backend, model_name, ptt_key = load_runtime_config()
    exit_key = "esc"

    print("🎤 Тест Buffered Push-to-Talk + STT")
    print(f"STT backend: {backend}")
    print(f"STT model: {model_name}")
    print(f"PTT key: {ptt_key}")
    print(f"Параметры окна: -{PRE_ROLL_SECONDS:.1f}с до нажатия, +{POST_ROLL_SECONDS:.1f}с после отпускания")
    print("Захват аудио идет постоянно короткими чанками, STT запускается только по событию PTT.")
    print(f"Нажмите '{exit_key}' или Ctrl+C для выхода.\n")

    try:
        stt = create_stt(backend, model_name)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Ошибка инициализации STT: {exc}")
        return

    ring_buffer_max = max(1, int(RING_BUFFER_SECONDS / CHUNK_SECONDS))
    ring_buffer: deque[np.ndarray] = deque(maxlen=ring_buffer_max)
    state_lock = threading.Lock()

    capture_state = "idle"  # idle | recording | post
    active_chunks: list[np.ndarray] = []
    post_deadline = 0.0

    stop_event = threading.Event()
    segment_queue: queue.Queue[np.ndarray | None] = queue.Queue()
    result_queue: queue.Queue[tuple[str, object]] = queue.Queue()

    transcriber = threading.Thread(
        target=transcriber_worker,
        args=(stt, backend, segment_queue, result_queue, stop_event),
        daemon=True,
        name="PTT_Transcriber",
    )
    transcriber.start()

    def audio_callback(indata, frames, _time_info, _status):
        nonlocal active_chunks
        chunk = indata[:, 0].copy().astype(np.float32)
        with state_lock:
            ring_buffer.append(chunk)
            if capture_state in ("recording", "post"):
                active_chunks.append(chunk)

    blocksize = int(SAMPLE_RATE * CHUNK_SECONDS)
    segments_sent = 0
    segments_with_text = 0
    last_ptt_state = None

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=blocksize,
            callback=audio_callback,
        ):
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

                to_process: np.ndarray | None = None
                now = time.monotonic()

                with state_lock:
                    if ptt_pressed and capture_state == "idle":
                        capture_state = "recording"
                        active_chunks = list(ring_buffer)[-int(PRE_ROLL_SECONDS / CHUNK_SECONDS) :]
                        post_deadline = 0.0
                        print(f"\n[PTT] ▶ Старт захвата: добавлено {len(active_chunks)} pre-roll чанков")

                    elif (not ptt_pressed) and capture_state == "recording":
                        capture_state = "post"
                        post_deadline = now + POST_ROLL_SECONDS
                        print(f"\n[PTT] ⏱ Отпущено: добираю post-roll {POST_ROLL_SECONDS:.1f}с")

                    if capture_state == "post" and now >= post_deadline:
                        capture_state = "idle"
                        if active_chunks:
                            to_process = np.concatenate(active_chunks)
                        active_chunks = []

                if to_process is not None and to_process.size > 0:
                    segments_sent += 1
                    print(
                        f"\n[PTT] 📦 Сегмент #{segments_sent}: {to_process.size / SAMPLE_RATE:.2f}с, отправка в STT..."
                    )
                    segment_queue.put(to_process)

                try:
                    result_type, payload = result_queue.get(timeout=0.03)
                except queue.Empty:
                    continue

                if result_type == "error":
                    raise RuntimeError(f"Ошибка STT: {payload}")

                text = str(payload).strip()
                if text:
                    segments_with_text += 1
                    print(f"\n[PTT+STT] ✅ #{segments_with_text}: {text}")
                else:
                    print("\n[PTT+STT] ... Пустой результат")

    except KeyboardInterrupt:
        print("\nПрервано пользователем.")
    except Exception as exc:  # noqa: BLE001
        print(f"\n❌ Ошибка: {exc}")
    finally:
        stop_event.set()
        segment_queue.put(None)

        if hasattr(stt, "_running"):
            setattr(stt, "_running", False)

        print(f"\nИтог: сегментов отправлено={segments_sent}, непустых распознаваний={segments_with_text}")


if __name__ == "__main__":
    main()