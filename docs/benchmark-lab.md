# Benchmark / Lab

Branch `benchmark-lab`. Experimental route `/lab`; standard transcription, quality review and exports keep existing behavior. Local only; no automatic inference-time downloads. `lab-data/` and optional `.venv-lab/` are Git-ignored.

## Existing architecture and implementation choices

- `backend/main.py`: FastAPI, imports/audio normalization through FFmpeg, FIFO heavy-job queue, pause/cancel, process supervision, persisted jobs. Whisper backend selected between whisper.cpp (Metal) and faster-whisper; Qwen exists separately but isn't a Lab baseline in this milestone.
- `backend/asr_worker.py`, `qwen_worker.py` and whisper-cli: isolated ASR processes. Word timestamps depend on backend/profile/precision.
- `backend/engine.py`, `diarize_worker.py`: Community-1/3.1 worker, waveform decoded with FFmpeg (TorchCodec installation currently warns), explicit device choice, hashed turns cache, standard and exclusive timelines.
- `backend/quality.py`: reusable `assign_speakers`; summed temporal overlap, tie → `None`, exclusive timeline for attribution, standard timeline for overlap. Word groups preserve source text; missing/inconsistent timing gets diagnostics. Display labels follow chronology, not centroid row order.
- `backend/storage.py`, `review.py`, `exporting.py`: atomic revisions, explicit proposals/apply/restore, quality diagnostics, standard exports.
- `scripts/evaluate_quality.py`: existing frozen-reference evaluation, WER/DER and reference-word midpoint attribution. Common token normalization/edit-distance now shared with Lab.
- `backend/runner.py`: existing foreground pipeline with isolated scratch/history and cleanup. Lab reuses it in subprocesses; no duplicate ASR/diarization implementation.

No second embedding system added. Community-1 4.x public output exposes `speaker_embeddings` ordered by `speaker_diarization.labels()`. Crop extraction uses a small guarded adapter around internal `pipeline._embedding`; unsupported APIs degrade to original attribution. Normal caches never contain experimental refinements or embeddings.

## Run

```bash
./scripts/restart_lab.sh
./scripts/check_quality.sh
```

Open `http://localhost:5173/lab`. Import audio plus optional gold JSON, TXT transcription or RTTM. Tick **Gold verificato manualmente sull’audio** only for reviewed gold. Select dataset/configurations and run. Baseline always runs first; same explicit Whisper settings and Community-1 apply to every variant. Shared process gate serializes UI Lab, standalone CLI and ordinary heavy jobs; existing app FIFO remains active.

Table persists across refresh/restart; select a row for metrics, differences and full decisions. `degraded` identifies missing/failed requested refinement; never interpret it as a completed Laya/GLiNER experiment. Interrupted runs preserve completed variants. Cancellation/worker failure releases subprocesses; input/gold/result artifacts remain in Lab for reproducibility.

Startup recovery preserves runs owned by live API/CLI processes; dead-owner runs marked interrupted. UI cancellation of CLI runs persists cancellation request for supervisor. Lab API/CLI and ordinary heavy jobs share process gate, with application FIFO retained. Child ASR/diarization workers inherit parent ownership rather than reacquiring gate.

## Refinement contracts

`backend/refinement.py` defines `Evidence`, `Resolution`, `SpeakerRefiner`, detector and acceptance policy. Add a refiner by implementing `resolve(Evidence) -> Resolution` and optional `close()`, then register configuration in `lab.py` and selection in worker. Inputs include text, candidates, frozen neighboring turns, temporal score, acoustic evidence, duration/timestamps and ambiguity reasons. `UNKNOWN` is explicit refiner output; stored transcript unknown uses existing `None` convention.

High temporal coverage (≥ .9) with adequate margin, consistent words and no overlap bypasses refiners, even if a tiny secondary overlap exists. Other triggers reuse `speaker_ambiguity`, unknown and overlap diagnostics, low coverage, close temporal candidates, word/segment disagreement and brief uncertain segments. Baseline context remains frozen; no cascading semantic correction.

Acoustic resolver compares a center crop (default max 3 s, min 1.5 s) with Community-1 centroids in the same inference worker. It abstains on short/mixed/overlap windows and invalid embeddings. It deliberately avoids expanding a brief utterance into a neighboring voice. Cosine similarity is an uncalibrated score, not a posterior probability. A long ASR segment crossing multiple voices is not automatically relabeled from one crop; use word precision to separate it first.

Laya runs only on unresolved cases, in a separate semantic process after ASR and Pyannote exit. SDK is imported and model loaded only at first actual prediction. Text/context reaches semantic classifier; numeric temporal/acoustic scores don't. Closed candidates plus UNKNOWN, max 1024 tokens, CPU with selected resource-profile thread cap. SDK probabilities remain uncalibrated on speaker attribution; independent support and conflict veto required. UNKNOWN, low confidence and small margin retain original assignment (including `None`). Default change ≥ .85, semantic ≥ .9, decision margin ≥ .15; all adjustable in Lab. No normal-mode promotion.

Each analyzed case logs original/final/proposal, acceptance/change, resolver, score/confidence kind, thresholds, candidate scores, reasons, crop metadata and separate temporal/acoustic/semantic provenance. Full logs stay in Lab, not normal history. Accepted acoustic cases skip semantic model. No assumptions that alternating turns imply alternating speakers.

GLiNER `fastino/GLiNER2.5-multi-Decide` has a documented adapter stub: per-candidate score API/domain behavior still require validation. `QualityPostprocessor` separately reserves DiarizationLM's full-transcript contract; GGUF/llama.cpp prompt parsing and transcript-preserving speaker transfer not integrated. Rizzo Flow and WhisperDiari not added.

## Optional Laya environment

```bash
./scripts/setup_lab_models.sh
export LOCAL_WHISPER_SEMANTIC_PYTHON="$PWD/.venv-lab/bin/python"
export LOCAL_WHISPER_LAYA_PATH="$PWD/models_cache/lab/laya-multilingual"
./scripts/restart_lab.sh
```

Setup is explicit, downloads only multilingual checkpoint (678 MB observed) and small encoder configuration, records remote revisions; never downloads base encoder weights or corpus. Dedicated Python environment isolates experimental dependency versions from standard `.venv`. Required local files: `rl_agent_config.json`, `model.safetensors`, tokenizer files, `encoder/config.json`. Inference sets HF/Transformers offline. CPU chosen for low contention and tested SDK; MLX conversion and ONNX/Metal calibration are later experiments, not assumed equivalent runtimes.

Sources: [Laya model/limits](https://huggingface.co/convaiinnovations/laya-multilingual), [SDK](https://github.com/NandhaKishorM/laya), [GLiNER](https://huggingface.co/fastino/GLiNER2.5-multi-Decide), [Google DiarizationLM](https://github.com/google/speaker-id/tree/master/DiarizationLM).

## Metrics and reproducibility

- WER: lowercase Unicode word tokens, punctuation ignored, Levenshtein edits/reference token count. Gold `text` required. Empty reference → unavailable.
- `der`: product attribution timeline (returned segments), collar .25 s, overlap included, gold `turns` and explicit UEM/default full audio. Store missed speech, false alarm, confusion and raw seconds. Can exceed 100%; no speech denominator → unavailable.
- `pyannote_der`: raw standard Community-1 timeline, same DER protocol. Refinement operates product assignments, not raw diarizer turns; don't report its success as improving raw Pyannote DER.
- `wder`: **conditional speaker error among exact lexical matches** in deterministic diagonal-first Levenshtein alignment. Requires gold words/speakers matching gold text plus turns for optimal speaker permutation. ASR substitutions/deletions/insertions counted by WER, not this WDER denominator. Store aligned-correct count/errors; this is not interchangeable with every published WDER convention. Oversized alignment requires shorter clips.
- Word timeline speaker accuracy: separate reference-word midpoint metric, independent of ASR text. Requires timed gold words. Brief speakers included in mapping with zero collar. Partial UEM filters timed gold/hypothesis words by midpoint for WER/WDER too; untimed gold must describe selected complete clip. Frozen refinements retain baseline speaker mapping for WDER and attribution diagnostics; product DER keeps optimal mapping.
- Product divergences compare original/final label at each segment midpoint after global speaker mapping; classify correct/wrong correction, new/remaining error. Overlap/missing gold unscored. This local midpoint view doesn't replace word metrics or DER.
- Time: total pipeline seconds includes cold model loading, ASR, diarization and refiners; excludes scoring. RTF = time/audio duration. Store refiner extra time separately, plus full wall time including scoring.
- RAM: sampled **summed process-tree RSS** every 250 ms, including nested ASR/diarization/semantic workers. OS/GPU allocations and sub-sample spikes may be missed. If process inventory unavailable, clearly labeled max-process high-water RSS fallback. This is not complete 16 GB unified-memory accounting. CPU/GPU utilization omitted instead of guessing.

Record captures timestamp, dataset/audio/reference SHA256, scenario/channel, parameters, ASR config, commit, dirty state and dirty source fingerprint, installed standard package versions, model availability, metrics/performance and notes. Each variant cold model process; filesystem/model cache warmth uncontrolled, so repeat runs before drawing performance conclusions. Baseline pre-refinement segments saved per variant for exact change audit; run's first baseline supplies metric deltas. Refinement disabled leaves baseline unchanged.

16 GB target: sequential heavy processes, semantic model freed by exit, 10 GiB sampled RSS budget, 6-hour variant timeout, 10-minute semantic timeout. RSS budget cannot guarantee no swap/GPU memory pressure. Large files still decoded fully by existing Pyannote; use short controlled clips. No long-run memory-leak claim based on 45 s smoke.

## Dataset/reference format

`docs/lab/reference.example.json` and `manifest.example.json` show schema. Gold fields independent: text → WER; turns → DER; text + speaker words + turns → WDER; timed words additionally enable midpoint accuracy. Seconds relative to selected audio, half-open intervals, original speaker IDs consistent throughout clip. Multiple concurrent turns preserve overlap. `verified=false` allows performance but no quality scores. Audio/reference SHA256 frozen at import; edits require new import. Don't use ASR output as human gold.

```bash
./scripts/benchmark_lab.sh import /absolute/path/manifest.json
./scripts/benchmark_lab.sh run DATASET_ID --variants baseline acoustic acoustic_laya
```

Manifest uses local paths relative to manifest location. Imports deduplicate repeated inputs and hardlink matching canonical audio. Public preparation downloads individual files sequentially under total Lab disk budget.

Subset conversion example (output must not exist):

```bash
.venv/bin/python scripts/prepare_lab_subset.py \
  --audio /absolute/path/meeting.wav --annotation /absolute/path/meeting.rttm \
  --format rttm --start 30 --end 330 --output /absolute/path/subset \
  --corpus AMI --scenario 'EN far-field meeting' --channel Array1-01
```

AMI `--format ami`: pass one meeting's word XML directory only. Word intervals are a derived speech reference, not official RTTM; choose same turn/UEM protocol for comparisons. CHiME/DiPCo `--format chime`: requires aligned chosen-microphone times; refuses ambiguous multi-clock dictionaries and utterance-cutting crops without word times. RTTM provides no text/word gold. Utility never fabricates word timestamps; review output and set `verified=true` before import.

Default suite: approximately one hour, half IT and half EN. Two AMI participant groups and speaker-disjoint VoxForge contributor splits separate calibration/evaluation. Paired microphone recordings and original/degraded copies are correlated observations, not independent evidence.

| Corpus | Preparation / limit |
| --- | --- |
| [AMI](https://groups.inf.ed.ac.uk/ami/download/) | Select headset **mix** and array channel for same meeting/window; single person's close-talk audio cannot be scored against all speakers blindly. XML words or official speaker activity RTTM/UEM, explicit protocol. |
| [LibriCSS](https://github.com/chenzhuo1011/libri_css) | Selected 0S/0L, 10/20/30/40% mini-sessions, local audio plus prepared reference. Never run full upstream download script automatically. |
| [CHiME-6](https://www.chimechallenge.org/challenges/chime6/track1_data) | Synchronized microphone clock, local JSON/RTTM/UEM, noisy far-field stress cases. |
| [DiPCo](https://www.chimechallenge.org/challenges/chime7/task1/data) | Selected dinner-party dev clips; local scoring JSON, same timing clock/protocol. |
| [TIGR](https://sharetigr.usi.ch/en/st/corpus) | Manual transcripts/ELAN/TEI and authorized multimedia; normalize selected participant/timeline annotations, inspect overlap. |
| [KIParla](https://kiparla.it/search/) | Transcript access does not imply downloadable audio. Site restricts downloading; import only separately authorized local recordings. No automated fetching/access workaround. |
| [ASR-ItaCSC](https://magichub.com/datasets/italian-conversational-speech-corpus/) | Official description lists mobile recordings, WAV/TXT and 10.43 h transcripts. No speaker-time gold confirmed: WER use first; DER/WDER only after inspecting actual authorized annotations. |

## Validation status

`./scripts/check_quality.sh` runs standard and Lab tests plus frontend tests/build. Lab tests cover safe-case bypass, UNKNOWN/margins/conflicts, acoustic crop/cosine, lazy closed-choice SDK contract, reference corruption, missing gold, metric permutation, failure cleanup and persistent HTTP workflow. Actual smoke outcomes recorded in `docs/lab/validation.md`; this foundation isn't a claim that any refiner improves diarization. Public suite and frozen comparisons available; results in `docs/lab/public-suite-validation.md`. No refiner promotion without held-out improvement.


## Public suite and frozen comparisons

```bash
./scripts/prepare_lab_datasets.sh
./scripts/benchmark_lab.sh run --suite public-it-en --selection small --mode frozen \
  --resource-profile light --budget-seconds 1800 --variants baseline acoustic acoustic_laya
./scripts/benchmark_lab.sh run DATASET_ID --selection complete --mode complete \
  --repetitions 3 --budget-seconds 1800 --variants baseline acoustic_laya
```

Preparation: ~29.6 min AMI EN (ES2002a/ES2003a headset mix and Array1-01), ~30.2 min IT (14 VoxForge contributors, originals plus one deterministic noise/reverb/compression copy). GPL corpus/CC BY 4.0 provenance, source archive hashes, genuine manual word timing, manual AMI transcription-segment turns. DER uses these turns with 250 ms collar and overlap included; not official AMI SAD protocol. VoxForge has utterance text, no speaker-time gold. No artificial concatenated conversations. Source media/archives removed only after canonical extraction; completed clips reused. Interrupted transfers retain `.part`, URL/ETag/size state and resume using HTTP Range. Download, extraction and scratch reservations bounded by 2,000,000,000 bytes; installed models excluded.

Small selection targets 120 s, medium 600 s, complete all selected suite. Seed 42, equal language/scenario/split quotas, speech-bearing timed windows; untimed utterances remain whole, so total can fall slightly below target. Custom single-audio interval requires usable timed reference or whole utterance boundaries. Empty scoring regions and missing metrics remain absent. Gold coverage and micro-averages use counts/denominators. Original/degraded and calibration/evaluation charts separate by default.

Frozen preparation runs ASR/Pyannote and collects acoustic observations once, seals immutable `lab-data/snapshots/<key>/snapshot.json` with SHA256. Baseline/acoustic/Laya always use original transcript/turns/context. Semantic stage sees original context and only unresolved acoustic cases; accepted acoustic decisions merged afterward. Acceptance thresholds/context reuse snapshot; changes to ASR/diarization, detector margin, minimum audio/window, resource threads or inference implementation/model revisions prepare new snapshot. `GET /api/lab/snapshots`; CLI `--snapshot-key` requires compatible single-clip snapshot. WER and raw Pyannote DER invariant; product DER/WDER assess attribution. Frozen equivalent pipeline time includes shared preparation plus replay; actual run time, preparation reuse and added refiner time reported separately. RSS reports maximum across preparation/replay, not sum of sequential processes; GPU allocations partly excluded.

Resources: light 2 threads/8 GiB RSS, balanced 4/10, heavy up to 8/10, capped by cores. Distinct from ASR quality profile. Single heavy Lab process across API and CLI via FIFO/file lock. Deadline starts after queue acquisition and includes imports/model prep/inference/scoring, excludes dataset download. On expiry, terminate worker/process groups, preserve completed results, mark partial; shutdown grace may add seconds. Complete mode accepts baseline plus one finalist, 1–3 paired repetitions from audio. Profiles measure current Mac, not simulated hardware.

API: `/api/lab/suites`, `/prepare`, `/selection`, `/snapshots`, `/datasets/{id}/audio` (Range), `/datasets/{id}/reference`, `/runs`, `/runs/{id}/export?format=json|csv`. Exports contain config, provenance, selection seed/interval, scoring revision, counts and metrics. CLI JSON/CSV export: `./scripts/benchmark_lab.sh export RUN_ID --output /absolute/path/report.json` (or `.csv`). UI at `/lab` offers local audio, interval drilldown, reference/baseline/refined output, decision logs, quality/delta bars, cost bars and WDER/refiner-time scatter.

TIGR authenticated files remain external dependency. Import actual Sony audio plus ELAN, choose transcription tiers explicitly (avoid coding/translation tiers). REF annotations inherit genuine parent interval; preserve speaker/overlap. Recognized anonymization/inaudible markers excluded, with explicit exclusion file for muted names lacking markers. Entire affected utterances excluded when no word timing exists. Review audio/annotation anonymization before enabling quality metrics:

```bash
./scripts/benchmark_lab.sh import-elan --audio /absolute/path/event6a.wav \
  --annotation /absolute/path/event6a.eaf --tiers SPEAKER_TIER_1 SPEAKER_TIER_2 \
  --group tigr-event6a --split evaluation --interval START END \
  --exclude /absolute/path/muted-intervals.json --reviewed-anonymization --replace-it
```

Interval must use whole ELAN utterance boundaries, max 30 min. `--replace-it` replaces equal provisional IT duration in suite membership (canonical files preserved for prior runs); total occupancy still capped. Without anonymization review, imported reference remains unverified. No signing research agreement or fetching gated files without authenticated user session.
