# Roadmap

## Goals
- Keep runs reproducible, observable, and easy to troubleshoot.
- Avoid hidden state or UI-only behavior that drifts from the CLI.
- Scale to large exports without sacrificing reliability.

## Guiding principles
- Single source of truth for configuration and defaults.
- Append-only outputs; no destructive actions by default.
- Local-first processing with explicit dependency checks.
- Small, testable modules with clear contracts.

## Phase 1 — Core reliability and maintainability
- [x] Central `RunConfig` shared by CLI and UI.
- [x] Persist UI job state to disk for refresh-safe status.
- [x] Reconcile stale persisted `running` job states (dead PID recovery).
- [x] Guard transcription backend selection in the UI.
- [x] Cap UI log size and trim safely.
- [x] Stream zip uploads to disk (avoid loading large uploads into RAM).
- [x] Stop/abort terminates process trees (ffmpeg/whisper child processes).
- [x] Align UI/CLI defaults for transcription and OCR language (auto-friendly).
- [x] Auto-detect export root consistently for folder and zip inputs.
- [x] Transcript quality validation + end-of-run retry for flagged audio transcripts.
- [ ] Add explicit errors for missing media or permissions.
- [ ] Expand parser fixtures (PT/EN/iOS/Android edge cases).

## Phase 2 — Observability and UX clarity
- [x] Basics vs Advanced UI layout to reduce option overload.
- [x] Benchmark mode (sample-based speed/quality + estimate).
- [ ] Job history view (last N runs, status, outputs).
- [x] Coherent progress summaries including transcription backlog/retry phase.
- [x] Clear run end-state callouts (idle/running/done/error).
- [x] Surface backend availability directly in settings.

## Phase 3 — Performance and scale
- [ ] Stream messages instead of loading full chat into memory.
- [ ] Smarter batching for audio conversions and transcription.
- [ ] Optional cache for OCR/transcripts across runs.
- [ ] Concurrency tuning presets (CPU/GPU/low-memory).

## Phase 4 — Distribution and supportability
- [ ] Single-command installer per platform (Windows/macOS/Linux).
- [ ] Release packaging (pip + standalone binary).
- [ ] CI for tests and linting on each PR.
- [ ] Versioned schema for JSONL outputs.

## Phase 5 — Extensibility
- [ ] Plugin interface for new exporters or output formats.
- [ ] Hooks for external storage targets (S3/Drive).
- [ ] Optional data redaction profiles.

## Privacy and safety (ongoing)
- [ ] Make sensitive logging optional and minimized by default.
- [ ] Add a one-click redaction mode for output artifacts.
