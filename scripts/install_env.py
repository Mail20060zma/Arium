#!/usr/bin/env python3
"""Arium environment installer.

Creates a Python 3.11 virtual environment and installs dependencies,
including a selected torch profile (CPU or CUDA).
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
REQ_DIR = ROOT_DIR / "requirements"

BASE_REQUIREMENTS = REQ_DIR / "base.txt"
TORCH_REQUIREMENTS = {
    "cpu": REQ_DIR / "torch-cpu.txt",
    "cu121": REQ_DIR / "torch-cu121.txt",
    "cu124": REQ_DIR / "torch-cu124.txt",
}

TORCH_INDEX_URL = {
    "cpu": "https://download.pytorch.org/whl/cpu",
    "cu121": "https://download.pytorch.org/whl/cu121",
    "cu124": "https://download.pytorch.org/whl/cu124",
}


@dataclass
class InstallConfig:
    profile: str
    python_mode: str
    venv_dir: Path
    non_interactive: bool


def run_command(command: Sequence[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print(f"[cmd] {' '.join(command)}")
    subprocess.run(command, check=True, cwd=cwd, env=env)


def run_capture(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _probe_python(cmd: Sequence[str]) -> tuple[str, str] | None:
    probe = run_capture([*cmd, "-c", "import sys; print(sys.executable); print(f'{sys.version_info.major}.{sys.version_info.minor}')"])
    if probe.returncode != 0:
        return None

    lines = [line.strip() for line in probe.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None

    return lines[0], lines[1]


def find_python_311() -> str | None:
    candidates: list[list[str]] = []
    if platform.system() == "Windows":
        candidates.append(["py", "-3.11"])

    candidates.extend([
        ["python3.11"],
        ["python3"],
        ["python"],
    ])

    for cmd in candidates:
        probe = _probe_python(cmd)
        if not probe:
            continue

        executable, version = probe
        if version == "3.11":
            return executable

    return None


def detect_gpu_profile() -> str:
    if platform.system() == "Darwin":
        return "cpu"

    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return "cpu"

    gpu_check = run_capture([nvidia_smi, "--query-gpu=name", "--format=csv,noheader"])
    if gpu_check.returncode != 0:
        return "cpu"

    return "cu121"


def try_auto_install_python() -> None:
    system_name = platform.system()

    if system_name == "Windows":
        winget = shutil.which("winget")
        if not winget:
            raise RuntimeError("winget не найден. Установите Python 3.11 вручную с python.org.")
        run_command([
            winget,
            "install",
            "-e",
            "--id",
            "Python.Python.3.11",
            "--accept-source-agreements",
            "--accept-package-agreements",
        ])
        return

    if system_name == "Darwin":
        brew = shutil.which("brew")
        if not brew:
            raise RuntimeError("Homebrew не найден. Установите Python 3.11: brew install python@3.11")
        run_command([brew, "install", "python@3.11"])
        return

    apt_get = shutil.which("apt-get")
    if apt_get:
        run_command(["sudo", apt_get, "update"])
        run_command(["sudo", apt_get, "install", "-y", "python3.11", "python3.11-venv"])
        return

    dnf = shutil.which("dnf")
    if dnf:
        run_command(["sudo", dnf, "install", "-y", "python3.11", "python3.11-devel"])
        return

    raise RuntimeError("Не найден поддерживаемый менеджер пакетов. Установите Python 3.11 вручную.")


def resolve_python(config: InstallConfig) -> str:
    python_exe = find_python_311()
    if python_exe:
        return python_exe

    if config.python_mode == "check-only":
        raise RuntimeError(
            "Python 3.11 не найден. Установите его вручную и запустите установщик снова."
        )

    print("[info] Python 3.11 не найден, запускаю автоустановку...")
    try_auto_install_python()

    python_exe = find_python_311()
    if not python_exe:
        raise RuntimeError("Python 3.11 по-прежнему не найден после автоустановки.")

    return python_exe


def prompt_choice(prompt: str, options: dict[str, str], *, default: str) -> str:
    print(prompt)
    for key, value in options.items():
        marker = " (default)" if key == default else ""
        print(f"  {key}: {value}{marker}")

    answer = input("Ваш выбор: ").strip().lower()
    if not answer:
        return default
    if answer not in options:
        print(f"[warn] Неизвестный выбор '{answer}', используется значение по умолчанию: {default}")
        return default
    return answer


def venv_python_path(venv_dir: Path) -> Path:
    if platform.system() == "Windows":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def install_dependencies(venv_py: Path, profile: str) -> None:
    run_command([str(venv_py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
    run_command([str(venv_py), "-m", "pip", "install", "-r", str(BASE_REQUIREMENTS)])

    torch_req = TORCH_REQUIREMENTS[profile]
    torch_index = TORCH_INDEX_URL[profile]
    run_command([
        str(venv_py),
        "-m",
        "pip",
        "install",
        "-r",
        str(torch_req),
        "--index-url",
        torch_index,
        "--extra-index-url",
        "https://pypi.org/simple",
    ])


def validate_install(venv_py: Path, profile: str) -> None:
    check_script = (
        "import torch, importlib; "
        "mods=['openai','requests','speech_recognition','vosk','pygame','soundfile']; "
        "missing=[m for m in mods if importlib.util.find_spec(m) is None]; "
        "print(f'torch={torch.__version__}'); "
        "print(f'cuda_available={torch.cuda.is_available()}'); "
        "print('missing=' + ','.join(missing) if missing else 'missing=none'); "
    )
    run_command([str(venv_py), "-c", check_script])

    if profile.startswith("cu"):
        cuda_check = run_capture([str(venv_py), "-c", "import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)"])
        if cuda_check.returncode != 0:
            raise RuntimeError("Torch CUDA установлен, но torch.cuda.is_available() == False")


def print_finish_info(venv_dir: Path) -> None:
    if platform.system() == "Windows":
        activate_cmd = f"{venv_dir}\\Scripts\\activate"
    else:
        activate_cmd = f"source {venv_dir}/bin/activate"

    print("\n[ok] Установка завершена")
    print(f"[info] Активация окружения: {activate_cmd}")
    print("[info] Запуск проекта: python app/main.py")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Arium installer")
    parser.add_argument("--profile", choices=["auto", "cpu", "cu121", "cu124"], default="auto")
    parser.add_argument("--python-mode", choices=["check-only", "auto-install"], default="check-only")
    parser.add_argument("--venv", default=".venv")
    parser.add_argument("--non-interactive", action="store_true")
    return parser.parse_args()


def select_profile(initial_profile: str, non_interactive: bool) -> str:
    if initial_profile != "auto":
        return initial_profile

    detected = detect_gpu_profile()
    if non_interactive:
        print(f"[info] Автовыбор профиля torch: {detected}")
        return detected

    return prompt_choice(
        "Выберите профиль torch:",
        {
            "auto": "Автоопределение (рекомендовано)",
            "cpu": "CPU-only",
            "cu121": "CUDA 12.1",
            "cu124": "CUDA 12.4",
        },
        default="auto",
    )


def select_python_mode(initial_mode: str, non_interactive: bool) -> str:
    if non_interactive:
        return initial_mode

    return prompt_choice(
        "Выберите режим Python 3.11:",
        {
            "check-only": "Только проверка (без автоустановки)",
            "auto-install": "Автоустановка Python 3.11 при отсутствии",
        },
        default=initial_mode,
    )


def main() -> int:
    args = parse_args()

    profile = select_profile(args.profile, args.non_interactive)
    if profile == "auto":
        profile = detect_gpu_profile()

    python_mode = select_python_mode(args.python_mode, args.non_interactive)

    config = InstallConfig(
        profile=profile,
        python_mode=python_mode,
        venv_dir=ROOT_DIR / args.venv,
        non_interactive=args.non_interactive,
    )

    print(f"[info] Профиль torch: {config.profile}")
    print(f"[info] Режим Python: {config.python_mode}")
    print(f"[info] Папка venv: {config.venv_dir}")

    python_exe = resolve_python(config)
    print(f"[info] Использую Python: {python_exe}")

    run_command([python_exe, "-m", "venv", str(config.venv_dir)])

    venv_py = venv_python_path(config.venv_dir)
    if not venv_py.exists():
        raise RuntimeError(f"Не найден python в venv: {venv_py}")

    try:
        install_dependencies(venv_py, config.profile)
    except Exception:
        if config.profile != "cpu":
            print("[warn] Ошибка установки CUDA-профиля, пробую fallback на CPU...")
            install_dependencies(venv_py, "cpu")
            config.profile = "cpu"
        else:
            raise

    validate_install(venv_py, config.profile)
    print_finish_info(config.venv_dir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[error] Установка прервана пользователем")
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}")
        raise SystemExit(1)
