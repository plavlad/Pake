const state = {
  file: null,
  jobId: null,
  timer: null,
  result: null,
  speakerMap: {},
};

const elements = {
  dropZone: document.getElementById("dropZone"),
  fileInput: document.getElementById("fileInput"),
  fileName: document.getElementById("fileName"),
  uploadBtn: document.getElementById("uploadBtn"),
  progressSection: document.getElementById("progressSection"),
  progressBar: document.getElementById("progressBar"),
  stageText: document.getElementById("stageText"),
  errorText: document.getElementById("errorText"),
  resultSection: document.getElementById("resultSection"),
  speakerPanel: document.getElementById("speakerPanel"),
  transcriptView: document.getElementById("transcriptView"),
  exportTxtBtn: document.getElementById("exportTxtBtn"),
  exportDocxBtn: document.getElementById("exportDocxBtn"),
};

function setError(message = "") {
  if (!message) {
    elements.errorText.classList.add("hidden");
    elements.errorText.textContent = "";
    return;
  }
  elements.errorText.textContent = message;
  elements.errorText.classList.remove("hidden");
}

function stageLabel(stage, fallbackMessage = "") {
  const map = {
    queued: "Задача в очереди",
    transcribing: "Распознавание",
    aligning: "Выравнивание",
    diarization: "Диаризация",
    llm_processing: "Обработка LLM",
    completed: "Готово",
    failed: "Ошибка",
  };
  return map[stage] || fallbackMessage || stage || "Ожидание";
}

function ts(seconds) {
  const value = Math.max(0, Math.floor(seconds));
  const h = String(Math.floor(value / 3600)).padStart(2, "0");
  const m = String(Math.floor((value % 3600) / 60)).padStart(2, "0");
  const s = String(value % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

function mappedSpeaker(name) {
  return state.speakerMap[name] || name;
}

function renderTranscript() {
  if (!state.result) return;
  const lines = state.result.segments.map((seg) => {
    const speaker = mappedSpeaker(seg.speaker);
    return `[${ts(seg.start)} - ${ts(seg.end)}] ${speaker}: ${seg.text}`;
  });
  elements.transcriptView.textContent = lines.join("\n");
}

function renderSpeakerPanel() {
  if (!state.result) return;
  elements.speakerPanel.innerHTML = "";

  const uniqueSpeakers = [...new Set(state.result.segments.map((s) => s.speaker))];
  uniqueSpeakers.forEach((speaker) => {
    if (!state.speakerMap[speaker]) {
      state.speakerMap[speaker] = speaker;
    }
    const row = document.createElement("div");
    row.className = "speaker-row";

    const label = document.createElement("label");
    label.textContent = speaker;
    label.setAttribute("for", `speaker-${speaker}`);

    const input = document.createElement("input");
    input.id = `speaker-${speaker}`;
    input.value = state.speakerMap[speaker];
    input.placeholder = "Новое имя";
    input.addEventListener("input", () => {
      state.speakerMap[speaker] = input.value.trim() || speaker;
      renderTranscript();
    });

    row.appendChild(label);
    row.appendChild(input);
    elements.speakerPanel.appendChild(row);
  });
}

async function exportFile(format) {
  if (!state.jobId) return;
  const response = await fetch(`/api/export/${state.jobId}?format=${format}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ speaker_map: state.speakerMap }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Не удалось выполнить экспорт");
  }
  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = format === "docx" ? "transcript.docx" : "transcript.txt";
  link.click();
  window.URL.revokeObjectURL(url);
}

async function pollStatus() {
  if (!state.jobId) return;

  const response = await fetch(`/api/status/${state.jobId}`);
  if (!response.ok) {
    throw new Error("Не удалось получить статус задачи");
  }
  const status = await response.json();

  elements.progressSection.classList.remove("hidden");
  elements.progressBar.style.width = `${status.progress || 0}%`;
  elements.stageText.textContent = `${stageLabel(status.stage, status.message)}: ${status.message || ""}`;

  if (status.stage === "failed") {
    clearInterval(state.timer);
    elements.uploadBtn.disabled = false;
    setError(status.error || "Во время обработки произошла ошибка");
    return;
  }

  if (status.stage === "completed") {
    clearInterval(state.timer);
    elements.uploadBtn.disabled = false;
    setError("");
    state.result = status.result;
    elements.resultSection.classList.remove("hidden");
    renderSpeakerPanel();
    renderTranscript();
  }
}

async function startUpload() {
  if (!state.file) return;
  if (state.timer) {
    clearInterval(state.timer);
  }
  elements.uploadBtn.disabled = true;
  elements.resultSection.classList.add("hidden");
  setError("");

  const formData = new FormData();
  formData.append("file", state.file);
  const response = await fetch("/api/upload", {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Ошибка загрузки файла");
  }

  const payload = await response.json();
  state.jobId = payload.job_id;
  state.result = null;
  state.speakerMap = {};

  await pollStatus();
  state.timer = setInterval(async () => {
    try {
      await pollStatus();
    } catch (error) {
      clearInterval(state.timer);
      setError(error.message || "Ошибка при опросе статуса");
    }
  }, 2000);
}

function setFile(file) {
  state.file = file;
  elements.uploadBtn.disabled = !file;
  elements.fileName.textContent = file ? `Выбран файл: ${file.name}` : "Файл не выбран";
}

elements.dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  elements.dropZone.classList.add("dragover");
});

elements.dropZone.addEventListener("dragleave", () => {
  elements.dropZone.classList.remove("dragover");
});

elements.dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  elements.dropZone.classList.remove("dragover");
  const [file] = event.dataTransfer.files || [];
  setFile(file || null);
});

elements.fileInput.addEventListener("change", () => {
  const [file] = elements.fileInput.files || [];
  setFile(file || null);
});

elements.uploadBtn.addEventListener("click", async () => {
  try {
    await startUpload();
  } catch (error) {
    setError(error.message || "Не удалось запустить обработку");
    elements.uploadBtn.disabled = false;
  }
});

elements.exportTxtBtn.addEventListener("click", async () => {
  try {
    await exportFile("txt");
  } catch (error) {
    setError(error.message || "Ошибка экспорта TXT");
  }
});

elements.exportDocxBtn.addEventListener("click", async () => {
  try {
    await exportFile("docx");
  } catch (error) {
    setError(error.message || "Ошибка экспорта DOCX");
  }
});
