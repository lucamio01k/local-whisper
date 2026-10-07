# Lab validation — 2026-10-06

Working branch: `benchmark-lab`; no changes to `main`. Standard application and experimental dependencies use separate virtual environments.

## Checks

`./scripts/check_quality.sh`: **84 Python tests, 2 frontend tests, production Vite build passed**. Python tests include existing transcription, diarization, quality review/export and worker management, plus conservative refinement, metrics/permutation, frozen input hashes, API persistence, missing interpreter, semantic crash/timeout and dataset conversion safeguards. No separate lint/typecheck command exists in frontend package scripts.

Manual UI: standard transcription screen, separate `/lab`, persisted comparison after server restart/refresh, model availability, detail selector/decision logs, experiment notes and narrow navigation checked. GLiNER/DiarizationLM correctly marked pending. Missing gold displayed as unavailable metrics.

## Real local benchmark

45-second Italian meeting excerpt from an existing local recording. **No human gold**; reference explicitly unverified. Whisper.cpp `large-v3-turbo`, quality profile, word attribution, Community-1 on CPU; Laya on CPU with two threads. Run `a055f38fe5114c62b034f3f9a1bf5913`, local artifacts in `lab-data/runs/` (ignored by Git).

| Variant | Time (s) | RTF | Peak tree RSS (GiB) | Refiner time (s) | Speaker changes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | 136.838 | 3.041 | 1.811 | 0 | 0 |
| + Acoustic | 129.567 | 2.879 | 1.710 | 0.730 | 0 |
| + Acoustic + Laya | 160.624 | 3.569 | 1.927 | 23.431 | 0 |

All variants completed. Acoustic inspected 15 ambiguous segments: 5 real embedding/cosine results, 9 mixed-window abstentions, 1 short-window abstention. Laya made 15 real structured semantic decisions. Conservative defaults accepted zero changes. WER, DER and WDER stayed `null`; no improvement claim.

Laya checkpoint: `convaiinnovations/laya-multilingual`, revision `1720e3e3357cfe1e281542e223f8273b0890ca34`, 678.2 MB. Offline SDK inference succeeded in isolated `.venv-lab`; SDK 0.3.28, torch 2.14.1, transformers 5.19.0. Encoder config comes from `jhu-clsp/mmBERT-base`; no second encoder weights downloaded. Normal environment retained torch 2.12.1 / pyannote.audio 4.0.5.

First sandbox-only attempt failed Metal buffer allocation; local execution with approved host access completed. Failed record retained for diagnosis. Main smoke began before semantic provenance hashing was added: its `semantic_model_info` is empty; future runs store runtime versions and checkpoint hashes.

Final semantic-worker recheck on the saved acoustic output: 15 real Laya decisions in 21.380 s, zero changes, no error, empty worker registry. SDK/runtime versions and SHA256 provenance returned correctly; checkpoint hash `9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204`. This recheck exercised the final subprocess/fallback implementation without rerunning ASR/Pyannote.

## Limits and next experiment

No active Lab/semantic/diarization/ASR worker and no Lab/semantic temporary directory remained at final cleanup check. Development backend/frontend intentionally remain running. Process exit releases model memory; a short smoke does **not** establish absence of long-run leaks or meeting-time responsiveness.

RSS sampled every 250 ms covers process tree, not all GPU/driver/unified allocations. Cold model processes still share uncontrolled OS filesystem cache. Single runs cannot attribute the faster acoustic total to refinement. RTF above 1 in this quality/CPU configuration is not real-time processing.

Next: prepare authorized, reviewed gold clips (paired AMI headset mix/far-field plus Italian conversation), freeze timing/scoring protocol and baseline, tune thresholds on separate meetings, then compare held-out WER/DER/WDER, repeated performance and responsiveness. Dataset download and a 2–4 hour human-gold suite remain future work; converter/manifest support is ready.
