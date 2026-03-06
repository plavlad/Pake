#!/usr/bin/env bash
# ============================================================
# start.sh — Умный скрипт запуска системы транскрибации
# Проверяет и устанавливает все зависимости, затем стартует сервер
# ============================================================

set -euo pipefail

# --- Цвета ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC}  $1"; }
log_ok()   { echo -e "${GREEN}[OK]${NC}    $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
log_step() { echo -e "\n${BOLD}${CYAN}══ $1 ══${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║   Система транскрибации юридических аудиозаписей     ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${NC}"
echo ""

# ============================================================
# 1. Проверка Python
# ============================================================
log_step "Проверка Python"

if ! command -v python3 &>/dev/null; then
    log_err "Python3 не найден. Установите Python 3.10 или выше и повторите."
fi

PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [[ "$PYTHON_MAJOR" -lt 3 || ("$PYTHON_MAJOR" -eq 3 && "$PYTHON_MINOR" -lt 10) ]]; then
    log_err "Требуется Python 3.10+. Найден: $PYTHON_VER"
fi
log_ok "Python $PYTHON_VER"

# ============================================================
# 2. Проверка ffmpeg
# ============================================================
log_step "Проверка ffmpeg"

if ! command -v ffmpeg &>/dev/null; then
    log_warn "ffmpeg не найден. Пытаемся установить автоматически..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -q && sudo apt-get install -y ffmpeg
    elif command -v brew &>/dev/null; then
        brew install ffmpeg
    elif command -v yum &>/dev/null; then
        sudo yum install -y ffmpeg
    else
        log_err "Не удалось установить ffmpeg. Установите вручную: https://ffmpeg.org/download.html"
    fi
fi
log_ok "ffmpeg $(ffmpeg -version 2>&1 | head -n1 | awk '{print $3}')"

# ============================================================
# 3. Проверка Ollama
# ============================================================
log_step "Проверка Ollama"

if ! command -v ollama &>/dev/null; then
    log_warn "Ollama не найдена. Устанавливаем..."
    curl -fsSL https://ollama.com/install.sh | sh
    log_ok "Ollama установлена."
else
    log_ok "Ollama $(ollama --version 2>/dev/null || echo 'найдена')"
fi

# Запускаем Ollama-сервис если не запущен
if ! curl -s --max-time 3 http://localhost:11434/api/tags &>/dev/null; then
    log_info "Запускаем Ollama в фоновом режиме..."
    nohup ollama serve > /tmp/ollama_serve.log 2>&1 &
    OLLAMA_PID=$!
    echo $OLLAMA_PID > /tmp/ollama.pid
    # Ждём пока сервис поднимется
    for i in {1..15}; do
        sleep 1
        if curl -s --max-time 2 http://localhost:11434/api/tags &>/dev/null; then
            break
        fi
        if [[ $i -eq 15 ]]; then
            log_err "Ollama не запустилась за 15 секунд. Проверьте /tmp/ollama_serve.log"
        fi
    done
    log_ok "Ollama запущена (PID: $OLLAMA_PID)"
else
    log_ok "Ollama уже запущена"
fi

# ============================================================
# 4. Проверка и загрузка модели Qwen 2.5
# ============================================================
log_step "Проверка модели Qwen 2.5"

QWEN_MODEL="${OLLAMA_MODEL:-qwen2.5}"

if ! ollama list 2>/dev/null | grep -q "^${QWEN_MODEL}"; then
    log_warn "Модель ${QWEN_MODEL} не найдена. Скачиваем (~4-8 GB, это займёт время)..."
    ollama pull "$QWEN_MODEL"
    log_ok "Модель ${QWEN_MODEL} загружена."
else
    log_ok "Модель ${QWEN_MODEL} уже загружена"
fi

# ============================================================
# 5. Проверка HF_TOKEN (для диаризации)
# ============================================================
log_step "Проверка токена HuggingFace"

if [[ -z "${HF_TOKEN:-}" ]]; then
    echo ""
    log_warn "Переменная HF_TOKEN не установлена!"
    log_warn "Диаризация спикеров (разделение по голосам) НЕДОСТУПНА без токена."
    log_warn ""
    log_warn "Чтобы включить диаризацию:"
    log_warn "  1. Зарегистрируйтесь на https://huggingface.co"
    log_warn "  2. Получите токен: https://huggingface.co/settings/tokens"
    log_warn "  3. Примите условия моделей:"
    log_warn "     - https://huggingface.co/pyannote/speaker-diarization-3.1"
    log_warn "     - https://huggingface.co/pyannote/segmentation-3.0"
    log_warn "  4. Запустите: export HF_TOKEN=hf_ваш_токен && ./start.sh"
    echo ""
    log_warn "Продолжаем БЕЗ диаризации (все реплики будут помечены как SPEAKER_00)."
else
    log_ok "HF_TOKEN установлен — диаризация включена"
fi

# ============================================================
# 6. Виртуальное окружение Python
# ============================================================
log_step "Виртуальное окружение"

VENV_DIR="$SCRIPT_DIR/venv"

if [[ ! -d "$VENV_DIR" ]]; then
    log_info "Создаём виртуальное окружение..."
    python3 -m venv "$VENV_DIR"
    log_ok "Виртуальное окружение создано: $VENV_DIR"
else
    log_ok "Виртуальное окружение найдено"
fi

# Активируем
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
log_ok "venv активирован"

# ============================================================
# 7. Установка зависимостей
# ============================================================
log_step "Зависимости Python"

STAMP_FILE="$VENV_DIR/.deps_installed_stamp"
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"

# Переустанавливаем если requirements.txt новее маркера
if [[ ! -f "$STAMP_FILE" ]] || [[ "$REQUIREMENTS_FILE" -nt "$STAMP_FILE" ]]; then
    log_info "Устанавливаем зависимости (первый запуск займёт 5-15 минут)..."

    pip install --upgrade pip wheel setuptools --quiet

    # Определяем наличие NVIDIA GPU
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null 2>&1; then
        log_info "NVIDIA GPU обнаружена — устанавливаем CUDA-версию PyTorch..."
        pip install torch torchvision torchaudio \
            --index-url https://download.pytorch.org/whl/cu121 \
            --quiet
    else
        log_warn "GPU не обнаружена — устанавливаем CPU-версию PyTorch (медленнее)."
        pip install torch torchvision torchaudio --quiet
    fi

    # WhisperX устанавливаем отдельно (сложные зависимости)
    log_info "Устанавливаем WhisperX..."
    pip install git+https://github.com/m-bain/whisperX.git --quiet

    # Остальные зависимости
    log_info "Устанавливаем остальные зависимости..."
    pip install -r "$REQUIREMENTS_FILE" --quiet

    touch "$STAMP_FILE"
    log_ok "Все зависимости установлены."
else
    log_ok "Зависимости уже установлены (requirements.txt не изменился)"
fi

# ============================================================
# 8. Создаём рабочие директории
# ============================================================
mkdir -p "$SCRIPT_DIR/uploads" "$SCRIPT_DIR/results"

# ============================================================
# 9. Запуск сервера
# ============================================================
log_step "Запуск Backend-сервера"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"

# Определяем локальный IP для отображения
if command -v hostname &>/dev/null; then
    LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || hostname -i 2>/dev/null || echo "127.0.0.1")
else
    LOCAL_IP="127.0.0.1"
fi

echo ""
echo -e "${BOLD}${GREEN}  Сервер запускается...${NC}"
echo ""
echo -e "  ${CYAN}Локальный доступ:${NC}   http://localhost:${PORT}"
echo -e "  ${CYAN}Сетевой доступ:${NC}    http://${LOCAL_IP}:${PORT}"
echo ""
echo -e "  ${YELLOW}Для остановки нажмите Ctrl+C${NC}"
echo ""

# Экспортируем переменные окружения для бэкенда
export HF_TOKEN="${HF_TOKEN:-}"
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5}"
export WHISPER_MODEL="${WHISPER_MODEL:-large-v3}"

cd "$SCRIPT_DIR"
exec uvicorn backend.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --log-level info
