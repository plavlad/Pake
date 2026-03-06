from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import time
import venv
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
VENV_DIR = ROOT_DIR / ".venv"
REQUIREMENTS_FILE = ROOT_DIR / "requirements.txt"
STAMP_FILE = VENV_DIR / ".requirements.sha256"
DEFAULT_HOST = os.getenv("APP_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.getenv("APP_PORT", "8000"))
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5")


def run_command(command: list[str], check: bool = True, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT_DIR,
        check=check,
        text=True,
        capture_output=capture_output,
    )


def ensure_python_version() -> None:
    if sys.version_info < (3, 10):
        raise RuntimeError("Требуется Python 3.10 или новее.")


def ensure_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Не найдено обязательное приложение: {name}")


def ensure_hf_token() -> None:
    if not os.getenv("HF_TOKEN"):
        raise RuntimeError("Переменная окружения HF_TOKEN не задана. Она обязательна для диаризации Pyannote.")


def ensure_venv() -> None:
    if VENV_DIR.exists():
        return
    print("Создаю виртуальное окружение...")
    builder = venv.EnvBuilder(with_pip=True)
    builder.create(VENV_DIR)


def venv_python() -> Path:
    return VENV_DIR / "bin" / "python"


def requirements_hash() -> str:
    return hashlib.sha256(REQUIREMENTS_FILE.read_bytes()).hexdigest()


def dependencies_installed(python_bin: Path) -> bool:
    if not STAMP_FILE.exists() or STAMP_FILE.read_text(encoding="utf-8").strip() != requirements_hash():
        return False

    probe = """
import importlib.util
import sys

modules = ["fastapi", "uvicorn", "requests", "docx", "whisperx", "multipart"]
missing = [name for name in modules if importlib.util.find_spec(name) is None]
sys.exit(0 if not missing else 1)
"""
    result = subprocess.run([str(python_bin), "-c", probe], cwd=ROOT_DIR, check=False)
    return result.returncode == 0


def install_dependencies(python_bin: Path) -> None:
    print("Устанавливаю Python-зависимости...")
    run_command([str(python_bin), "-m", "pip", "install", "--upgrade", "pip"])
    run_command([str(python_bin), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)])
    STAMP_FILE.write_text(requirements_hash(), encoding="utf-8")


def ensure_ollama_service() -> None:
    status = run_command(["ollama", "list"], check=False, capture_output=True)
    if status.returncode == 0:
        return

    print("Пытаюсь запустить Ollama...")
    subprocess.Popen(
        ["ollama", "serve"],
        cwd=ROOT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.time() + 15
    while time.time() < deadline:
        probe = run_command(["ollama", "list"], check=False, capture_output=True)
        if probe.returncode == 0:
            return
        time.sleep(1)

    raise RuntimeError("Не удалось запустить сервис Ollama. Проверьте локальную установку.")


def ensure_ollama_model() -> None:
    response = run_command(["ollama", "list"], capture_output=True)
    if OLLAMA_MODEL in response.stdout:
        return
    print(f"Скачиваю модель {OLLAMA_MODEL} через Ollama...")
    run_command(["ollama", "pull", OLLAMA_MODEL])


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def bootstrap(host: str, port: int) -> None:
    ensure_python_version()
    ensure_binary("ffmpeg")
    ensure_binary("ollama")
    ensure_hf_token()
    ensure_venv()

    python_bin = venv_python()
    if not dependencies_installed(python_bin):
        install_dependencies(python_bin)

    ensure_ollama_service()
    ensure_ollama_model()

    os.execv(
        str(python_bin),
        [
            str(python_bin),
            str(ROOT_DIR / "run.py"),
            "--serve",
            "--host",
            host,
            "--port",
            str(port),
        ],
    )


def serve(host: str, port: int) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR) + os.pathsep + env.get("PYTHONPATH", "")

    print("Сервис будет доступен по адресам:")
    print(f"  - http://127.0.0.1:{port}")
    print(f"  - http://{local_ip()}:{port}")
    print("")
    print("Подсказка: для диаризации должен быть экспортирован HF_TOKEN.")

    subprocess.run(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            host,
            "--port",
            str(port),
        ],
        cwd=ROOT_DIR,
        env=env,
        check=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smart launcher for legal audio transcriber.")
    parser.add_argument("--serve", action="store_true", help="Запуск backend-сервера внутри venv.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host for uvicorn.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port for uvicorn.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.serve:
        serve(args.host, args.port)
        return
    bootstrap(args.host, args.port)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nОстановка по запросу пользователя.")
    except Exception as exc:
        print(f"Ошибка запуска: {exc}", file=sys.stderr)
        sys.exit(1)
