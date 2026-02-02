const form = document.getElementById("run-form");
const zipInput = document.getElementById("zip");
const zipButton = document.getElementById("zip-button");
const zipDisplay = document.getElementById("zip-display");
const statusPill = document.getElementById("status-pill");
const logOutput = document.getElementById("log-output");
const progress = document.getElementById("progress");
const resetBtn = document.getElementById("reset");
const stopBtn = document.getElementById("stop-job");
const outPreview = document.getElementById("out-preview");
const formatPreview = document.getElementById("format-preview");
const ocrPreview = document.getElementById("ocr-preview");
const transcribePreview = document.getElementById("transcribe-preview");
const cudaPreview = document.getElementById("cuda-preview");
const elapsedPreview = document.getElementById("elapsed-preview");
const benchButton = document.getElementById("bench-run");
const benchStop = document.getElementById("bench-stop");
const benchStatus = document.getElementById("bench-status");
const benchLog = document.getElementById("bench-log");
const benchResults = document.getElementById("bench-results");
const benchSummary = document.getElementById("bench-summary");

const id = (name) => document.getElementById(name);

const bindToggle = (toggleId, fieldId) => {
  const toggle = id(toggleId);
  const field = id(fieldId);
  const update = () => {
    field.disabled = !toggle.checked;
  };
  update();
  toggle.addEventListener("change", update);
};

bindToggle("md_max_enabled", "md_max_chars");
bindToggle("progress_enabled", "progress_every");
bindToggle("ocr_max_enabled", "ocr_max");
bindToggle("ocr_edge_enabled", "ocr_edge_threshold");
bindToggle("ocr_downscale_enabled", "ocr_downscale");
bindToggle("audio_workers_enabled", "audio_workers");
bindToggle("ocr_workers_enabled", "ocr_workers");
bindToggle("me_enabled", "me");
bindToggle("them_enabled", "them");

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

zipButton.addEventListener("click", () => zipInput.click());

zipInput.addEventListener("change", () => {
  const file = zipInput.files && zipInput.files[0];
  zipDisplay.value = file ? file.name : "";
});

const toggleExclusive = (primaryId, secondaryId) => {
  const primary = id(primaryId);
  const secondary = id(secondaryId);
  primary.addEventListener("change", () => {
    if (primary.checked) {
      secondary.checked = false;
      secondary.dispatchEvent(new Event("change"));
    }
  });
};

toggleExclusive("only_transcribe", "only_ocr");
toggleExclusive("only_ocr", "only_transcribe");

const syncDisableGroups = () => {
  const noTranscribe = id("no_transcribe").checked;
  const noOcr = id("no_ocr").checked;

  ["convert_audio", "transcribe_backend", "whisper_model", "lang"].forEach((field) => {
    id(field).disabled = noTranscribe;
  });

  ["ocr_mode", "ocr_lang"].forEach((field) => {
    id(field).disabled = noOcr;
  });
};

["no_transcribe", "no_ocr"].forEach((field) => {
  id(field).addEventListener("change", syncDisableGroups);
});
syncDisableGroups();

const syncPreview = () => {
  if (outPreview) outPreview.textContent = id("out").value || "out";
  if (formatPreview) {
    const fmt = id("format").value;
    formatPreview.textContent = fmt === "auto" ? "Auto" : fmt.toUpperCase();
  }
  if (ocrPreview) {
    ocrPreview.textContent = id("no_ocr").checked ? "Disabled" : "Enabled";
  }
  if (transcribePreview) {
    transcribePreview.textContent = id("no_transcribe").checked ? "Disabled" : "Enabled";
  }
};

["out", "format", "no_ocr", "no_transcribe"].forEach((field) => {
  id(field).addEventListener("change", syncPreview);
});
syncPreview();

const syncRuntime = async () => {
  if (!cudaPreview) return;
  try {
    const res = await fetch("/api/runtime");
    if (!res.ok) {
      cudaPreview.textContent = "Unknown";
      return;
    }
    const data = await res.json();
    if (data.cuda_available === true) {
      cudaPreview.textContent = "CUDA ON";
    } else if (data.cuda_available === false) {
      cudaPreview.textContent = "CPU";
    } else {
      cudaPreview.textContent = "Unknown";
    }

    const backendSelect = id("transcribe_backend");
    if (backendSelect) {
      const opts = backendSelect.querySelectorAll("md-select-option");
      const openaiOpt = Array.from(opts).find((o) => o.getAttribute("value") === "openai");
      const fasterOpt = Array.from(opts).find((o) => o.getAttribute("value") === "faster");

      const openaiOk = data.openai_whisper_available ?? data.whisper_available;
      const fasterOk = data.faster_whisper_available;
      const fasterUsable = data.faster_whisper_usable ?? fasterOk;
      const symlinkOk = data.windows_symlink_ok;
      const fasterDownloadWarn = data.faster_whisper_download_may_need_symlink ?? false;

      if (openaiOpt) {
        openaiOpt.textContent = openaiOk ? "OpenAI Whisper (GPU if available)" : "OpenAI Whisper (not installed)";
        if (!openaiOk) openaiOpt.setAttribute("disabled", "");
        else openaiOpt.removeAttribute("disabled");
      }
      if (fasterOpt) {
        if (!fasterOk) {
          fasterOpt.textContent = "Faster Whisper (not installed)";
          fasterOpt.setAttribute("disabled", "");
        } else {
          fasterOpt.textContent = fasterDownloadWarn
            ? "Faster Whisper (CPU) — downloads may need Developer Mode"
            : "Faster Whisper (CPU)";
          fasterOpt.removeAttribute("disabled");
        }
      }

      if (!openaiOk && !fasterOk) {
        backendSelect.value = "auto";
      }
    }
  } catch (err) {
    cudaPreview.textContent = "Unknown";
  }
};

syncRuntime();

let poller = null;
let currentJobId = null;
let elapsedTimer = null;
let benchPoller = null;
let currentBenchId = null;

const setStopEnabled = (enabled) => {
  if (!stopBtn) return;
  stopBtn.disabled = !enabled;
};

const setBenchStopEnabled = (enabled) => {
  if (!benchStop) return;
  benchStop.disabled = !enabled;
};

const setBenchStatus = (text, tone) => {
  if (!benchStatus) return;
  benchStatus.textContent = text;
  benchStatus.style.borderColor = tone || "var(--outline)";
};

const collectBenchModels = () => {
  const checks = document.querySelectorAll(".bench-model md-checkbox");
  const models = [];
  checks.forEach((cb) => {
    if (cb.checked) {
      const model = cb.getAttribute("data-model");
      if (model) models.push(model);
    }
  });
  return models;
};

const renderBenchResults = (data) => {
  if (!benchResults) return;
  const results = data.results || [];
  if (!results.length) {
    benchResults.innerHTML = "<p class=\"helper\">No benchmark results yet.</p>";
    return;
  }

  const rec = data.recommendations || {};
  const recKey = (obj) => `${obj.backend}:${obj.model}:${obj.device}`;
  const fastestKey = rec.fastest ? recKey(rec.fastest) : null;
  const balancedKey = rec.balanced ? recKey(rec.balanced) : null;
  const qualityKey = rec.highest_quality ? recKey(rec.highest_quality) : null;

  const fmtHhMm = (secs) => {
    if (secs === null || secs === undefined) return "-";
    const s = Math.max(0, Math.round(Number(secs)));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  };

  const rows = results
    .map((r) => {
      const key = recKey(r);
      const tags = [
        key === fastestKey ? "<span class=\"tag\">Fastest</span>" : "",
        key === balancedKey ? "<span class=\"tag\">Balanced</span>" : "",
        key === qualityKey ? "<span class=\"tag\">Best quality</span>" : "",
      ].join(" ");
      const est = r.estimated_total_audio_plus_ocr_seconds ?? r.estimated_total_audio_seconds ?? null;
      return `
        <tr>
          <td>${r.backend}</td>
          <td>${r.model}</td>
          <td>${r.device}</td>
          <td>${r.avg_seconds_per_sample ?? "-"}</td>
          <td>${r.avg_realtime_factor ?? "-"}</td>
          <td>${r.avg_logprob ?? "-"}</td>
          <td>${r.errors ?? 0}</td>
          <td class="est">${fmtHhMm(est)}</td>
          <td class="tags">${tags}</td>
        </tr>
      `;
    })
    .join("");

  benchResults.innerHTML = `
    <table class="bench-table">
      <thead>
        <tr>
          <th>Backend</th>
          <th>Model</th>
          <th>Device</th>
          <th>Sec / sample</th>
          <th>RTF</th>
          <th>Logprob</th>
          <th>Errors</th>
          <th class="est">Est</th>
          <th>Pick</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  if (benchSummary) {
    const summary = data.summary || {};
    const devices = (summary.devices_tested || []).join(", ") || "cpu";
    const totalAudio = summary.total_audio_files ?? 0;
    const totalImages = summary.total_image_files ?? 0;
    benchSummary.textContent = `Samples: ${summary.audio_samples ?? 0} audio • Totals: ${totalAudio} audio / ${totalImages} images • Devices: ${devices} • Models: ${
      (summary.models || []).join(", ") || "n/a"
    }`;
  }
};

const pollJob = (jobId) => {
  if (poller) clearInterval(poller);
  if (elapsedTimer) clearInterval(elapsedTimer);
  currentJobId = jobId;
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
          elapsedPreview.textContent = `${m.toString().padStart(2, "0")}:${s
            .toString()
            .padStart(2, "0")}`;
        } else {
          elapsedPreview.textContent = "00:00";
        }
      }

      if (status.status === "done") {
        setStatus("Done", "rgba(61, 214, 197, 0.6)");
        progress.classList.add("hidden");
        clearInterval(poller);
        setStopEnabled(false);
        setBanner("done", "Done", "Outputs are ready in the output folder.");
      } else if (status.status === "error") {
        setStatus("Error", "rgba(243, 179, 76, 0.7)");
        progress.classList.add("hidden");
        clearInterval(poller);
        setStopEnabled(false);
        setBanner("error", "Error", "Something went wrong. Check the log.");
      } else if (status.status === "stopped") {
        setStatus("Stopped", "rgba(243, 179, 76, 0.7)");
        progress.classList.add("hidden");
        clearInterval(poller);
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
    if (elapsedPreview) {
      const current = elapsedPreview.textContent || "00:00";
      if (current && current !== "00:00") {
        const parts = current.split(":").map((x) => parseInt(x, 10));
        if (parts.length === 2 && !Number.isNaN(parts[0]) && !Number.isNaN(parts[1])) {
          let m = parts[0];
          let s = parts[1] + 1;
          if (s >= 60) {
            s = 0;
            m += 1;
          }
          elapsedPreview.textContent = `${m.toString().padStart(2, "0")}:${s
            .toString()
            .padStart(2, "0")}`;
        }
      }
    }
  }, 1000);
};

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!zipInput.files || !zipInput.files[0]) {
    setStatus("Zip required", "rgba(243, 179, 76, 0.7)");
    return;
  }

  const fd = new FormData();
  fd.append("zip", zipInput.files[0]);

  fd.append("tz", id("tz").value);
  fd.append("out", id("out").value);
  fd.append("format", id("format").value);
  fd.append("date_order", id("date_order").value);

  if (id("quiet").checked) fd.append("quiet", "true");
  if (id("no_resume").checked) fd.append("no_resume", "true");
  if (id("no_manifest").checked) fd.append("no_manifest", "true");
  if (id("no_report").checked) fd.append("no_report", "true");
  if (id("no_md").checked) fd.append("no_md", "true");
  if (id("no_by_month").checked) fd.append("no_by_month", "true");

  if (id("md_max_enabled").checked) fd.append("md_max_chars", id("md_max_chars").value);
  if (id("progress_enabled").checked) fd.append("progress_every", id("progress_every").value);

  if (id("audio_workers_enabled").checked) fd.append("audio_workers", id("audio_workers").value);
  if (id("ocr_workers_enabled").checked) fd.append("ocr_workers", id("ocr_workers").value);
  if (id("hash_media").checked) fd.append("hash_media", "true");

  if (id("me_enabled").checked && id("me").value.trim()) fd.append("me", id("me").value.trim());
  if (id("them_enabled").checked && id("them").value.trim()) fd.append("them", id("them").value.trim());

  fd.append("convert_audio", id("convert_audio").value);
  fd.append("transcribe_backend", id("transcribe_backend").value);
  fd.append("whisper_model", id("whisper_model").value);
  fd.append("lang", id("lang").value);

  if (id("no_transcribe").checked) fd.append("no_transcribe", "true");
  if (id("only_transcribe").checked) fd.append("only_transcribe", "true");
  if (id("force_cpu").checked) fd.append("force_cpu", "true");

  if (id("no_ocr").checked) fd.append("no_ocr", "true");
  if (id("only_ocr").checked) fd.append("only_ocr", "true");

  fd.append("ocr_mode", id("ocr_mode").value);
  fd.append("ocr_lang", id("ocr_lang").value);
  if (id("ocr_max_enabled").checked) fd.append("ocr_max", id("ocr_max").value);
  if (id("ocr_edge_enabled").checked) fd.append("ocr_edge_threshold", id("ocr_edge_threshold").value);
  if (id("ocr_downscale_enabled").checked) fd.append("ocr_downscale", id("ocr_downscale").value);

  setStatus("Starting...", "rgba(61, 214, 197, 0.5)");
  logOutput.textContent = "Starting run...";

  const res = await fetch("/api/run", {
    method: "POST",
    body: fd,
  });

  if (!res.ok) {
    const text = await res.text();
    logOutput.textContent = text || "Failed to start job";
    setStatus("Failed", "rgba(243, 179, 76, 0.7)");
    return;
  }

  const data = await res.json();
  pollJob(data.job_id);
});

const pollBenchmark = (benchId) => {
  if (benchPoller) clearInterval(benchPoller);
  currentBenchId = benchId;
  setBenchStopEnabled(true);
  setBenchStatus("Running", "rgba(61, 214, 197, 0.5)");
  if (benchLog) benchLog.textContent = "Benchmark started...";

  const tick = async () => {
    try {
      const statusRes = await fetch(`/api/benchmarks/${benchId}`);
      if (!statusRes.ok) return;
      const status = await statusRes.json();
      const logRes = await fetch(`/api/benchmarks/${benchId}/log`);
      const logText = await logRes.text();
      if (benchLog) {
        benchLog.textContent = logText || "Benchmark running...";
        benchLog.scrollTop = benchLog.scrollHeight;
      }

      if (status.status === "done") {
        setBenchStatus("Done", "rgba(61, 214, 197, 0.6)");
        clearInterval(benchPoller);
        setBenchStopEnabled(false);
        const res = await fetch(`/api/benchmarks/${benchId}/result`);
        if (res.ok) {
          const data = await res.json();
          renderBenchResults(data);
        }
      } else if (status.status === "error") {
        setBenchStatus("Error", "rgba(243, 179, 76, 0.7)");
        clearInterval(benchPoller);
        setBenchStopEnabled(false);
      } else if (status.status === "stopped") {
        setBenchStatus("Stopped", "rgba(243, 179, 76, 0.7)");
        clearInterval(benchPoller);
        setBenchStopEnabled(false);
      }
    } catch (err) {
      if (benchLog) benchLog.textContent = `Error fetching benchmark status: ${err}`;
    }
  };

  tick();
  benchPoller = setInterval(tick, 2000);
};

resetBtn.addEventListener("click", () => {
  form.reset();
  zipDisplay.value = "";
  setStatus("Idle", "var(--outline)");
  setBanner("idle", "Idle", "Upload a zip to begin.");
  logOutput.textContent = "Ready.";
  syncDisableGroups();
  syncPreview();
  setStopEnabled(false);
  if (elapsedPreview) elapsedPreview.textContent = "00:00";
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

if (benchButton) {
  benchButton.addEventListener("click", async () => {
    if (!zipInput.files || !zipInput.files[0]) {
      setBenchStatus("Zip required", "rgba(243, 179, 76, 0.7)");
      return;
    }

    const fd = new FormData();
    fd.append("zip", zipInput.files[0]);
    fd.append("out", id("out").value);

    fd.append("bench_audio_samples", id("bench_audio_samples").value);
    fd.append("bench_image_samples", id("bench_image_samples").value);
    fd.append("bench_backend", id("bench_backend").value);
    fd.append("bench_lang", id("bench_lang").value);
    if (id("bench_include_ocr").checked) fd.append("bench_include_ocr", "true");

    const models = collectBenchModels();
    if (models.length) fd.append("bench_models", models.join(","));

    setBenchStatus("Starting...", "rgba(61, 214, 197, 0.5)");
    if (benchLog) benchLog.textContent = "Starting benchmark...";

    const res = await fetch("/api/benchmark", {
      method: "POST",
      body: fd,
    });

    if (!res.ok) {
      const text = await res.text();
      if (benchLog) benchLog.textContent = text || "Failed to start benchmark";
      setBenchStatus("Failed", "rgba(243, 179, 76, 0.7)");
      return;
    }

    const data = await res.json();
    pollBenchmark(data.bench_id);
  });
}

if (benchStop) {
  benchStop.addEventListener("click", async () => {
    if (!currentBenchId) return;
    setBenchStatus("Stopping...", "rgba(243, 179, 76, 0.7)");
    try {
      await fetch(`/api/benchmarks/${currentBenchId}/stop`, { method: "POST" });
    } catch (err) {
      if (benchLog) benchLog.textContent = `Error stopping benchmark: ${err}`;
    }
  });
}
