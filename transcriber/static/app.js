/* ═══════════════════════════════════════════════════
   Транскрибатор — клиентская логика
   ═══════════════════════════════════════════════════ */

const API = window.location.origin;
const POLL_INTERVAL = 2000;

const SPEAKER_COLORS = [
    '#6366f1', '#22c55e', '#f59e0b', '#ef4444',
    '#06b6d4', '#ec4899', '#8b5cf6', '#14b8a6',
    '#f97316', '#64748b', '#a855f7', '#84cc16',
];

let currentTaskId = null;
let pollTimer = null;
let resultData = null;
let speakerMap = {};

// ── DOM-элементы ─────────────────────────────────

const $uploadZone = document.getElementById('uploadZone');
const $fileInput = document.getElementById('fileInput');
const $progressSection = document.getElementById('progressSection');
const $progressFilename = document.getElementById('progressFilename');
const $progressFill = document.getElementById('progressFill');
const $progressStage = document.getElementById('progressStage');
const $resultSection = document.getElementById('resultSection');
const $speakerList = document.getElementById('speakerList');
const $resultText = document.getElementById('resultText');
const $statusDot = document.getElementById('statusDot');
const $statusText = document.getElementById('statusText');

// ── Инициализация ────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    checkHealth();
    setupDragDrop();
    setupFileInput();
});


async function checkHealth() {
    try {
        const resp = await fetch(`${API}/api/health`);
        const data = await resp.json();
        $statusDot.className = 'status-dot ' + (data.ollama ? 'ok' : 'err');
        $statusText.textContent = data.ollama ? 'Системы готовы' : 'Ollama недоступна';
    } catch {
        $statusDot.className = 'status-dot err';
        $statusText.textContent = 'Сервер недоступен';
    }
}


// ── Drag & Drop ──────────────────────────────────

function setupDragDrop() {
    ['dragenter', 'dragover'].forEach(evt => {
        $uploadZone.addEventListener(evt, e => {
            e.preventDefault();
            $uploadZone.classList.add('dragover');
        });
    });

    ['dragleave', 'drop'].forEach(evt => {
        $uploadZone.addEventListener(evt, e => {
            e.preventDefault();
            $uploadZone.classList.remove('dragover');
        });
    });

    $uploadZone.addEventListener('drop', e => {
        const files = e.dataTransfer.files;
        if (files.length > 0) uploadFile(files[0]);
    });
}


function setupFileInput() {
    $fileInput.addEventListener('change', () => {
        if ($fileInput.files.length > 0) {
            uploadFile($fileInput.files[0]);
            $fileInput.value = '';
        }
    });
}


// ── Загрузка файла ───────────────────────────────

async function uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);

    showProgress(file.name);

    try {
        const resp = await fetch(`${API}/api/upload`, { method: 'POST', body: formData });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Ошибка загрузки');
        }

        const data = await resp.json();
        currentTaskId = data.task_id;
        startPolling();
    } catch (err) {
        showError(err.message);
    }
}


// ── Polling статуса ──────────────────────────────

function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(pollStatus, POLL_INTERVAL);
    pollStatus();
}


async function pollStatus() {
    if (!currentTaskId) return;

    try {
        const resp = await fetch(`${API}/api/tasks/${currentTaskId}`);
        const task = await resp.json();

        updateProgress(task);

        if (task.stage === 'done') {
            clearInterval(pollTimer);
            pollTimer = null;
            await loadResult();
        } else if (task.stage === 'error') {
            clearInterval(pollTimer);
            pollTimer = null;
            showError(task.error || 'Неизвестная ошибка');
        }
    } catch (err) {
        console.error('Ошибка polling:', err);
    }
}


// ── UI: прогресс ─────────────────────────────────

function showProgress(filename) {
    $resultSection.classList.remove('active');
    $progressSection.classList.add('active');
    $progressFilename.textContent = filename;
    $progressFill.style.width = '0%';
    $progressStage.className = 'progress-stage';
    $progressStage.innerHTML = '<div class="spinner"></div><span>Подготовка...</span>';
}

function updateProgress(task) {
    const pct = Math.round(task.progress * 100);
    $progressFill.style.width = pct + '%';
    $progressStage.className = 'progress-stage';
    $progressStage.innerHTML = `<div class="spinner"></div><span>${task.stage_label} (${pct}%)</span>`;
}

function showError(msg) {
    $progressStage.className = 'progress-stage error';
    $progressStage.innerHTML = `<span>Ошибка: ${escapeHtml(msg)}</span>`;
}


// ── Загрузка результата ──────────────────────────

async function loadResult() {
    try {
        const resp = await fetch(`${API}/api/tasks/${currentTaskId}/result`);
        resultData = await resp.json();

        $progressStage.className = 'progress-stage done';
        $progressStage.innerHTML = '<span>Обработка завершена</span>';

        speakerMap = {};
        resultData.speakers.forEach(s => { speakerMap[s] = s; });

        renderSpeakers();
        renderText();

        $resultSection.classList.add('active');
        $resultSection.scrollIntoView({ behavior: 'smooth' });
    } catch (err) {
        showError('Не удалось загрузить результат');
    }
}


// ── Панель спикеров ──────────────────────────────

function renderSpeakers() {
    $speakerList.innerHTML = '';

    resultData.speakers.forEach((speaker, idx) => {
        const color = SPEAKER_COLORS[idx % SPEAKER_COLORS.length];

        const item = document.createElement('div');
        item.className = 'speaker-item';
        item.innerHTML = `
            <span class="speaker-color" style="background:${color}"></span>
            <span class="speaker-original">${escapeHtml(speaker)}</span>
            <span class="speaker-arrow">→</span>
            <input type="text"
                   value="${escapeHtml(speakerMap[speaker] || speaker)}"
                   data-original="${escapeHtml(speaker)}"
                   placeholder="Введите имя">
        `;

        const input = item.querySelector('input');
        input.addEventListener('input', () => {
            speakerMap[speaker] = input.value || speaker;
            renderText();
        });

        $speakerList.appendChild(item);
    });
}


// ── Отображение текста ───────────────────────────

function renderText() {
    if (!resultData) return;

    let text = resultData.text;

    for (const [original, replacement] of Object.entries(speakerMap)) {
        if (original !== replacement && replacement) {
            text = text.replaceAll(original, replacement);
        }
    }

    const lines = text.split('\n');
    $resultText.innerHTML = '';

    lines.forEach(line => {
        const div = document.createElement('div');
        div.className = 'result-line';

        if (!line.trim()) {
            div.innerHTML = '<div class="empty-line"></div>';
            $resultText.appendChild(div);
            return;
        }

        const match = line.match(/^(\[[\d:]+\s*-\s*[\d:]+\])\s*(.+?):\s*(.*)/);
        if (match) {
            const timecode = match[1];
            const speaker = match[2];
            const speech = match[3];

            const color = getSpeakerColor(speaker);
            div.innerHTML = `
                <span class="timecode">${escapeHtml(timecode)}</span>
                <span class="speaker-label" style="color:${color}">${escapeHtml(speaker)}:</span>
                <span>${escapeHtml(speech)}</span>
            `;
        } else {
            div.textContent = line;
        }

        $resultText.appendChild(div);
    });
}


function getSpeakerColor(displayName) {
    for (const [original, mapped] of Object.entries(speakerMap)) {
        if (mapped === displayName || original === displayName) {
            const idx = resultData.speakers.indexOf(original);
            if (idx >= 0) return SPEAKER_COLORS[idx % SPEAKER_COLORS.length];
        }
    }
    return SPEAKER_COLORS[0];
}


// ── Экспорт ──────────────────────────────────────

async function exportResult(format) {
    if (!currentTaskId || !resultData) return;

    try {
        const resp = await fetch(`${API}/api/export`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                task_id: currentTaskId,
                format: format,
                speaker_map: speakerMap,
                title: 'Протокол',
            }),
        });

        if (!resp.ok) throw new Error('Ошибка экспорта');

        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;

        const cd = resp.headers.get('Content-Disposition');
        const fnMatch = cd && cd.match(/filename="(.+?)"/);
        a.download = fnMatch ? fnMatch[1] : `result.${format}`;

        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);

        showToast('Файл скачан', 'success');
    } catch (err) {
        showToast(err.message, 'error');
    }
}


// ── Утилиты ──────────────────────────────────────

function escapeHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}


function showToast(msg, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3500);
}
