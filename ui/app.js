const form = document.getElementById("run-form");
const zipInput = document.getElementById("zip");
const zipButton = document.getElementById("zip-button");
const zipDisplay = document.getElementById("zip-display");
const statusPill = document.getElementById("status-pill");
const logOutput = document.getElementById("log-output");
const progress = document.getElementById("progress");
const resetBtn = document.getElementById("reset");

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

let poller = null;

const pollJob = (jobId) => {
  if (poller) clearInterval(poller);
  progress.classList.remove("hidden");
  progress.indeterminate = true;
  setStatus("Running", "rgba(61, 214, 197, 0.5)");

  const tick = async () => {
    try {
      const statusRes = await fetch(`/api/jobs/${jobId}`);
      if (!statusRes.ok) return;
      const status = await statusRes.json();
      const logRes = await fetch(`/api/jobs/${jobId}/log`);
      const logText = await logRes.text();
      logOutput.textContent = logText || "Running...";

      if (status.status === "done") {
        setStatus("Done", "rgba(61, 214, 197, 0.6)");
        progress.classList.add("hidden");
        clearInterval(poller);
      } else if (status.status === "error") {
        setStatus("Error", "rgba(243, 179, 76, 0.7)");
        progress.classList.add("hidden");
        clearInterval(poller);
      }
    } catch (err) {
      logOutput.textContent = `Error fetching status: ${err}`;
    }
  };

  tick();
  poller = setInterval(tick, 2000);
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

resetBtn.addEventListener("click", () => {
  form.reset();
  zipDisplay.value = "";
  setStatus("Idle", "var(--outline)");
  logOutput.textContent = "Ready.";
  syncDisableGroups();
});
