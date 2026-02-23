const form = document.getElementById("run-form");
const zipInput = document.getElementById("zip");
const zipButton = document.getElementById("zip-button");
const zipDisplay = document.getElementById("zip-display");
const runButton = document.getElementById("run-button");
const statusPill = document.getElementById("status-pill");
const logOutput = document.getElementById("log-output");
const progress = document.getElementById("progress");
const resetBtn = document.getElementById("reset");
const stopBtn = document.getElementById("stop-job");
const outPreview = document.getElementById("out-preview");
const ocrPreview = document.getElementById("ocr-preview");
const transcribePreview = document.getElementById("transcribe-preview");
const speedPreview = document.getElementById("speed-preview");
const cudaPreview = document.getElementById("cuda-preview");
const elapsedPreview = document.getElementById("elapsed-preview");
const sampleAudioInput = document.getElementById("sample_audio");
const sampleAudioButton = document.getElementById("sample-audio-button");
const sampleAudioDisplay = document.getElementById("sample-audio-display");
const modelTestButton = document.getElementById("test-model-button");
const modelTestOutput = document.getElementById("test-model-output");
const modelTestTime = document.getElementById("test-model-time");
const modelTestUsed = document.getElementById("test-model-used");

const id = (name) => document.getElementById(name);

const setStatus = (text, tone) => {
  statusPill.textContent = text;
  statusPill.style.borderColor = tone || "var(--outline)";
};

const statusBanner = document.getElementById("status-banner");
const statusTitle = statusBanner ? statusBanner.querySelector(".status-title") : null;
const statusSubtitle = statusBanner ? statusBanner.querySelector(".status-subtitle") : null;

const setBanner = (state, title, subtitle) => {
  if (!statusBanner) return;
  statusBanner.classList.remove("idle", "running", "done", "error");
  statusBanner.classList.add(state);
  if (statusTitle) statusTitle.textContent = title;
  if (statusSubtitle) statusSubtitle.textContent = subtitle;
};

let runtimeInfo = null;
let poller = null;
let currentJobId = null;
let elapsedTimer = null;
let jobActive = false;

const speedPresetLabel = (value) => {
  if (value === "off") return "Off";
  return "Auto";
};

const updateDevicePreview = () => {
  if (id("force_cpu").checked) {
    cudaPreview.textContent = "Forced CPU";
    return;
  }
  if (!runtimeInfo) {
    cudaPreview.textContent = "Unknown";
    return;
  }
  if (runtimeInfo.cuda_available === true) cudaPreview.textContent = "CUDA ON";
  else if (runtimeInfo.cuda_available === false) cudaPreview.textContent = "CPU";
  else cudaPreview.textContent = "Unknown";
};

zipButton.addEventListener("click", () => zipInput.click());
zipInput.addEventListener("change", () => {
  const file = zipInput.files && zipInput.files[0];
  zipDisplay.value = file ? file.name : "";
});

if (sampleAudioButton && sampleAudioInput) {
  sampleAudioButton.addEventListener("click", () => sampleAudioInput.click());
}
if (sampleAudioInput && sampleAudioDisplay) {
  sampleAudioInput.addEventListener("change", () => {
    const file = sampleAudioInput.files && sampleAudioInput.files[0];
    sampleAudioDisplay.value = file ? file.name : "";
  });
}

const syncPreview = () => {
  if (outPreview) outPreview.textContent = id("out").value || "out";
  if (ocrPreview) ocrPreview.textContent = id("no_ocr").checked ? "Disabled" : "Enabled";
  if (transcribePreview) transcribePreview.textContent = id("no_transcribe").checked ? "Disabled" : "Enabled";
  if (speedPreview) speedPreview.textContent = speedPresetLabel(id("speed_preset").value);
  updateDevicePreview();
};
["out", "no_ocr", "no_transcribe", "force_cpu", "speed_preset"].forEach((field) =>
  id(field).addEventListener("change", syncPreview),
);
syncPreview();

const runtimeChecks = () => {
  if (!runtimeInfo) return { ok: false, details: ["Runtime info unavailable."] };
  const noTranscribe = id("no_transcribe").checked;
  const noOcr = id("no_ocr").checked;
  const details = [];

  if (!noTranscribe) {
    if (!runtimeInfo.transformers_available) details.push("Missing Python dependency: transformers");
    if (!runtimeInfo.torch_available) details.push("Missing Python dependency: torch");
    if (!runtimeInfo.ffmpeg_available) details.push("Missing system dependency: ffmpeg");
  }
  if (!noOcr) {
    if (!runtimeInfo.tesseract_available) details.push("Missing system dependency: tesseract");
  }

  return { ok: details.length === 0, details };
};

const modelTestRuntimeChecks = () => {
  if (!runtimeInfo) return { ok: false, details: ["Runtime info unavailable."] };
  const details = [];
  if (!runtimeInfo.transformers_available) details.push("Missing Python dependency: transformers");
  if (!runtimeInfo.torch_available) details.push("Missing Python dependency: torch");
  if (!runtimeInfo.ffmpeg_available) details.push("Missing system dependency: ffmpeg");
  return { ok: details.length === 0, details };
};

const updateModelTestButtonState = () => {
  if (!modelTestButton) return;
  modelTestButton.disabled = !modelTestRuntimeChecks().ok;
};

const renderPreflight = () => {
  updateModelTestButtonState();
  const check = runtimeChecks();
  if (check.ok) {
    if (runButton) runButton.disabled = jobActive;
    return true;
  }

  if (runButton) runButton.disabled = true;
  setStatus("Missing deps", "rgba(243, 179, 76, 0.7)");
  setBanner("error", "Missing dependencies", "Install missing requirements shown in the log.");

  const hints = runtimeInfo && runtimeInfo.install_hints ? runtimeInfo.install_hints : {};
  const lines = [];
  for (const d of check.details) {
    if (d.includes("ffmpeg") && hints.ffmpeg) lines.push(`${d}\n${hints.ffmpeg}`);
    else if (d.includes("tesseract") && hints.tesseract) lines.push(`${d}\n${hints.tesseract}`);
    else lines.push(d);
  }
  logOutput.textContent = lines.join("\n\n");
  return false;
};

const loadRuntime = async () => {
  try {
    const res = await fetch("/api/runtime");
    if (!res.ok) throw new Error("runtime endpoint unavailable");
    runtimeInfo = await res.json();

    const speedPreset = id("speed_preset");
    const supported = Array.isArray(runtimeInfo.supported_speed_presets)
      ? runtimeInfo.supported_speed_presets
      : ["auto", "off"];
    const labels = {
      auto: "Auto (recommended)",
      off: "Off (manual model/device)",
    };
    const current = speedPreset.value || "auto";
    speedPreset.innerHTML = "";
    for (const preset of supported) {
      const opt = document.createElement("md-select-option");
      opt.setAttribute("value", preset);
      opt.textContent = labels[preset] || preset;
      speedPreset.appendChild(opt);
    }
    const next = supported.includes(current) ? current : (supported.includes("auto") ? "auto" : supported[0] || "auto");
    speedPreset.value = next;

    syncPreview();
    updateDevicePreview();

    renderPreflight();
  } catch (err) {
    cudaPreview.textContent = "Unknown";
    runtimeInfo = null;
    if (runButton) runButton.disabled = true;
    if (modelTestButton) modelTestButton.disabled = true;
    setStatus("Runtime error", "rgba(243, 179, 76, 0.7)");
    logOutput.textContent = `Failed to load runtime checks: ${err}`;
  }
};

id("no_transcribe").addEventListener("change", renderPreflight);
id("no_ocr").addEventListener("change", renderPreflight);
id("force_cpu").addEventListener("change", renderPreflight);

const setStopEnabled = (enabled) => {
  if (!stopBtn) return;
  stopBtn.disabled = !enabled;
};

const pollJob = (jobId) => {
  if (poller) clearInterval(poller);
  if (elapsedTimer) clearInterval(elapsedTimer);
  currentJobId = jobId;
  jobActive = true;
  if (runButton) runButton.disabled = true;
  setStopEnabled(true);
  progress.classList.remove("hidden");
  progress.indeterminate = true;
  setStatus("Running", "rgba(61, 214, 197, 0.5)");
  setBanner("running", "Running", "Processing media and building outputs.");

  const tick = async () => {
    try {
      const statusRes = await fetch(`/api/jobs/${jobId}`);
      if (!statusRes.ok) return;
      const status = await statusRes.json();
      const logRes = await fetch(`/api/jobs/${jobId}/log`);
      const logText = await logRes.text();
      logOutput.textContent = logText || "Running...";
      logOutput.scrollTop = logOutput.scrollHeight;

      if (elapsedPreview) {
        if (status.started_at) {
          const start = new Date(status.started_at).getTime();
          const now = Date.now();
          const secs = Math.max(0, Math.floor((now - start) / 1000));
          const m = Math.floor(secs / 60);
          const s = secs % 60;
          elapsedPreview.textContent = `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
        } else {
          elapsedPreview.textContent = "00:00";
        }
      }

      if (status.status === "done") {
        setStatus("Done", "rgba(61, 214, 197, 0.6)");
        progress.classList.add("hidden");
        clearInterval(poller);
        poller = null;
        if (elapsedTimer) clearInterval(elapsedTimer);
        elapsedTimer = null;
        jobActive = false;
        renderPreflight();
        setStopEnabled(false);
        setBanner("done", "Done", "Outputs are ready in the output folder.");
      } else if (status.status === "error") {
        setStatus("Error", "rgba(243, 179, 76, 0.7)");
        progress.classList.add("hidden");
        clearInterval(poller);
        poller = null;
        if (elapsedTimer) clearInterval(elapsedTimer);
        elapsedTimer = null;
        jobActive = false;
        renderPreflight();
        setStopEnabled(false);
        setBanner("error", "Error", "Something went wrong. Check the log.");
      } else if (status.status === "stopped") {
        setStatus("Stopped", "rgba(243, 179, 76, 0.7)");
        progress.classList.add("hidden");
        clearInterval(poller);
        poller = null;
        if (elapsedTimer) clearInterval(elapsedTimer);
        elapsedTimer = null;
        jobActive = false;
        renderPreflight();
        setStopEnabled(false);
        setBanner("error", "Stopped", "The run was stopped early.");
      }
    } catch (err) {
      logOutput.textContent = `Error fetching status: ${err}`;
    }
  };

  tick();
  poller = setInterval(tick, 2000);
  elapsedTimer = setInterval(() => {
    if (!elapsedPreview) return;
    const current = elapsedPreview.textContent || "00:00";
    if (!current || current === "00:00") return;
    const parts = current.split(":").map((x) => parseInt(x, 10));
    if (parts.length !== 2 || Number.isNaN(parts[0]) || Number.isNaN(parts[1])) return;
    let m = parts[0];
    let s = parts[1] + 1;
    if (s >= 60) {
      s = 0;
      m += 1;
    }
    elapsedPreview.textContent = `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }, 1000);
};

if (modelTestButton) {
  modelTestButton.addEventListener("click", async () => {
    if (!sampleAudioInput || !sampleAudioInput.files || !sampleAudioInput.files[0]) {
      setStatus("Audio required", "rgba(243, 179, 76, 0.7)");
      if (modelTestOutput) modelTestOutput.textContent = "Choose a single audio file to run a model test.";
      return;
    }

    const check = modelTestRuntimeChecks();
    if (!check.ok) {
      const hints = runtimeInfo && runtimeInfo.install_hints ? runtimeInfo.install_hints : {};
      const lines = [];
      for (const d of check.details) {
        if (d.includes("ffmpeg") && hints.ffmpeg) lines.push(`${d}\n${hints.ffmpeg}`);
        else lines.push(d);
      }
      if (modelTestOutput) modelTestOutput.textContent = lines.join("\n\n");
      return;
    }

    const fd = new FormData();
    fd.append("audio", sampleAudioInput.files[0]);
    fd.append("whisper_model", id("whisper_model").value);
    if (id("force_cpu").checked) fd.append("force_cpu", "true");

    const originalLabel = modelTestButton.textContent;
    modelTestButton.disabled = true;
    modelTestButton.textContent = "Testing...";
    if (modelTestOutput) modelTestOutput.textContent = "Transcribing sample audio...";
    if (modelTestTime) modelTestTime.textContent = "-";
    if (modelTestUsed) modelTestUsed.textContent = id("whisper_model").value;
    setStatus("Testing", "rgba(61, 214, 197, 0.5)");

    try {
      const res = await fetch("/api/transcribe-test", { method: "POST", body: fd });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) {
        const details = payload && payload.details ? payload.details : "Failed to run model test.";
        const message = Array.isArray(details) ? details.join("\n") : String(details);
        if (modelTestOutput) modelTestOutput.textContent = message;
        setStatus("Test failed", "rgba(243, 179, 76, 0.7)");
        return;
      }

      const transcript = payload && payload.text && String(payload.text).trim() ? String(payload.text).trim() : "(empty transcript)";
      if (modelTestOutput) modelTestOutput.textContent = transcript;
      if (modelTestTime) modelTestTime.textContent = `${Number(payload.elapsed_seconds || 0).toFixed(2)}s`;
      if (modelTestUsed) modelTestUsed.textContent = payload.model || id("whisper_model").value;
      setStatus("Test done", "rgba(61, 214, 197, 0.6)");
      setBanner("idle", "Test complete", "Review result and choose the model you prefer.");
    } catch (err) {
      if (modelTestOutput) modelTestOutput.textContent = `Model test failed: ${err}`;
      setStatus("Test failed", "rgba(243, 179, 76, 0.7)");
    } finally {
      modelTestButton.textContent = originalLabel || "Test selected model";
      updateModelTestButtonState();
    }
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!zipInput.files || !zipInput.files[0]) {
    setStatus("Zip required", "rgba(243, 179, 76, 0.7)");
    return;
  }

  if (!renderPreflight()) return;

  const fd = new FormData();
  fd.append("zip", zipInput.files[0]);
  fd.append("out", id("out").value);
  fd.append("whisper_model", id("whisper_model").value);
  fd.append("speed_preset", id("speed_preset").value);
  if (id("force_cpu").checked) fd.append("force_cpu", "true");
  if (id("no_transcribe").checked) fd.append("no_transcribe", "true");
  if (id("no_ocr").checked) fd.append("no_ocr", "true");

  setStatus("Starting...", "rgba(61, 214, 197, 0.5)");
  logOutput.textContent = "Starting run...";
  if (runButton) runButton.disabled = true;

  const res = await fetch("/api/run", {
    method: "POST",
    body: fd,
  });

  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    if (payload && payload.error === "job_already_running") {
      logOutput.textContent = `Another job is already running (job_id=${payload.job_id}). Stop it or wait until it finishes.`;
      setStatus("Job running", "rgba(243, 179, 76, 0.7)");
    } else {
      const text = payload ? JSON.stringify(payload, null, 2) : (await res.text());
      logOutput.textContent = text || "Failed to start job";
      setStatus("Failed", "rgba(243, 179, 76, 0.7)");
    }
    jobActive = false;
    renderPreflight();
    return;
  }

  const data = await res.json();
  pollJob(data.job_id);
});

resetBtn.addEventListener("click", () => {
  if (poller) clearInterval(poller);
  poller = null;
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = null;
  jobActive = false;
  currentJobId = null;
  form.reset();
  zipDisplay.value = "";
  if (sampleAudioDisplay) sampleAudioDisplay.value = "";
  setStatus("Idle", "var(--outline)");
  setBanner("idle", "Idle", "Upload a zip to begin.");
  logOutput.textContent = "Ready.";
  if (modelTestOutput) modelTestOutput.textContent = "No test run yet.";
  if (modelTestTime) modelTestTime.textContent = "-";
  if (modelTestUsed) modelTestUsed.textContent = "-";
  syncPreview();
  if (elapsedPreview) elapsedPreview.textContent = "00:00";
  loadRuntime();
  setStopEnabled(false);
});

if (stopBtn) {
  stopBtn.addEventListener("click", async () => {
    if (!currentJobId) return;
    setStatus("Stopping...", "rgba(243, 179, 76, 0.7)");
    try {
      await fetch(`/api/jobs/${currentJobId}/stop`, { method: "POST" });
    } catch (err) {
      logOutput.textContent = `Error stopping job: ${err}`;
    }
  });
}

loadRuntime();
