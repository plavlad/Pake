#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  Умный скрипт установки и запуска транскрибатора
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
REQ_FILE="$SCRIPT_DIR/requirements.txt"
REQ_HASH_FILE="$VENV_DIR/.requirements_hash"
OLLAMA_MODEL="qwen2.5"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# ---- 1. Проверка Python ----
info "Проверка Python..."
if command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    fail "Python не найден. Установите Python 3.10+ и повторите попытку."
fi

PY_VERSION=$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$($PYTHON -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$($PYTHON -c 'import sys; print(sys.version_info.minor)')

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    fail "Требуется Python 3.10+, найден $PY_VERSION"
fi
ok "Python $PY_VERSION"

# ---- 2. Проверка ffmpeg ----
info "Проверка ffmpeg..."
if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg найден: $(ffmpeg -version 2>&1 | head -1)"
else
    fail "ffmpeg не найден. Установите: sudo apt install ffmpeg (Linux) / brew install ffmpeg (macOS)"
fi

# ---- 3. Проверка Ollama ----
info "Проверка Ollama..."
if command -v ollama &>/dev/null; then
    ok "Ollama найден"
else
    fail "Ollama не найден. Установите: https://ollama.ai/download"
fi

# Убедимся, что сервер Ollama запущен
if ! curl -s http://localhost:11434/api/tags &>/dev/null; then
    info "Запуск сервера Ollama..."
    ollama serve &>/dev/null &
    sleep 3
    if ! curl -s http://localhost:11434/api/tags &>/dev/null; then
        fail "Не удалось запустить сервер Ollama"
    fi
fi
ok "Сервер Ollama работает"

# ---- 4. Проверка модели Qwen 2.5 ----
info "Проверка модели $OLLAMA_MODEL..."
if ollama list 2>/dev/null | grep -q "$OLLAMA_MODEL"; then
    ok "Модель $OLLAMA_MODEL уже скачана"
else
    info "Скачивание модели $OLLAMA_MODEL (это может занять время)..."
    ollama pull "$OLLAMA_MODEL" || fail "Не удалось скачать модель $OLLAMA_MODEL"
    ok "Модель $OLLAMA_MODEL скачана"
fi

# ---- 5. Виртуальное окружение ----
info "Проверка виртуального окружения..."
if [ ! -d "$VENV_DIR" ]; then
    info "Создание виртуального окружения..."
    $PYTHON -m venv "$VENV_DIR" || fail "Не удалось создать venv"
    ok "Виртуальное окружение создано"
else
    ok "Виртуальное окружение существует"
fi

source "$VENV_DIR/bin/activate"

# ---- 6. Установка зависимостей (с кешированием) ----
info "Проверка зависимостей..."
CURRENT_HASH=$(md5sum "$REQ_FILE" 2>/dev/null | awk '{print $1}' || md5 -q "$REQ_FILE" 2>/dev/null || echo "none")

NEED_INSTALL=false
if [ ! -f "$REQ_HASH_FILE" ]; then
    NEED_INSTALL=true
elif [ "$(cat "$REQ_HASH_FILE")" != "$CURRENT_HASH" ]; then
    NEED_INSTALL=true
fi

if [ "$NEED_INSTALL" = true ]; then
    info "Установка зависимостей из requirements.txt..."
    pip install --upgrade pip setuptools wheel -q
    pip install -r "$REQ_FILE" || fail "Ошибка установки зависимостей"
    echo "$CURRENT_HASH" > "$REQ_HASH_FILE"
    ok "Зависимости установлены"
else
    ok "Зависимости актуальны, пропуск установки"
fi

# ---- 7. Создание директории для загрузок ----
mkdir -p "$SCRIPT_DIR/uploads"

# ---- 8. Запуск сервера ----
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Транскрибатор запускается на http://$HOST:$PORT  ${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo ""

cd "$SCRIPT_DIR"
exec "$VENV_DIR/bin/python" -m uvicorn app.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --reload \
    --log-level info
