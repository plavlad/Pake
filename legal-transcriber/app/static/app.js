const state = {
  selectedFile: null,
  taskId: null,
  pollTimer: null,
  speakerMap: {},
  result: null,
};

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const selectButton = document.getElementById("selectButton");
const selectedFile = document.getElementById("selectedFile");
const uploadButton = document.getElementById("uploadButton");
const statusPanel = document.getElementById("statusPanel");
const stageText = document.getElementById("stageText");
const progressText = document.getElementById("progressText");
const progressFill = document.getElementById("progressFill");
const errorText = document.getElementById("errorText");
const resultPanel = document.getElementById("resultPanel");
const speakerList = document.getElementById("speakerList");
const transcriptSegments = document.getElementById("transcriptSegments");
const exportTxt = document.getElementById("exportTxt");
const exportDocx = document.getElementById("exportDocx");
const copyButton = document.getElementById("copyButton");

selectButton.addEventListener("click", (event) => {
  event.stopPropagation();
  fileInput.click();
});
dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});

fileInput.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  setSelectedFile(file || null);
});

["dragenter", "dragover"].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add("dragover");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragover");
  });
});

dropzone.addEventListener("drop", (event) => {
  const file = event.dataTransfer?.files?.[0];
  setSelectedFile(file || null);
});

uploadButton.addEventListener("click", async () => {
  if (!state.selectedFile) {
    return;
  }

  resetStatus();
  const formData = new FormData();
  formData.append("file", state.selectedFile);

  uploadButton.disabled = true;
  uploadButton.textContent = "Загрузка...";

  try {
    const response = await fetch("/api/tasks", {
      method: "POST",
      body: formData,
    });
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.detail || "Не удалось создать задачу.");
    }

    state.taskId = payload.id;
    updateStatus(payload);
    startPolling();
  } catch (error) {
    showError(error.message || "Ошибка загрузки.");
  } finally {
    uploadButton.disabled = false;
    uploadButton.textContent = "Запустить обработку";
  }
});

exportTxt.addEventListener("click", () => exportResult("txt"));
exportDocx.addEventListener("click", () => exportResult("docx"));

copyButton.addEventListener("click", async () => {
  if (!state.result) {
    return;
  }

  const text = formatTranscriptText();
  try {
    await navigator.clipboard.writeText(text);
    copyButton.textContent = "Скопировано";
    setTimeout(() => {
      copyButton.textContent = "Скопировать текст";
    }, 1500);
  } catch (_error) {
    showError("Не удалось скопировать текст в буфер обмена.");
  }
});

function setSelectedFile(file) {
  state.selectedFile = file;
  selectedFile.textContent = file ? `${file.name} (${formatBytes(file.size)})` : "Файл не выбран";
  uploadButton.disabled = !file;
}

function resetStatus() {
  clearInterval(state.pollTimer);
  state.pollTimer = null;
  state.result = null;
  state.speakerMap = {};
  statusPanel.classList.remove("hidden");
  resultPanel.classList.add("hidden");
  errorText.classList.add("hidden");
  errorText.textContent = "";
  progressFill.style.width = "0%";
  stageText.textContent = "Подготовка";
  progressText.textContent = "0%";
}

function updateStatus(task) {
  statusPanel.classList.remove("hidden");
  stageText.textContent = task.stage || "Обработка";
  progressText.textContent = `${task.progress || 0}%`;
  progressFill.style.width = `${task.progress || 0}%`;

  if (task.status === "failed") {
    showError(task.error || "Неизвестная ошибка обработки.");
    clearInterval(state.pollTimer);
  }

  if (task.status === "completed" && task.result) {
    clearInterval(state.pollTimer);
    state.result = task.result;
    state.speakerMap = { ...(task.result.speaker_map || {}) };
    renderResult();
  }
}

function showError(message) {
  errorText.textContent = message;
  errorText.classList.remove("hidden");
}

function startPolling() {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    if (!state.taskId) {
      return;
    }

    try {
      const response = await fetch(`/api/tasks/${state.taskId}`);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail || "Не удалось получить статус задачи.");
      }
      updateStatus(payload);
    } catch (error) {
      showError(error.message || "Потеряна связь с сервером.");
      clearInterval(state.pollTimer);
    }
  }, 2500);
}

function renderResult() {
  resultPanel.classList.remove("hidden");
  renderSpeakers();
  renderSegments();
}

function renderSpeakers() {
  speakerList.innerHTML = "";
  for (const speaker of state.result.speakers || []) {
    const card = document.createElement("div");
    card.className = "speaker-card";

    const label = document.createElement("label");
    label.textContent = speaker.id;
    label.setAttribute("for", `speaker-${speaker.id}`);

    const input = document.createElement("input");
    input.id = `speaker-${speaker.id}`;
    input.value = state.speakerMap[speaker.id] || speaker.name || speaker.id;
    input.addEventListener("input", () => {
      state.speakerMap[speaker.id] = input.value.trim() || speaker.id;
      renderSegments();
      debounceSpeakerMapSave();
    });

    card.append(label, input);
    speakerList.appendChild(card);
  }
}

function renderSegments() {
  transcriptSegments.innerHTML = "";
  for (const segment of state.result.segments || []) {
    const card = document.createElement("article");
    card.className = "segment";

    const header = document.createElement("div");
    header.className = "segment-header";

    const speaker = document.createElement("span");
    speaker.className = "segment-speaker";
    speaker.textContent = state.speakerMap[segment.speaker] || segment.display_speaker || segment.speaker;

    const time = document.createElement("span");
    time.className = "segment-time";
    time.textContent = `${toTime(segment.start)} - ${toTime(segment.end)}`;

    const text = document.createElement("p");
    text.className = "segment-text";
    text.textContent = segment.text;

    header.append(speaker, time);
    card.append(header, text);
    transcriptSegments.appendChild(card);
  }
}

async function exportResult(format) {
  if (!state.taskId) {
    return;
  }
  await saveSpeakerMap();
  window.open(`/api/tasks/${state.taskId}/export?format=${format}`, "_blank");
}

function formatTranscriptText() {
  return (state.result.segments || [])
    .map((segment) => {
      const speaker = state.speakerMap[segment.speaker] || segment.display_speaker || segment.speaker;
      return `[${toTime(segment.start)} - ${toTime(segment.end)}] ${speaker}: ${segment.text}`;
    })
    .join("\n");
}

let speakerMapSaveTimer = null;
function debounceSpeakerMapSave() {
  clearTimeout(speakerMapSaveTimer);
  speakerMapSaveTimer = setTimeout(saveSpeakerMap, 400);
}

async function saveSpeakerMap() {
  if (!state.taskId) {
    return;
  }

  try {
    const response = await fetch(`/api/tasks/${state.taskId}/speaker-map`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ speaker_map: state.speakerMap }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Не удалось сохранить имена спикеров.");
    }
    state.result = payload.result;
  } catch (error) {
    showError(error.message || "Ошибка сохранения имен спикеров.");
  }
}

function toTime(seconds) {
  const total = Math.max(0, Math.round(Number(seconds || 0)));
  const hh = String(Math.floor(total / 3600)).padStart(2, "0");
  const mm = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function formatBytes(bytes) {
  if (!bytes) {
    return "0 B";
  }
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const size = bytes / 1024 ** index;
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}
