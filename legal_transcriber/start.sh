#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
REQ_FILE="$APP_DIR/requirements.txt"
REQ_HASH_FILE="$VENV_DIR/.requirements.sha256"
PORT="${PORT:-8000}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5}"

echo "=== Legal Local Transcriber: smart start ==="

check_cmd() {
  local cmd="$1"
  local hint="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[ERROR] Команда '$cmd' не найдена. $hint"
    exit 1
  fi
}

check_cmd python3 "Установите Python 3.10+"
check_cmd ffmpeg "Установите ffmpeg и добавьте в PATH"
check_cmd ollama "Установите Ollama: https://ollama.com"

mkdir -p "$APP_DIR/data/uploads" "$APP_DIR/data/exports"

if [[ -f "$APP_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$APP_DIR/.env"
  set +a
  OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5}"
fi

echo "[1/6] Проверка сервиса Ollama..."
if ! ollama list >/dev/null 2>&1; then
  echo "Ollama не отвечает. Пытаюсь запустить 'ollama serve' в фоне..."
  nohup ollama serve > "$APP_DIR/data/ollama.log" 2>&1 &
  sleep 3
fi
if ! ollama list >/dev/null 2>&1; then
  echo "[ERROR] Ollama не запустилась. Проверьте логи: $APP_DIR/data/ollama.log"
  exit 1
fi

echo "[2/6] Проверка модели $OLLAMA_MODEL..."
if ! ollama list | awk 'NR>1 {print $1}' | grep -Eq "^${OLLAMA_MODEL}(:.+)?$"; then
  echo "Модель '$OLLAMA_MODEL' не найдена. Загружаю..."
  ollama pull "$OLLAMA_MODEL"
fi

echo "[3/6] Проверка виртуального окружения..."
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[4/6] Проверка зависимостей Python..."
REQ_HASH="$(sha256sum "$REQ_FILE" | awk '{print $1}')"
INSTALLED_HASH=""
if [[ -f "$REQ_HASH_FILE" ]]; then
  INSTALLED_HASH="$(cat "$REQ_HASH_FILE")"
fi

NEED_INSTALL=0
if [[ "$REQ_HASH" != "$INSTALLED_HASH" ]]; then
  NEED_INSTALL=1
elif ! python -m pip check >/dev/null 2>&1; then
  # Если окружение повреждено, пересоберем зависимости.
  NEED_INSTALL=1
fi

if [[ "$NEED_INSTALL" -eq 1 ]]; then
  python -m pip install --upgrade pip wheel setuptools
  python -m pip install -r "$REQ_FILE"
  echo "$REQ_HASH" > "$REQ_HASH_FILE"
else
  echo "Зависимости уже установлены, установка пропущена."
fi

echo "[5/6] Проверка переменных окружения..."
if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "[WARN] HF_TOKEN не задан. Диаризация потребует Hugging Face токен."
  echo "       Создайте .env (на основе .env.example) или экспортируйте HF_TOKEN."
fi

echo "[6/6] Запуск FastAPI backend..."
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
