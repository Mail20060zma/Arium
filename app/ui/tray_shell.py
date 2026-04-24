from __future__ import annotations

import ctypes
import io
import importlib
import json
import logging
import platform
import threading
import time
import tkinter as tk
import wave
from ctypes import wintypes
from pathlib import Path


if platform.system() != "Windows":
    raise RuntimeError("tray_shell.py currently supports Windows only.")


# Win32 constants
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_USER = 0x0400
WM_APP_TRAY = WM_USER + 1
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_HOTKEY = 0x0312

NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_DEFAULTSIZE = 0x00000040
LR_SHARED = 0x00008000

MF_STRING = 0x00000000
TPM_RIGHTBUTTON = 0x0002
TPM_BOTTOMALIGN = 0x0020
TPM_LEFTALIGN = 0x0000

CMD_SETTINGS = 1000
CMD_EXIT = 1001
WM_APP_UPDATE_HOTKEY = WM_USER + 2

HOTKEY_ID_MAIN = 0xA11
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

# ============ SETTINGS POPUP LAYOUT TUNING ============
# Change these values to quickly adjust popup size/position without diving into class internals.
SETTINGS_POPUP_UI = {
    # Size behavior (relative to screen, clamped by min/max)
    "width_ratio": 0.22,
    "height_ratio": 0.34,
    "min_width": 340,
    "max_width": 460,
    "min_height": 240,
    "max_height": 360,
    # Corner / spacing
    "corner_radius_ratio": 0.06,
    "min_corner_radius": 16,
    "max_corner_radius": 24,
    "content_pad_ratio": 0.06,
    "min_content_pad": 16,
    # Position (bottom-right anchor by default)
    "anchor": "bottom-right",
    "offset_right": 20,
    "offset_bottom": 88,
    # Internal content density
    "section_gap": 62,
    # Animation
    "open_steps": 14,
    "open_step_ms": 12,
    "open_lift_px": 12,
    "close_steps": 10,
    "close_step_ms": 10,
    "close_drop_px": 8,
}

# ============ FLOATING SETTINGS BUTTON TUNING ============
# Screen position and size for always-visible settings overlay button.
SETTINGS_OVERLAY_BUTTON_UI = {
    "anchor": "bottom-right",
    "diameter": 46,
    "offset_right": 12,
    "offset_bottom": 52,
    "accent_color": "#ac2fff",
    "icon": "^",
}


logger = logging.getLogger(__name__)


def parse_global_hotkey(shortcut: str) -> tuple[int, int] | None:
    """Parses shortcut like Ctrl+Shift+Space into (modifiers, virtual_key)."""
    if not shortcut:
        return None

    tokens = [part.strip().lower() for part in shortcut.split("+") if part.strip()]
    if not tokens:
        return None

    modifiers = 0
    vk = None

    key_map = {
        "space": 0x20,
        "enter": 0x0D,
        "tab": 0x09,
        "esc": 0x1B,
        "escape": 0x1B,
        "up": 0x26,
        "down": 0x28,
        "left": 0x25,
        "right": 0x27,
        "home": 0x24,
        "end": 0x23,
        "pgup": 0x21,
        "pgdn": 0x22,
        "insert": 0x2D,
        "delete": 0x2E,
    }

    for token in tokens:
        if token in {"ctrl", "control"}:
            modifiers |= MOD_CONTROL
            continue
        if token == "shift":
            modifiers |= MOD_SHIFT
            continue
        if token == "alt":
            modifiers |= MOD_ALT
            continue
        if token in {"win", "windows"}:
            modifiers |= MOD_WIN
            continue

        if len(token) == 1 and token.isalpha():
            vk = ord(token.upper())
            continue
        if len(token) == 1 and token.isdigit():
            vk = ord(token)
            continue
        if token.startswith("f") and token[1:].isdigit():
            n = int(token[1:])
            if 1 <= n <= 24:
                vk = 0x70 + (n - 1)
                continue
        if token in key_map:
            vk = key_map[token]
            continue

    if vk is None:
        return None
    return modifiers, vk


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# Some Python builds expose a reduced wintypes surface.
HICON = getattr(wintypes, "HICON", wintypes.HANDLE)
HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)
HBRUSH = getattr(wintypes, "HBRUSH", wintypes.HANDLE)


# Use pointer-sized WinAPI integer types to avoid 32-bit truncation on x64.
LRESULT_T = getattr(wintypes, "LRESULT", ctypes.c_ssize_t)
WPARAM_T = getattr(wintypes, "WPARAM", ctypes.c_size_t)
LPARAM_T = getattr(wintypes, "LPARAM", ctypes.c_ssize_t)


WNDPROC = ctypes.WINFUNCTYPE(LRESULT_T, wintypes.HWND, wintypes.UINT, WPARAM_T, LPARAM_T)

# Explicit signatures are required; otherwise ctypes defaults to c_int args and overflows on x64.
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM_T, LPARAM_T]
user32.DefWindowProcW.restype = LRESULT_T

user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM_T, LPARAM_T]
user32.PostMessageW.restype = wintypes.BOOL

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL

user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL

user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = wintypes.BOOL

user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT_T


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", HICON),
        ("szTip", wintypes.WCHAR * 128),
    ]


class EdgeGlowAnimator:
    """Animates thin topmost overlays on all screen edges."""

    def __init__(self, root: tk.Tk, color: str = "#ac2fff", thickness: int = 8):
        self.root = root
        self.color = color
        self.thickness = thickness

    def play(self, duration_ms: int = 1200, frames: int = 36) -> None:
        width = self.root.winfo_screenwidth()
        height = self.root.winfo_screenheight()
        horizontal_width = max(width - (self.thickness * 2), 1)

        overlays = []
        geometry = [
            f"{horizontal_width}x{self.thickness}+{self.thickness}+0",
            f"{horizontal_width}x{self.thickness}+{self.thickness}+{height - self.thickness}",
            f"{self.thickness}x{height}+0+0",
            f"{self.thickness}x{height}+{width - self.thickness}+0",
        ]

        for geo in geometry:
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.attributes("-alpha", 0.0)
            win.configure(bg=self.color)
            win.geometry(geo)
            overlays.append(win)

        frame_sleep = max(duration_ms / frames / 1000.0, 0.001)

        for i in range(frames + 1):
            t = i / frames
            # Smooth pulse: 0 -> max -> 0
            alpha = 0.85 * (1.0 - abs(2.0 * t - 1.0))
            for win in overlays:
                win.attributes("-alpha", alpha)
            self.root.update_idletasks()
            self.root.update()
            time.sleep(frame_sleep)

        for win in overlays:
            win.destroy()


class UiSettingsStore:
    """Simple JSON-backed settings storage for the standalone UI shell."""

    DEFAULTS = {
        "response_mode": "Голосом",
        "launch_shortcut": "Ctrl+Shift+Space",
        "theme": "Тёмная",
        "overlay_settings_button": "Включена",
    }

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return self.DEFAULTS.copy()

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, TypeError):
            return self.DEFAULTS.copy()

        merged = self.DEFAULTS.copy()
        for key in merged:
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                merged[key] = val
        return merged

    def save(self, settings: dict[str, str]) -> None:
        merged = self.DEFAULTS.copy()
        merged.update(settings)

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
        except OSError:
            # Keep UI responsive even if filesystem is temporarily unavailable.
            return


class BackendAiBridge:
    """Starts existing AI/TTS processing threads and accepts text from UI."""

    def __init__(self):
        self.core = None
        self.threads: list[threading.Thread] = []
        self.started = False

    def start(self) -> bool:
        if self.started:
            return True

        try:
            from app import main as core
        except Exception as exc:
            logger.error(f"AI bridge import failed: {exc}")
            return False

        self.core = core

        try:
            core.app_state.running = True

            if not core.initialize_settings():
                logger.error("AI bridge failed: initialize_settings")
                return False

            tool_handlers = core.get_tool_handlers()
            if not core.initialize_llm(tool_handlers):
                logger.error("AI bridge failed: initialize_llm")
                return False

            tts_ok = core.initialize_tts()
            if tts_ok:
                core.audio_tools.set_tts_model(core.tts_model)
                core.audio_tools.set_audio_output_dir(Path(core.__file__).parent / "audio_output")
            else:
                logger.warning("AI bridge: TTS unavailable, playback thread will be skipped")

            history_file = Path(core.__file__).parent / "data" / "chat_history.json"
            history_file.parent.mkdir(parents=True, exist_ok=True)

            threads = [
                threading.Thread(
                    target=core.thread_process_ai,
                    args=(history_file,),
                    daemon=True,
                    name="AiriAIProcessing",
                )
            ]
            if core.tts_model:
                threads.append(
                    threading.Thread(
                        target=core.thread_play_response,
                        daemon=True,
                        name="AiriPlayback",
                    )
                )

            for thread in threads:
                thread.start()

            self.threads = threads
            self.started = True
            logger.info("AI bridge started")
            return True

        except Exception as exc:
            logger.error(f"AI bridge start failed: {exc}")
            return False

    def submit_text(self, text: str) -> bool:
        if not self.started or not self.core:
            return False
        text = (text or "").strip()
        if not text:
            return False

        try:
            self.core.voice_input_queue.put_nowait(text)
            return True
        except Exception as exc:
            logger.warning(f"AI bridge queue submit failed: {exc}")
            return False

    def stop(self) -> None:
        if not self.started or not self.core:
            return

        self.core.app_state.running = False
        for thread in self.threads:
            thread.join(timeout=2.0)

        self.threads = []
        self.started = False
        logger.info("AI bridge stopped")


class HotkeyVoiceController:
    """Hotkey-driven recording: toggle record -> transcribe with project STT -> emit text."""

    def __init__(self, on_text_ready, on_record_started=None):
        self.on_text_ready = on_text_ready
        self.on_record_started = on_record_started

        self.sample_rate = 16000
        self.sample_width = 2

        self._recording = False
        self._transcribing = False
        self._capture_thread: threading.Thread | None = None
        self._backend_cache: dict[tuple[str, str | None], object] = {}

        self._chunks_lock = threading.Lock()
        self._audio_chunks: list[bytes] = []

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_transcribing(self) -> bool:
        return self._transcribing

    def toggle_recording(self) -> None:
        if self._transcribing:
            return
        if self._recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        if self._recording:
            return

        with self._chunks_lock:
            self._audio_chunks.clear()

        self._recording = True
        if callable(self.on_record_started):
            self.on_record_started()

        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name="AiriHotkeyRecorder",
        )
        self._capture_thread.start()

    def stop_recording(self) -> None:
        if not self._recording:
            return

        self._recording = False
        threading.Thread(
            target=self._finalize_and_transcribe,
            daemon=True,
            name="AiriHotkeyTranscribe",
        ).start()

    def shutdown(self) -> None:
        self._recording = False
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=1.5)

    def _capture_loop(self) -> None:
        try:
            sr = importlib.import_module("speech_recognition")

            recognizer = sr.Recognizer()
            with sr.Microphone(sample_rate=self.sample_rate) as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.25)

                while self._recording:
                    try:
                        audio = recognizer.listen(source, timeout=0.5, phrase_time_limit=4)
                        raw = audio.get_raw_data(convert_rate=self.sample_rate, convert_width=self.sample_width)
                        if raw:
                            with self._chunks_lock:
                                self._audio_chunks.append(raw)
                    except sr.WaitTimeoutError:
                        continue
        except Exception as exc:
            logger.warning(f"Recorder capture loop stopped: {exc}")

    def _finalize_and_transcribe(self) -> None:
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=2.0)

        with self._chunks_lock:
            pcm = b"".join(self._audio_chunks)
            self._audio_chunks.clear()

        if not pcm:
            logger.info("No audio captured for transcription")
            return

        self._transcribing = True
        try:
            text = self._transcribe_pcm(pcm)
            if text and callable(self.on_text_ready):
                self.on_text_ready(text)
        finally:
            self._transcribing = False

    def _get_stt_backend(self):
        from app.STT import create_stt
        from app.utils.settings import Settings

        settings = Settings("settings.json")
        provider = settings.get("stt_provider", "whisper")
        model_name = settings.get("stt_model", "small")
        if provider == "google":
            model_name = None

        key = (provider, model_name)
        backend = self._backend_cache.get(key)
        if backend is not None:
            return provider, backend

        backend = create_stt(provider, model_name)
        self._backend_cache[key] = backend
        return provider, backend

    def _transcribe_pcm(self, pcm_bytes: bytes) -> str:
        try:
            provider, backend = self._get_stt_backend()

            if provider == "whisper":
                wav_bytes = self._pcm_to_wav_bytes(pcm_bytes)
                audio_array = backend._wav_bytes_to_numpy(wav_bytes)
                result = backend.model.transcribe(
                    audio_array,
                    language="ru",
                    fp16=getattr(backend, "use_fp16", False),
                    temperature=0.0,
                    compression_ratio_threshold=2.4,
                    logprob_threshold=-1.0,
                )
                return result.get("text", "").strip()

            if provider == "vosk":
                KaldiRecognizer = importlib.import_module("vosk").KaldiRecognizer

                recognizer = KaldiRecognizer(backend.model, self.sample_rate)
                recognizer.SetWords(False)
                recognizer.AcceptWaveform(pcm_bytes)
                result = json.loads(recognizer.FinalResult())
                return result.get("text", "").strip()

            if provider == "google":
                sr = importlib.import_module("speech_recognition")

                audio = sr.AudioData(pcm_bytes, self.sample_rate, self.sample_width)
                return backend._recognizer.recognize_google(
                    audio,
                    language="ru-RU",
                    show_all=False,
                ).strip()

            logger.warning(f"Unsupported STT provider: {provider}")
            return ""

        except Exception as exc:
            logger.warning(f"Transcription failed: {exc}")
            return ""

    def _pcm_to_wav_bytes(self, pcm_bytes: bytes) -> bytes:
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(self.sample_width)
                wav_file.setframerate(self.sample_rate)
                wav_file.writeframes(pcm_bytes)
            return buffer.getvalue()


class SettingsPopup:
    """Minimalist settings popup with rounded corners and fade-in."""

    def __init__(self, root: tk.Tk, accent_color: str = "#ac2fff", on_settings_changed=None, on_closed=None):
        self.root = root
        self.accent_color = accent_color
        self.on_settings_changed = on_settings_changed
        self.on_closed = on_closed
        self.window: tk.Toplevel | None = None

        self.store = UiSettingsStore(Path(__file__).with_name("ui_settings.json"))
        self.settings = self.store.load()

        self.response_mode_var = tk.StringVar(value=self.settings["response_mode"])
        self.shortcut_var = tk.StringVar(value=self.settings["launch_shortcut"])
        self.theme_var = tk.StringVar(value=self.settings["theme"])
        self.overlay_button_var = tk.StringVar(value=self.settings["overlay_settings_button"])

        self._capturing_shortcut = False
        self._shortcut_button: tk.Button | None = None
        self._content_frame: tk.Frame | None = None
        self._shortcut_entry: tk.Entry | None = None
        self._canvas: tk.Canvas | None = None
        self._dropdown_popup: tk.Toplevel | None = None
        self._closing = False
        self._base_x = 0
        self._base_y = 0

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        layout = SETTINGS_POPUP_UI

        # Responsive popup sizing for different display resolutions.
        self.width = max(layout["min_width"], min(int(sw * layout["width_ratio"]), layout["max_width"]))
        self.height = max(layout["min_height"], min(int(sh * layout["height_ratio"]), layout["max_height"]))
        self.radius = max(
            layout["min_corner_radius"],
            min(int(self.width * layout["corner_radius_ratio"]), layout["max_corner_radius"]),
        )
        self.content_pad = max(layout["min_content_pad"], int(self.width * layout["content_pad_ratio"]))
        self.anchor = layout["anchor"]
        self.offset_right = layout["offset_right"]
        self.offset_bottom = layout["offset_bottom"]

        self.mask_color = "#ff00ff"
        self.bg_color = "#232323"
        self.title_color = "#ececec"
        self.label_color = "#b8b8b8"
        self.input_bg_color = "#2b2b2b"
        self.input_fg_color = "#ececec"
        self.section_gap = int(layout["section_gap"])
        self.open_steps = int(layout["open_steps"])
        self.open_step_ms = int(layout["open_step_ms"])
        self.open_lift_px = int(layout["open_lift_px"])
        self.close_steps = int(layout["close_steps"])
        self.close_step_ms = int(layout["close_step_ms"])
        self.close_drop_px = int(layout["close_drop_px"])

        # Ensure there is enough vertical space for all settings rows.
        min_required_height = 344
        self.height = max(self.height, min_required_height)

        self._apply_theme_palette(self.theme_var.get())

    def _apply_theme_palette(self, theme_value: str) -> None:
        if theme_value == "Светлая":
            self.bg_color = "#f3f3f3"
            self.title_color = "#232323"
            self.label_color = "#444444"
            self.input_bg_color = "#ffffff"
            self.input_fg_color = "#1f1f1f"
        else:
            self.bg_color = "#232323"
            self.title_color = "#ececec"
            self.label_color = "#b8b8b8"
            self.input_bg_color = "#2b2b2b"
            self.input_fg_color = "#ececec"

    def _persist(self) -> None:
        self.settings["response_mode"] = self.response_mode_var.get()
        self.settings["launch_shortcut"] = self.shortcut_var.get()
        self.settings["theme"] = self.theme_var.get()
        self.settings["overlay_settings_button"] = self.overlay_button_var.get()
        self.store.save(self.settings)
        if callable(self.on_settings_changed):
            self.on_settings_changed(self.settings.copy())

    def _on_response_mode_changed(self, value: str) -> None:
        self.response_mode_var.set(value)
        self._persist()

    def _on_theme_changed(self, value: str) -> None:
        self.theme_var.set(value)
        self._persist()
        self._apply_theme_palette(value)
        if self.window and self.window.winfo_exists():
            self.close(notify=False)
            self.open()

    def _start_shortcut_capture(self) -> None:
        self._capturing_shortcut = True
        if self.window and self.window.winfo_exists():
            self.window.focus_force()
        if self._shortcut_button and self._shortcut_button.winfo_exists():
            self._shortcut_button.configure(text="Нажмите клавиши...")

    def _on_overlay_button_changed(self, value: str) -> None:
        self.overlay_button_var.set(value)
        self._persist()

    def _on_key_press(self, event: tk.Event) -> None:
        if not self._capturing_shortcut:
            return

        modifiers: list[str] = []
        state = getattr(event, "state", 0)
        if state & 0x0004:
            modifiers.append("Ctrl")
        if state & 0x0001:
            modifiers.append("Shift")
        if state & 0x0008:
            modifiers.append("Alt")

        key = (event.keysym or "").strip()
        if not key:
            return

        blocked = {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"}
        if key in blocked:
            return

        pretty_key = "Space" if key == "space" else key.capitalize()
        combo = "+".join(modifiers + [pretty_key]) if modifiers else pretty_key

        self.shortcut_var.set(combo)
        if self._shortcut_entry and self._shortcut_entry.winfo_exists():
            self._shortcut_entry.configure(state="normal")
            self._shortcut_entry.delete(0, "end")
            self._shortcut_entry.insert(0, combo)
            self._shortcut_entry.configure(state="readonly")

        self._capturing_shortcut = False
        if self._shortcut_button and self._shortcut_button.winfo_exists():
            self._shortcut_button.configure(text="Назначить")
        self._persist()

    def _build_controls(self, canvas: tk.Canvas) -> None:
        frame = tk.Frame(canvas, bg=self.bg_color)
        self._content_frame = frame

        row_y = 6
        row_gap = 70
        inner_width = self.width - (self.content_pad * 2)
        field_h = 38
        label_w = max(132, int(inner_width * 0.40))
        right_w = inner_width - label_w

        self._build_row_label(frame, "Ответ модели", 0, row_y, label_w, field_h)
        self._create_rounded_select(
            parent=frame,
            x=label_w,
            y=row_y,
            width=right_w,
            height=field_h,
            value_var=self.response_mode_var,
            options=["Текстом", "Голосом"],
            on_change=self._on_response_mode_changed,
        )

        row_y += row_gap
        self._build_row_label(frame, "Комбинация запуска", 0, row_y, label_w, field_h)

        gap = 8
        action_w = 108
        shortcut_w = right_w - action_w - gap

        shortcut_entry = self._create_rounded_readonly(
            parent=frame,
            x=label_w,
            y=row_y,
            width=shortcut_w,
            height=field_h,
            value_var=self.shortcut_var,
        )
        self._shortcut_entry = shortcut_entry

        shortcut_btn = self._create_rounded_button(
            parent=frame,
            x=label_w + shortcut_w + gap,
            y=row_y,
            width=action_w,
            height=field_h,
            text="Назначить",
            command=self._start_shortcut_capture,
        )
        self._shortcut_button = shortcut_btn

        row_y += row_gap
        self._build_row_label(frame, "Тема", 0, row_y, label_w, field_h)
        self._create_rounded_select(
            parent=frame,
            x=label_w,
            y=row_y,
            width=right_w,
            height=field_h,
            value_var=self.theme_var,
            options=["Светлая", "Тёмная"],
            on_change=self._on_theme_changed,
        )

        row_y += row_gap
        self._build_row_label(frame, "Кнопка настроек", 0, row_y, label_w, field_h)
        self._create_rounded_select(
            parent=frame,
            x=label_w,
            y=row_y,
            width=right_w,
            height=field_h,
            value_var=self.overlay_button_var,
            options=["Включена", "Отключена"],
            on_change=self._on_overlay_button_changed,
        )

        content_top = 58
        content_height = self.height - content_top - self.content_pad
        canvas.create_window(
            self.content_pad,
            content_top,
            anchor="nw",
            window=frame,
            width=inner_width,
            height=content_height,
        )

    def _build_row_label(self, parent: tk.Widget, text: str, x: int, y: int, width: int, height: int) -> None:
        label = tk.Label(
            parent,
            text=text,
            bg=self.bg_color,
            fg=self.label_color,
            anchor="w",
            font=("Segoe UI", 10),
        )
        label.place(x=x, y=y, width=width, height=height)

    def _draw_rounded_box(self, canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, r: int, fill: str) -> None:
        canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")
        canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline="")
        canvas.create_arc(x1, y1, x1 + 2 * r, y1 + 2 * r, start=90, extent=90, fill=fill, outline="")
        canvas.create_arc(x2 - 2 * r, y1, x2, y1 + 2 * r, start=0, extent=90, fill=fill, outline="")
        canvas.create_arc(x1, y2 - 2 * r, x1 + 2 * r, y2, start=180, extent=90, fill=fill, outline="")
        canvas.create_arc(x2 - 2 * r, y2 - 2 * r, x2, y2, start=270, extent=90, fill=fill, outline="")

    def _create_rounded_button(
        self,
        parent: tk.Widget,
        x: int,
        y: int,
        width: int,
        height: int,
        text: str,
        command,
    ) -> tk.Button:
        canvas = tk.Canvas(parent, width=width, height=height, bg=self.bg_color, highlightthickness=0, bd=0)
        canvas.place(x=x, y=y)
        radius = min(12, max(8, height // 3))
        self._draw_rounded_box(canvas, 0, 0, width, height, radius, self.accent_color)

        btn = tk.Button(
            canvas,
            text=text,
            command=command,
            relief="flat",
            bd=0,
            bg=self.accent_color,
            activebackground=self.accent_color,
            fg="#ffffff",
            activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
            highlightthickness=0,
        )
        canvas.create_window(width // 2, height // 2, window=btn)
        return btn

    def _create_rounded_readonly(
        self,
        parent: tk.Widget,
        x: int,
        y: int,
        width: int,
        height: int,
        value_var: tk.StringVar,
    ) -> tk.Entry:
        canvas = tk.Canvas(parent, width=width, height=height, bg=self.bg_color, highlightthickness=0, bd=0)
        canvas.place(x=x, y=y)
        radius = min(12, max(8, height // 3))
        self._draw_rounded_box(canvas, 0, 0, width, height, radius, self.input_bg_color)

        entry = tk.Entry(
            canvas,
            textvariable=value_var,
            state="readonly",
            readonlybackground=self.input_bg_color,
            fg=self.input_fg_color,
            bd=0,
            relief="flat",
            font=("Segoe UI", 10),
            highlightthickness=0,
            justify="center",
        )
        canvas.create_window(width // 2, height // 2, window=entry, width=width - 24)
        return entry

    def _create_rounded_select(
        self,
        parent: tk.Widget,
        x: int,
        y: int,
        width: int,
        height: int,
        value_var: tk.StringVar,
        options: list[str],
        on_change,
    ) -> None:
        canvas = tk.Canvas(parent, width=width, height=height, bg=self.bg_color, highlightthickness=0, bd=0)
        canvas.place(x=x, y=y)
        radius = min(12, max(8, height // 3))
        self._draw_rounded_box(canvas, 0, 0, width, height, radius, self.input_bg_color)

        btn = tk.Button(
            canvas,
            text=f"{value_var.get()}  ▾",
            command=lambda: self._open_dropdown(btn, value_var, options, on_change),
            relief="flat",
            bd=0,
            bg=self.input_bg_color,
            activebackground=self.input_bg_color,
            fg=self.input_fg_color,
            activeforeground=self.input_fg_color,
            font=("Segoe UI", 10),
            cursor="hand2",
            highlightthickness=0,
            anchor="center",
        )
        canvas.create_window(width // 2, height // 2, window=btn, width=width - 18)

        def sync_text(*_args):
            if btn.winfo_exists():
                btn.configure(text=f"{value_var.get()}  ▾")

        value_var.trace_add("write", sync_text)

    def _open_dropdown(self, anchor: tk.Button, value_var: tk.StringVar, options: list[str], on_change) -> None:
        self._close_dropdown()

        popup = tk.Toplevel(self.window)
        self._dropdown_popup = popup
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=self.mask_color)
        popup.attributes("-transparentcolor", self.mask_color)

        anchor.update_idletasks()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 6
        width = max(anchor.winfo_width(), 168)
        item_h = 34
        height = (len(options) * item_h) + 12
        popup.geometry(f"{width}x{height}+{x}+{y}")

        cvs = tk.Canvas(popup, width=width, height=height, bg=self.mask_color, highlightthickness=0, bd=0)
        cvs.pack(fill="both", expand=True)
        self._draw_rounded_box(cvs, 0, 0, width, height, 12, self.input_bg_color)

        for i, option in enumerate(options):
            btn = tk.Button(
                popup,
                text=option,
                relief="flat",
                bd=0,
                bg=self.input_bg_color,
                activebackground=self.accent_color,
                fg=self.input_fg_color,
                activeforeground="#ffffff",
                font=("Segoe UI", 10),
                cursor="hand2",
                anchor="w",
                padx=12,
                command=lambda v=option: self._select_dropdown_value(v, value_var, on_change),
            )
            btn.place(x=6, y=6 + i * item_h, width=width - 12, height=item_h)

        popup.bind("<FocusOut>", lambda _e: self._close_dropdown())
        popup.focus_force()

    def _select_dropdown_value(self, value: str, value_var: tk.StringVar, on_change) -> None:
        value_var.set(value)
        on_change(value)
        self._close_dropdown()

    def _close_dropdown(self) -> None:
        if self._dropdown_popup and self._dropdown_popup.winfo_exists():
            self._dropdown_popup.destroy()
        self._dropdown_popup = None

    def open(self) -> None:
        if self.window and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            return

        self._closing = False

        win = tk.Toplevel(self.root)
        self.window = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        win.configure(bg=self.mask_color)
        win.attributes("-transparentcolor", self.mask_color)

        x, y = self._popup_position(self.width, self.height)
        self._base_x = x
        self._base_y = y
        start_y = y + self.open_lift_px
        win.geometry(f"{self.width}x{self.height}+{x}+{start_y}")

        canvas = tk.Canvas(
            win,
            width=self.width,
            height=self.height,
            bg=self.mask_color,
            highlightthickness=0,
            bd=0,
        )
        self._canvas = canvas
        canvas.pack(fill="both", expand=True)

        self._draw_rounded_panel(canvas, 1, 1, self.width - 2, self.height - 2, self.radius)
        self._build_controls(canvas)

        canvas.create_text(
            22,
            28,
            text="Настройки",
            anchor="w",
            fill=self.title_color,
            font=("Segoe UI", 13, "bold"),
        )

        close_btn = tk.Button(
            win,
            text="✕",
            command=self.close,
            relief="flat",
            bd=0,
            bg=self.bg_color,
            activebackground=self.bg_color,
            fg=self.accent_color,
            activeforeground=self.accent_color,
            font=("Segoe UI", 12, "bold"),
            cursor="hand2",
            highlightthickness=0,
        )
        close_btn.place(x=self.width - 38, y=12, width=24, height=24)

        win.bind("<KeyPress>", self._on_key_press)
        win.bind("<Escape>", lambda _e: self.close())
        win.bind("<Destroy>", lambda _e: setattr(self, "_capturing_shortcut", False))
        self._animate_open(win)

    def close(self, animate: bool = True, notify: bool = True) -> None:
        self._persist()
        self._close_dropdown()
        if not self.window or not self.window.winfo_exists():
            self._reset_window_refs()
            if notify and callable(self.on_closed):
                self.on_closed()
            return

        if self._closing:
            return

        if animate:
            self._closing = True
            self._animate_close(self.window, notify=notify)
            return

        self._safe_destroy_window(notify=notify)

    def _popup_position(self, width: int, height: int) -> tuple[int, int]:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        if self.anchor == "top-right":
            return sw - width - self.offset_right, self.offset_bottom
        if self.anchor == "top-left":
            return self.offset_right, self.offset_bottom
        if self.anchor == "bottom-left":
            return self.offset_right, sh - height - self.offset_bottom

        # Default: bottom-right
        return sw - width - self.offset_right, sh - height - self.offset_bottom

    def _animate_open(self, win: tk.Toplevel) -> None:
        def step(i: int) -> None:
            if not win.winfo_exists():
                return
            t = min(1.0, i / max(self.open_steps, 1))
            eased = 1.0 - ((1.0 - t) * (1.0 - t))  # ease-out
            alpha = eased
            y = int((self._base_y + self.open_lift_px) - (self.open_lift_px * eased))
            win.attributes("-alpha", alpha)
            win.geometry(f"{self.width}x{self.height}+{self._base_x}+{y}")
            if i < self.open_steps:
                win.after(self.open_step_ms, step, i + 1)

        step(0)

    def _animate_close(self, win: tk.Toplevel, notify: bool = True) -> None:
        def step(i: int) -> None:
            if not win.winfo_exists():
                self._reset_window_refs()
                self._closing = False
                if notify and callable(self.on_closed):
                    self.on_closed()
                return

            t = min(1.0, i / max(self.close_steps, 1))
            eased = t * t  # ease-in
            alpha = max(0.0, 1.0 - eased)
            y = int(self._base_y + (self.close_drop_px * eased))
            win.attributes("-alpha", alpha)
            win.geometry(f"{self.width}x{self.height}+{self._base_x}+{y}")

            if i < self.close_steps:
                win.after(self.close_step_ms, step, i + 1)
            else:
                self._safe_destroy_window(notify=notify)

        step(0)

    def _safe_destroy_window(self, notify: bool = True) -> None:
        if self.window and self.window.winfo_exists():
            self.window.unbind("<KeyPress>")
            self.window.destroy()
        self._closing = False
        self._reset_window_refs()
        if notify and callable(self.on_closed):
            self.on_closed()

    def _reset_window_refs(self) -> None:
        self.window = None
        self._shortcut_button = None
        self._content_frame = None
        self._shortcut_entry = None
        self._canvas = None

    def _draw_rounded_panel(self, canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, r: int) -> None:
        canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=self.bg_color, outline="")
        canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=self.bg_color, outline="")

        canvas.create_arc(x1, y1, x1 + 2 * r, y1 + 2 * r, start=90, extent=90, fill=self.bg_color, outline="")
        canvas.create_arc(x2 - 2 * r, y1, x2, y1 + 2 * r, start=0, extent=90, fill=self.bg_color, outline="")
        canvas.create_arc(x1, y2 - 2 * r, x1 + 2 * r, y2, start=180, extent=90, fill=self.bg_color, outline="")
        canvas.create_arc(x2 - 2 * r, y2 - 2 * r, x2, y2, start=270, extent=90, fill=self.bg_color, outline="")

        # Accent strip on the top edge.
        canvas.create_rectangle(x1 + r, y1, x2 - r, y1 + 2, fill=self.accent_color, outline=self.accent_color)


class FloatingSettingsButton:
    """Always-visible bottom-corner settings button overlay."""

    def __init__(self, root: tk.Tk, on_click):
        self.root = root
        self.on_click = on_click
        self.window: tk.Toplevel | None = None

        cfg = SETTINGS_OVERLAY_BUTTON_UI
        self.anchor = cfg.get("anchor", "bottom-right")
        self.diameter = int(cfg.get("diameter", 56))
        self.offset_right = int(cfg.get("offset_right", 22))
        self.offset_bottom = int(cfg.get("offset_bottom", 24))
        self.accent_color = str(cfg.get("accent_color", "#ac2fff"))
        self.icon = str(cfg.get("icon", "⚙"))
        self.mask_color = "#ff00ff"

    def show(self) -> None:
        if self.window and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            return

        win = tk.Toplevel(self.root)
        self.window = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=self.mask_color)
        win.attributes("-transparentcolor", self.mask_color)

        x, y = self._position()
        win.geometry(f"{self.diameter}x{self.diameter}+{x}+{y}")

        canvas = tk.Canvas(
            win,
            width=self.diameter,
            height=self.diameter,
            bg=self.mask_color,
            highlightthickness=0,
            bd=0,
        )
        canvas.pack(fill="both", expand=True)

        pad = 2
        canvas.create_oval(
            pad,
            pad,
            self.diameter - pad,
            self.diameter - pad,
            fill=self.accent_color,
            outline="",
        )
        canvas.create_text(
            self.diameter // 2,
            self.diameter // 2,
            text=self.icon,
            fill="#ffffff",
            font=("Segoe UI", 17, "bold"),
        )

        canvas.bind("<Button-1>", lambda _e: self.on_click())
        canvas.bind("<Enter>", lambda _e: win.attributes("-alpha", 0.92))
        canvas.bind("<Leave>", lambda _e: win.attributes("-alpha", 1.0))

    def hide(self) -> None:
        if self.window and self.window.winfo_exists():
            self.window.destroy()
        self.window = None

    def _position(self) -> tuple[int, int]:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        if self.anchor == "top-right":
            return sw - self.diameter - self.offset_right, self.offset_bottom
        if self.anchor == "top-left":
            return self.offset_right, self.offset_bottom
        if self.anchor == "bottom-left":
            return self.offset_right, sh - self.diameter - self.offset_bottom
        return sw - self.diameter - self.offset_right, sh - self.diameter - self.offset_bottom


class SystemTrayController:
    """Windows tray icon controller with one context action: Exit."""

    def __init__(self, tooltip: str = "Arium UI", hotkey_shortcut: str = "Ctrl+Shift+Space"):
        self.tooltip = tooltip
        self.exit_requested = threading.Event()
        self.settings_requested = threading.Event()
        self.hotkey_requested = threading.Event()

        self._thread: threading.Thread | None = None
        self._hwnd = None
        self._class_name = "AriumTrayWindowClass"
        self._wndproc = None
        self._nid = None
        self._active_hotkey: tuple[int, int] | None = None
        self._pending_hotkey: tuple[int, int] | None = parse_global_hotkey(hotkey_shortcut)

    def update_hotkey(self, shortcut: str) -> None:
        self._pending_hotkey = parse_global_hotkey(shortcut)
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_APP_UPDATE_HOTKEY, 0, 0)

    def _apply_hotkey(self) -> None:
        if not self._hwnd:
            return

        if self._active_hotkey is not None:
            user32.UnregisterHotKey(self._hwnd, HOTKEY_ID_MAIN)
            self._active_hotkey = None

        if self._pending_hotkey is None:
            return

        modifiers, vk = self._pending_hotkey
        ok = user32.RegisterHotKey(self._hwnd, HOTKEY_ID_MAIN, modifiers, vk)
        if ok:
            self._active_hotkey = (modifiers, vk)
        else:
            logger.warning("Failed to register global hotkey")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_message_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run_message_loop(self) -> None:
        hinstance = kernel32.GetModuleHandleW(None)

        def wndproc(hwnd, msg, w_param, l_param):
            if msg == WM_APP_TRAY and l_param in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_context_menu(hwnd)
                return 0

            if msg == WM_HOTKEY and w_param == HOTKEY_ID_MAIN:
                self.hotkey_requested.set()
                return 0

            if msg == WM_APP_UPDATE_HOTKEY:
                self._apply_hotkey()
                return 0

            if msg == WM_COMMAND:
                command_id = w_param & 0xFFFF
                if command_id == CMD_SETTINGS:
                    self.settings_requested.set()
                    return 0
                if command_id == CMD_EXIT:
                    self.exit_requested.set()
                    self._remove_tray_icon()
                    user32.DestroyWindow(hwnd)
                    return 0

            if msg == WM_CLOSE:
                if self._active_hotkey is not None:
                    user32.UnregisterHotKey(hwnd, HOTKEY_ID_MAIN)
                    self._active_hotkey = None
                self._remove_tray_icon()
                user32.DestroyWindow(hwnd)
                return 0

            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0

            return user32.DefWindowProcW(hwnd, msg, w_param, l_param)

        self._wndproc = WNDPROC(wndproc)

        wndclass = WNDCLASSW()
        wndclass.lpfnWndProc = self._wndproc
        wndclass.hInstance = hinstance
        wndclass.lpszClassName = self._class_name
        wndclass.hIcon = user32.LoadIconW(None, IDI_APPLICATION)

        user32.RegisterClassW(ctypes.byref(wndclass))

        hwnd = user32.CreateWindowExW(
            0,
            self._class_name,
            "AriumTrayWindow",
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            hinstance,
            None,
        )
        self._hwnd = hwnd
        self._add_tray_icon(hwnd)
        self._apply_hotkey()

        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _add_tray_icon(self, hwnd) -> None:
        hicon = user32.LoadImageW(None, IDI_APPLICATION, IMAGE_ICON, 0, 0, LR_DEFAULTSIZE | LR_SHARED)

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = hicon
        nid.szTip = self.tooltip

        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self._nid = nid

    def _remove_tray_icon(self) -> None:
        if self._nid is not None:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            self._nid = None

    def _show_context_menu(self, hwnd) -> None:
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, CMD_SETTINGS, "Settings")
        user32.AppendMenuW(menu, MF_STRING, CMD_EXIT, "Exit")

        pos = POINT()
        user32.GetCursorPos(ctypes.byref(pos))
        user32.SetForegroundWindow(hwnd)

        user32.TrackPopupMenu(
            menu,
            TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RIGHTBUTTON,
            pos.x,
            pos.y,
            0,
            hwnd,
            None,
        )
        user32.DestroyMenu(menu)


class StandaloneUiShell:
    """Small bootstrap shell used for independent UI development."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()

        self.settings_popup = SettingsPopup(
            self.root,
            accent_color="#ac2fff",
            on_settings_changed=self._on_settings_changed,
            on_closed=self._on_settings_closed,
        )

        initial_hotkey = str(self.settings_popup.settings.get("launch_shortcut", "Ctrl+Shift+Space"))
        self.tray = SystemTrayController("Arium - UI shell", hotkey_shortcut=initial_hotkey)

        self.overlay_settings_button = FloatingSettingsButton(self.root, on_click=self._open_settings)
        self.ai_bridge = BackendAiBridge()
        self.voice_controller = HotkeyVoiceController(
            on_text_ready=self._on_transcribed_text,
            on_record_started=self._on_recording_started,
        )

        self._sync_overlay_button_visibility()

    def _open_settings(self) -> None:
        self.overlay_settings_button.hide()
        self.settings_popup.open()

    def _on_recording_started(self) -> None:
        # Reuse the same edge-highlight style as app startup for record-start feedback.
        EdgeGlowAnimator(self.root, color="#ac2fff", thickness=8).play(duration_ms=700, frames=22)

    def _on_transcribed_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return

        if not self.ai_bridge.started and not self.ai_bridge.start():
            logger.error("Unable to start AI pipeline; transcribed text not submitted")
            return

        ok = self.ai_bridge.submit_text(text)
        if not ok:
            logger.warning("Failed to submit transcribed text to AI queue")

    def _on_settings_closed(self) -> None:
        self._sync_overlay_button_visibility()

    def _on_settings_changed(self, _settings: dict[str, str]) -> None:
        new_hotkey = str(_settings.get("launch_shortcut", ""))
        self.tray.update_hotkey(new_hotkey)
        self._sync_overlay_button_visibility()

    def _sync_overlay_button_visibility(self) -> None:
        if self.settings_popup.window and self.settings_popup.window.winfo_exists():
            self.overlay_settings_button.hide()
            return

        mode = str(self.settings_popup.settings.get("overlay_settings_button", "Включена"))
        if mode == "Отключена":
            self.overlay_settings_button.hide()
        else:
            self.overlay_settings_button.show()

    def _on_hotkey_requested(self) -> None:
        if not self.ai_bridge.started:
            if not self.ai_bridge.start():
                logger.error("Unable to start AI pipeline for hotkey voice flow")
                return
        self.voice_controller.toggle_recording()

    def _poll_exit(self) -> None:
        if self.tray.hotkey_requested.is_set():
            self.tray.hotkey_requested.clear()
            self._on_hotkey_requested()

        if self.tray.settings_requested.is_set():
            self.tray.settings_requested.clear()
            self._open_settings()

        if self.tray.exit_requested.is_set():
            self.settings_popup.close(animate=False, notify=False)
            self.overlay_settings_button.hide()
            self.voice_controller.shutdown()
            self.ai_bridge.stop()
            self.root.quit()
            return
        self.root.after(120, self._poll_exit)

    def run(self) -> None:
        EdgeGlowAnimator(self.root).play()
        self.tray.start()
        if not self.ai_bridge.start():
            logger.warning("AI pipeline did not start at boot; will retry on first hotkey event")

        self.root.after(120, self._poll_exit)
        try:
            self.root.mainloop()
        finally:
            self.voice_controller.shutdown()
            self.ai_bridge.stop()
            self.tray.stop()


def main() -> None:
    app = StandaloneUiShell()
    app.run()


if __name__ == "__main__":
    main()
