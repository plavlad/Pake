/**
 * app.js — Фронтенд юридического транскрибатора
 *
 * Логика:
 * - Drag & Drop / клик для загрузки файла
 * - POST /api/upload → получаем task_id
 * - Polling GET /api/status/{task_id} каждые 2 секунды
 * - Отображение результата с реактивным переименованием спикеров
 * - Экспорт (GET /api/export/{task_id}?format=txt|docx)
 */

'use strict';

/* ── Состояние приложения ─────────────────────────────── */
const state = {
  taskId:       null,
  pollTimer:    null,
  result:       null,      // {segments, speakers, llm_text, raw_text}
  speakerNames: {},        // { "SPEAKER_00": "Судья Иванов", ... }
  activeTab:    'llm',     // 'llm' | 'raw'
};

/* Цвета спикеров (соответствуют CSS-переменным) */
const SPEAKER_COLORS = [
  '#4f7ef8', '#e05252', '#2dd4a0', '#f5a623',
  '#b06ef8', '#f87c4f', '#4fcef8', '#f84fa3',
];

/* ── Элементы DOM ─────────────────────────────────────── */
const $ = id => document.getElementById(id);

const dropzone          = $('dropzone');
const fileInput         = $('file-input');
const fileInfo          = $('file-info');
const fileNameDisplay   = $('file-name-display');
const progressSection   = $('progress-section');
const speakersSection   = $('speakers-section');
const resultSection     = $('result-section');
const progressBar       = $('progress-bar');
const progressStage     = $('progress-stage');
const progressPct       = $('progress-pct');
const errorBox          = $('error-box');
const speakerList       = $('speaker-list');
const transcriptContainer = $('transcript-container');

/* ── Этапы прогресса ──────────────────────────────────── */
const STAGES = [
  { id: 'step-upload',     pctRange: [0,  10],  conn: 'conn-1' },
  { id: 'step-transcribe', pctRange: [10, 55],  conn: 'conn-2' },
  { id: 'step-diarize',    pctRange: [55, 76],  conn: 'conn-3' },
  { id: 'step-llm',        pctRange: [76, 98],  conn: 'conn-4' },
  { id: 'step-done',       pctRange: [98, 100], conn: null     },
];

/* ============================================================
   DRAG & DROP
   ============================================================ */

dropzone.addEventListener('dragover', e => {
  e.preventDefault();
  dropzone.classList.add('drag-over');
});

dropzone.addEventListener('dragleave', () => {
  dropzone.classList.remove('drag-over');
});

dropzone.addEventListener('drop', e => {
  e.preventDefault();
  dropzone.classList.remove('drag-over');
  const file = e.dataTransfer?.files?.[0];
  if (file) handleFile(file);
});

dropzone.addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ' ') fileInput.click();
});

fileInput.addEventListener('change', () => {
  if (fileInput.files?.[0]) handleFile(fileInput.files[0]);
});

/* ============================================================
   ЗАГРУЗКА ФАЙЛА
   ============================================================ */

function handleFile(file) {
  const ALLOWED = /\.(mp3|wav|mp4|m4a|ogg|flac|opus|avi|mkv|webm)$/i;
  if (!ALLOWED.test(file.name)) {
    showToast('Неподдерживаемый формат файла', 'error');
    return;
  }
  if (file.size > 2 * 1024 * 1024 * 1024) {
    showToast('Файл превышает 2 ГБ', 'error');
    return;
  }

  // Показываем имя файла
  fileNameDisplay.textContent = file.name;
  fileInfo.classList.add('visible');

  resetProgress();
  showSection('progress');

  uploadFile(file);
}

async function uploadFile(file) {
  const formData = new FormData();
  formData.append('file', file);

  updateStep(0, 'active');
  updateProgress('Загрузка файла на сервер...', 3);

  try {
    const res = await fetch('/api/upload', { method: 'POST', body: formData });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(err.detail || 'Ошибка загрузки');
    }
    const data = await res.json();
    state.taskId = data.task_id;

    updateStep(0, 'done');
    updateProgress('Файл загружен, начинаем обработку...', 7);
    startPolling();

  } catch (err) {
    showError(`Ошибка загрузки: ${err.message}`);
  }
}

/* ============================================================
   POLLING СТАТУСА
   ============================================================ */

function startPolling() {
  clearPolling();
  state.pollTimer = setInterval(pollStatus, 2000);
}

function clearPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function pollStatus() {
  if (!state.taskId) return;

  try {
    const res = await fetch(`/api/status/${state.taskId}`);
    if (!res.ok) return;
    const data = await res.json();

    updateProgress(data.stage, data.progress);
    updateStepsFromProgress(data.progress);

    if (data.status === 'completed') {
      clearPolling();
      handleResult(data.result);
    } else if (data.status === 'error') {
      clearPolling();
      showError(data.error || 'Неизвестная ошибка обработки');
    }
  } catch (err) {
    console.warn('Ошибка опроса статуса:', err);
  }
}

/* ── Обновление визуальных шагов по проценту ─── */
function updateStepsFromProgress(pct) {
  STAGES.forEach((stage, idx) => {
    const el   = $(stage.id);
    const conn = stage.conn ? $(stage.conn) : null;

    if (pct >= stage.pctRange[1]) {
      el.className   = 'step done';
      if (conn) conn.className = 'step-connector done';
    } else if (pct >= stage.pctRange[0]) {
      el.className   = 'step active';
      if (conn) conn.className = 'step-connector';
    } else {
      el.className   = 'step';
      if (conn) conn.className = 'step-connector';
    }
  });
}

/* ============================================================
   ОБРАБОТКА РЕЗУЛЬТАТА
   ============================================================ */

function handleResult(result) {
  if (!result) { showError('Сервер вернул пустой результат'); return; }

  state.result = result;

  // Инициализируем имена спикеров (по умолчанию = ID)
  state.speakerNames = {};
  (result.speakers || []).forEach(sp => { state.speakerNames[sp] = sp; });

  updateProgress('Обработка завершена', 100);
  updateStepsFromProgress(100);

  // Показываем блоки
  showSection('speakers');
  showSection('result');

  // Рендерим панель спикеров
  renderSpeakerPanel(result.speakers || []);

  // Рендерим транскрипт
  renderTranscript();

  showToast('Обработка завершена успешно!', 'success');
}

/* ============================================================
   ПАНЕЛЬ СПИКЕРОВ
   ============================================================ */

function renderSpeakerPanel(speakers) {
  speakerList.innerHTML = '';

  speakers.forEach((spId, idx) => {
    const color = SPEAKER_COLORS[idx % SPEAKER_COLORS.length];

    const item = document.createElement('div');
    item.className = 'speaker-item';
    item.innerHTML = `
      <div class="speaker-badge" style="background:${color}"></div>
      <span class="speaker-id">${spId}</span>
      <input
        class="speaker-name-input"
        type="text"
        value="${escapeHtml(spId)}"
        placeholder="Введите имя..."
        data-speaker-id="${escapeHtml(spId)}"
        autocomplete="off"
      />
    `;

    const input = item.querySelector('input');
    input.addEventListener('input', onSpeakerRename);

    speakerList.appendChild(item);
  });
}

function onSpeakerRename(e) {
  const spId    = e.target.dataset.speakerId;
  const newName = e.target.value.trim() || spId;
  state.speakerNames[spId] = newName;
  renderTranscript();
}

/* ============================================================
   РЕНДЕР ТРАНСКРИПТА
   ============================================================ */

function switchTab(tab) {
  state.activeTab = tab;
  $('tab-llm').classList.toggle('active', tab === 'llm');
  $('tab-raw').classList.toggle('active', tab === 'raw');
  renderTranscript();
}

function renderTranscript() {
  if (!state.result) return;

  const segments = getActiveSegments();

  if (!segments || segments.length === 0) {
    transcriptContainer.innerHTML =
      '<div class="empty-transcript">Нет данных для отображения</div>';
    return;
  }

  const frag = document.createDocumentFragment();
  let prevSpeaker = null;

  segments.forEach((seg, idx) => {
    const rawSpId    = seg.speaker || seg.speakerId || 'SPEAKER_00';
    const spIndex    = getSpeakerIndex(rawSpId);
    const spName     = state.speakerNames[rawSpId] || rawSpId;
    const isNewBlock = rawSpId !== prevSpeaker;

    const div = document.createElement('div');
    div.className = 'segment' + (isNewBlock && idx > 0 ? ' speaker-change' : '');

    div.innerHTML = `
      <div class="segment-meta">
        <span class="segment-timestamp">${escapeHtml(seg.timestamp || '')}</span>
      </div>
      <div class="segment-body sp-bg-${spIndex % 8}">
        <div class="segment-speaker sp-color-${spIndex % 8}">${escapeHtml(spName)}</div>
        <div class="segment-text">${escapeHtml(seg.text || '')}</div>
      </div>
    `;

    frag.appendChild(div);
    prevSpeaker = rawSpId;
  });

  transcriptContainer.innerHTML = '';
  transcriptContainer.appendChild(frag);
}

/**
 * Возвращает сегменты для активной вкладки.
 * raw-вкладка: парсим raw_text обратно в массив.
 */
function getActiveSegments() {
  if (!state.result) return [];

  if (state.activeTab === 'llm') {
    return state.result.segments || [];
  }

  // Парсим сырой текст
  const rawText = state.result.raw_text || '';
  return parseTranscriptText(rawText);
}

function parseTranscriptText(text) {
  const RE = /^\[(\d{1,2}:\d{2}:\d{2})\]\s+(\S+?):\s+(.+)$/gm;
  const segments = [];
  let m;
  while ((m = RE.exec(text)) !== null) {
    segments.push({
      timestamp: m[1],
      speaker:   m[2].replace(/:$/, ''),
      text:      m[3].trim(),
    });
  }
  return segments;
}

function getSpeakerIndex(spId) {
  const speakers = (state.result?.speakers) || [];
  const idx      = speakers.indexOf(spId);
  return idx >= 0 ? idx : 0;
}

/* ============================================================
   ЭКСПОРТ
   ============================================================ */

function exportResult(format) {
  if (!state.taskId) return;
  const url = `/api/export/${state.taskId}?format=${format}`;
  // Открываем в новой вкладке — браузер сам скачает файл
  const a = document.createElement('a');
  a.href  = url;
  a.click();
  showToast(`Экспорт в ${format.toUpperCase()} начат`, 'success');
}

/* ============================================================
   UI-УТИЛИТЫ
   ============================================================ */

function showSection(name) {
  const map = {
    progress: progressSection,
    speakers: speakersSection,
    result:   resultSection,
  };
  const el = map[name];
  if (el) el.classList.add('visible');
}

function hideSection(name) {
  const map = {
    progress: progressSection,
    speakers: speakersSection,
    result:   resultSection,
  };
  const el = map[name];
  if (el) el.classList.remove('visible');
}

function updateProgress(stage, pct) {
  progressBar.style.width   = `${Math.min(100, Math.max(0, pct))}%`;
  progressStage.textContent = stage;
  progressPct.textContent   = `${Math.round(pct)}%`;
}

function updateStep(index, state) {
  const step = STAGES[index];
  if (!step) return;
  const el = $(step.id);
  if (el) el.className = `step ${state}`;
}

function resetProgress() {
  clearPolling();
  state.taskId    = null;
  state.result    = null;
  state.speakerNames = {};

  progressBar.style.width   = '0%';
  progressStage.textContent = 'Ожидание...';
  progressPct.textContent   = '0%';

  errorBox.classList.remove('visible');
  errorBox.textContent = '';

  STAGES.forEach(s => {
    const el = $(s.id);
    if (el) el.className = 'step';
    if (s.conn) {
      const conn = $(s.conn);
      if (conn) conn.className = 'step-connector';
    }
  });
}

function showError(message) {
  errorBox.textContent = `⚠️  ${message}`;
  errorBox.classList.add('visible');
  updateProgress('Ошибка обработки', 0);
  showToast(message, 'error');
}

function resetToUpload() {
  clearPolling();
  resetProgress();
  hideSection('progress');
  hideSection('speakers');
  hideSection('result');
  fileInfo.classList.remove('visible');
  fileInput.value = '';
  speakerList.innerHTML = '';
  transcriptContainer.innerHTML =
    '<div class="empty-transcript">Результат появится здесь после обработки</div>';
}

function showToast(message, type = 'info') {
  const container = $('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
