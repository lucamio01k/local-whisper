import asyncio
import copy
import threading
from functools import wraps
from contextlib import asynccontextmanager
from collections import deque
from backend.storage import atomic_json, active, commit, legacy_version, read_revision, LOCK, digest_file
from backend.quality import cpp_time, cpp_words, diagnostics, reading_segments, subtitle_segments, PIPELINE_VERSION
from backend.exporting import render as render_export
import concurrent.futures
import inspect
import json
import os
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
import platform
import re
import select
import signal
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from fastapi import BackgroundTasks, Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

@asynccontextmanager
async def lifespan(application):
    yield
    for job in jobs.values():
        if _is_job_active(job):
            job['cancel_requested'] = True
            job['pause_requested'] = False
    for proc in list(job_processes.values()):
        if os.name == 'posix' and proc.poll() is None:
            try: _signal_process(proc, signal.SIGCONT)
            except ProcessLookupError: pass
        _terminate_process(proc)


app = FastAPI(title="Local Whisper", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Directories ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
MODELS_DIR = BASE_DIR / "models_cache"
UPLOAD_DIR = BASE_DIR / "uploads"
CONFIG_FILE = BASE_DIR / "config.json"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB
UPLOAD_CHUNK_BYTES = 1024 * 1024
ALLOWED_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".mp4", ".mov", ".ogg", ".opus", ".webm"}
DIARIZATION_MODELS = {
    "community-1": "pyannote/speaker-diarization-community-1",
    "3.1": "pyannote/speaker-diarization-3.1",
}
DEFAULT_DIARIZATION_MODEL = "community-1"
DEFAULT_DIARIZATION_MODE = "conservative"
DEFAULT_DIARIZATION_START = "auto"
DEFAULT_DIARIZATION_DEVICE = "auto"
DIARIZATION_START_MODES = {"auto", "after", "off"}
DIARIZATION_DEVICES = {"auto", "cpu", "mps"}
DEFAULT_TRANSCRIPTION_BACKEND = "faster_whisper"
TRANSCRIPTION_BACKENDS = {
    "faster_whisper": {
        "label": "faster-whisper",
        "description": "Backend attuale CTranslate2. Stabile, usa CPU su Apple Silicon.",
    },
    "whisper_cpp": {
        "label": "whisper.cpp",
        "description": "Backend alternativo Metal/Core ML. Richiede whisper-cli e modelli GGML.",
    },
}
DEFAULT_PERFORMANCE_PROFILE = "balanced"
PERFORMANCE_PROFILES = {
    "fast": {
        "label": "Veloce",
        "beam_size": 1,
        "best_of": 1,
        "word_timestamps": False,
    },
    "balanced": {
        "label": "Bilanciato",
        "beam_size": 3,
        "best_of": 3,
        "word_timestamps": False,
    },
    "quality": {
        "label": "Qualita",
        "beam_size": 5,
        "best_of": 5,
        "word_timestamps": True,
    },
}

MODELS_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
WHISPER_CPP_MODELS_DIR = MODELS_DIR / "whisper.cpp"
WHISPER_CPP_MODELS_DIR.mkdir(exist_ok=True)

# ── In-memory job store ───────────────────────────────────────────────────────
jobs: Dict[str, Dict[str, Any]] = {}
job_processes: Dict[str, subprocess.Popen] = {}


def _set_job_control_defaults(job: Dict[str, Any]) -> None:
    job.setdefault("pause_requested", False)
    job.setdefault("cancel_requested", False)
    job.setdefault("pausable", False)
    job.setdefault("cancelable", False)


def _is_job_active(job: Dict[str, Any]) -> bool:
    return job.get("status") in ("queued", "downloading_yt", "processing", "paused")


def _check_canceled(job: Dict[str, Any]) -> None:
    if job.get("cancel_requested"):
        raise RuntimeError("Operazione annullata")


def _wait_if_paused(job: Dict[str, Any]) -> None:
    previous_stage = job.get("stage") or "processing"
    while job.get("pause_requested") and not job.get("cancel_requested"):
        job["status"] = "paused"
        job["stage"] = "paused"
        job["previous_stage"] = previous_stage
        job["message"] = "In pausa…"
        time.sleep(0.5)
    _check_canceled(job)
    if job.get("status") == "paused":
        job["status"] = "processing"
        job["stage"] = job.get("previous_stage", previous_stage)


def _terminate_process(proc: subprocess.Popen, force: bool = False) -> None:
    if proc.poll() is not None:
        return
    if os.name == "posix":
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
    else:
        if force:
            proc.kill()
        else:
            proc.terminate()


def _signal_process(proc: subprocess.Popen, sig: int) -> None:
    if proc.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(proc.pid, sig)


def _tail_text(path: Path, max_chars: int = 4000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(errors="replace")
    return text[-max_chars:]


class _SilentYtDlpLogger:
    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass

# ── Whisper model catalogue ───────────────────────────────────────────────────
WHISPER_MODELS = [
    "tiny", "base", "small", "medium",
    "large-v2", "large-v3", "large-v3-turbo",
]

APPROX_SIZES_MB = {
    "tiny": 75, "base": 145, "small": 465, "medium": 1500,
    "large-v2": 3100, "large-v3": 3100, "large-v3-turbo": 800,
}

# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    default = {
        "glossary": "",
        "diarization_precision": "segments",
        "hf_token": "",
        "default_model": "small",
        "transcription_backend": DEFAULT_TRANSCRIPTION_BACKEND,
        "diarization_enabled": True,
        "diarization_start": DEFAULT_DIARIZATION_START,
        "diarization_device": DEFAULT_DIARIZATION_DEVICE,
        "diarization_model": DEFAULT_DIARIZATION_MODEL,
        "diarization_mode": DEFAULT_DIARIZATION_MODE,
        "performance_profile": DEFAULT_PERFORMANCE_PROFILE,
    }
    if CONFIG_FILE.exists():
        try:
            loaded = json.loads(CONFIG_FILE.read_text())
            return {**default, **loaded}
        except Exception:
            pass
    return default


def save_config(config: dict) -> None:
    cleaned = dict(config)
    cleaned.pop("hf_token_set", None)
    atomic_json(CONFIG_FILE, cleaned)


# ── Model helpers ─────────────────────────────────────────────────────────────

def _model_hf_id(model_name: str) -> str:
    if model_name == "large-v3-turbo":
        return "deepdml/faster-whisper-large-v3-turbo-ct2"
    return f"Systran/faster-whisper-{model_name}"


def _validate_model_name(model_name: str) -> None:
    if model_name not in WHISPER_MODELS:
        raise HTTPException(400, f"Modello non supportato: {model_name}")


def _resolve_performance_profile(profile: Optional[str]) -> tuple[str, dict]:
    profile_key = profile if profile in PERFORMANCE_PROFILES else DEFAULT_PERFORMANCE_PROFILE
    return profile_key, PERFORMANCE_PROFILES[profile_key]


def _resolve_transcription_backend(backend: Optional[str]) -> str:
    return backend if backend in TRANSCRIPTION_BACKENDS else DEFAULT_TRANSCRIPTION_BACKEND


def _resolve_diarization_start(value: Optional[str], enabled: bool = True) -> str:
    if not enabled:
        return "off"
    return value if value in DIARIZATION_START_MODES else DEFAULT_DIARIZATION_START


def _resolve_diarization_device(value: Optional[str]) -> str:
    return value if value in DIARIZATION_DEVICES else DEFAULT_DIARIZATION_DEVICE


def _normalize_expected_speakers(value: Optional[Any]) -> Optional[int]:
    if value in (None, "", 0, "0"):
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, "Numero speaker non valido")
    if count < 1 or count > 20:
        raise HTTPException(400, "Numero speaker deve essere tra 1 e 20")
    return count


def _diarization_turns(diarization: Any) -> List[tuple]:
    annotation = getattr(diarization, "speaker_diarization", diarization)
    if not hasattr(annotation, "itertracks"):
        raise RuntimeError(
            f"Output diarizzazione non supportato: {type(diarization).__name__}"
        )
    return [
        (turn.start, turn.end, speaker)
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]


def _merge_adjacent_same_speaker_segments(
    segments: List[dict],
    max_gap: float = 2.0,
) -> List[dict]:
    if not segments:
        return segments

    merged: List[dict] = []
    for seg in segments:
        speaker = seg.get("speaker")
        previous = merged[-1] if merged else None
        gap = (
            float(seg.get("start", 0)) - float(previous.get("end", 0))
            if previous else None
        )
        if (
            previous
            and speaker
            and previous.get("speaker") == speaker
            and gap is not None
            and gap <= max_gap
        ):
            previous["end"] = seg.get("end", previous.get("end"))
            previous["text"] = " ".join(
                part.strip()
                for part in (previous.get("text", ""), seg.get("text", ""))
                if part and part.strip()
            )
            if previous.get("words") or seg.get("words"):
                previous["words"] = [
                    *(previous.get("words") or []),
                    *(seg.get("words") or []),
                ]
        else:
            merged.append({**seg})

    for idx, seg in enumerate(merged, 1):
        seg["id"] = idx
    return merged


def is_model_downloaded(model_name: str) -> bool:
    model_id = _model_hf_id(model_name)
    cache_key = "models--" + model_id.replace("/", "--")
    model_dir = MODELS_DIR / cache_key
    if not model_dir.exists():
        return False
    return bool(list(model_dir.glob("snapshots/*/model.bin")))


# ── Device detection ──────────────────────────────────────────────────────────

def detect_device() -> tuple[str, str]:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", "float16"
    except ImportError:
        pass
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "auto", "int8"
    return "cpu", "int8"


# ── Persistence helpers ───────────────────────────────────────────────────────

def _job_dir(job_id: str) -> Path:
    return UPLOAD_DIR / job_id


def _find_audio_file(job_id: str) -> Optional[Path]:
    d = _job_dir(job_id)
    if not d.exists():
        return None
    for ext in ("mp3", "wav", "m4a", "mp4", "mov", "ogg", "opus", "webm"):
        p = d / f"audio.{ext}"
        if p.exists():
            return p
    for p in d.iterdir():
        if p.is_file() and p.stem == "audio":
            return p
    return None


def _audio_duration(audio_path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(audio_path)], check=True, capture_output=True, text=True)
    return float(json.loads(result.stdout)["format"]["duration"])


def _prepare_diarization_audio(job_id: str, audio_path: str) -> str:
    started = time.monotonic()
    directory = _job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / "normalized.wav"
    marker = directory / "normalized.json"
    signature = {"sha256": digest_file(audio_path), "rate": 16000, "channels": 1, "codec": "pcm_s16le"}
    if not dest.exists() or not marker.exists() or json.loads(marker.read_text()) != signature:
        temp = directory / (uuid.uuid4().hex + ".wav")
        try:
            subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(audio_path), "-ac", "1", "-ar", "16000", "-vn", "-c:a", "pcm_s16le", str(temp)], check=True, capture_output=True)
            os.replace(temp, dest)
            atomic_json(marker, signature)
        finally:
            temp.unlink(missing_ok=True)
    job = jobs[job_id]
    job.setdefault("metrics", {})["conversion_seconds"] = round(time.monotonic() - started, 3)
    job["duration"] = _audio_duration(dest)
    return str(dest)


def _load_diarization_waveform(audio_path: str):
    """Decode diarization input with system FFmpeg, independent of TorchCodec."""
    import torch

    cmd = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", audio_path,
        "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1",
    ]
    try:
        decoded = subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg non trovato: necessario per diarizzazione") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Impossibile decodificare audio per diarizzazione: {detail}") from exc

    if not decoded.stdout:
        raise RuntimeError("Audio vuoto dopo conversione per diarizzazione")

    waveform = torch.frombuffer(bytearray(decoded.stdout), dtype=torch.float32).unsqueeze(0)
    return {"waveform": waveform, "sample_rate": 16000, "uri": Path(audio_path).stem}


def save_job_to_disk(job_id: str) -> None:
    with LOCK:
        _save_job_locked(job_id)


def _save_job_locked(job_id):
    job = jobs.get(job_id)
    if not job:
        return
    d = _job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    segments = job.get('segments', [])
    transcript_file = d / 'transcript.json'
    meta = {
        **{k: job.get(k) for k in QUALITY_FIELDS},
        "id": job_id,
        "title": job.get("title") or job.get("filename", job_id),
        "filename": job.get("filename", ""),
        "created_at": job.get("created_at", datetime.now(timezone.utc).isoformat()),
        "model": job.get("model", ""),
        "transcription_backend": job.get("transcription_backend", DEFAULT_TRANSCRIPTION_BACKEND),
        "language": job.get("language", ""),
        "duration": job.get("duration"),
        "has_audio": _find_audio_file(job_id) is not None,
        "has_transcript": transcript_file.exists(),
        "has_diarization": (d / "diarized.json").exists(),
        "youtube_url": job.get("youtube_url", ""),
        "diarization_error": job.get("diarization_error"),
        "diarization_model": job.get("diarization_model"),
        "diarization_mode": job.get("diarization_mode"),
        "diarization_start": job.get("diarization_start", DEFAULT_DIARIZATION_START),
        "diarization_device": job.get("diarization_device", DEFAULT_DIARIZATION_DEVICE),
        "performance_profile": job.get("performance_profile", DEFAULT_PERFORMANCE_PROFILE),
        "transcription_options": job.get("transcription_options", {}),
        "metrics": job.get("metrics", {}),
    }
    current = active(d)
    legacy_file = d / 'diarized.json' if (d / 'diarized.json').exists() else transcript_file
    previous = current['segments'] if current else json.loads(legacy_file.read_text()) if legacy_file.exists() else []
    # Snapshot legacy metadata before the first update, not after changing it.
    if not current and previous:
        previous_meta = json.loads((d / 'meta.json').read_text()) if (d / 'meta.json').exists() else meta
        baseline_version = legacy_version(d, previous)
        atomic_json(d / 'revisions' / (baseline_version + '.json'), {
            'version': baseline_version, 'parent': None, 'created_at': datetime.now(timezone.utc).isoformat(),
            'reason': 'legacy', 'segments': previous, 'metadata': previous_meta})
    if segments and (not current or current['segments'] != segments or current.get('metadata') != meta):
        record = commit(d, segments, meta, job.pop('_revision_reason', 'processing'), previous=previous)
        job['version'] = record['version']
    elif current:
        job['version'] = current['version']
    job['diagnostics'] = diagnostics(segments)
    # HEAD is already durable. A mirror failure must not report an uncommitted
    # operation or roll back memory to a version different from HEAD.
    try:
        atomic_json(d / 'meta.json', meta)
        cache_key = job.get('diarization_cache_key')
        if job.get('turns') and isinstance(cache_key, str) and re.fullmatch(r'[0-9a-f]{64}', cache_key):
            cache_path = d / 'turns_cache' / (cache_key + '.json')
            if not cache_path.exists(): atomic_json(cache_path, job['turns'])
        if not transcript_file.exists() and segments:
            atomic_json(transcript_file, job.get('raw_segments') or [{**seg, 'speaker': None} for seg in segments])
        if job.get('diarization_ran'):
            atomic_json(d / 'diarized.json', segments)
    except OSError as exc:
        if not active(d): raise
        job['persistence_warning'] = 'Revisione salvata; copia compatibilità non aggiornata: ' + str(exc)


def _load_job_from_disk(job_id: str) -> bool:
    with LOCK:
        return _load_job_locked(job_id)


def _load_job_locked(job_id: str) -> bool:
    d = _job_dir(job_id)
    meta_file = d / "meta.json"
    if not meta_file.exists() and not (d / "HEAD.json").exists():
        return False
    meta = json.loads(meta_file.read_text()) if meta_file.exists() else active(d)["metadata"]

    request_file = d / 'diarization_request.json'
    if 'expected_speakers' not in meta and request_file.exists():
        try:
            request = json.loads(request_file.read_text())
            meta['expected_speakers'] = request.get('expected_speakers')
        except (OSError, ValueError): pass
    segments: List[dict] = []
    loaded_diarized = False
    for fname in ("diarized.json", "transcript.json"):
        seg_file = d / fname
        if seg_file.exists():
            segments = json.loads(seg_file.read_text())
            loaded_diarized = fname == "diarized.json"
            break

    revision = active(d)
    if revision:
        segments = revision['segments']
        meta.update(revision.get('metadata', {}))
    audio_path = _find_audio_file(job_id)
    existing = jobs.get(job_id, {})
    jobs[job_id] = {
        **existing,
        **{k: meta.get(k) for k in QUALITY_FIELDS},
        "version": revision['version'] if revision else legacy_version(d, segments),
        "diagnostics": diagnostics(segments),
        "status": "done",
        "stage": "done",
        "progress": 100,
        "message": existing.get("message", ""),
        "segments": segments,
        "language": meta.get("language"),
        "duration": meta.get("duration"),
        "error": None,
        "audio_url": f"/api/audio/{job_id}" if audio_path else None,
        "filename": meta.get("filename", ""),
        "title": meta.get("title", ""),
        "model": meta.get("model", ""),
        "transcription_backend": meta.get("transcription_backend", DEFAULT_TRANSCRIPTION_BACKEND),
        "created_at": meta.get("created_at", ""),
        "youtube_url": meta.get("youtube_url", ""),
        "diarization_ran": (d / "diarized.json").exists(),
            "diarization_error": meta.get("diarization_error"),
            "diarization_model": meta.get("diarization_model", DEFAULT_DIARIZATION_MODEL),
            "diarization_mode": meta.get("diarization_mode", DEFAULT_DIARIZATION_MODE),
            "diarization_start": meta.get("diarization_start", DEFAULT_DIARIZATION_START),
            "diarization_device": meta.get("diarization_device", DEFAULT_DIARIZATION_DEVICE),
            "performance_profile": meta.get("performance_profile", DEFAULT_PERFORMANCE_PROFILE),
        "transcription_options": meta.get("transcription_options", {}),
        "metrics": meta.get("metrics", {}),
    }
    _set_job_control_defaults(jobs[job_id])
    return True


def load_jobs_from_disk() -> None:
    for d in sorted(UPLOAD_DIR.iterdir(), key=lambda p: p.stat().st_mtime):
        if not d.is_dir() or d.name.startswith("_work"):
            continue
        meta_file = d / "meta.json"
        if not meta_file.exists() and not (d / "HEAD.json").exists():
            continue
        try:
            _load_job_from_disk(d.name)
        except Exception as e:
            print(f"[warn] Errore caricamento job {d.name}: {e}")


QUALITY_FIELDS = ("glossary", "diarization_precision", "min_speakers", "max_speakers", "expected_speakers", "raw_segments", "turns", "diarization_actual_model", "diarization_actual_device", "diarization_cache_key")


def _validate_quality_options(precision, glossary, exact=None, minimum=None, maximum=None):
    if precision not in ('segments', 'words'):
        raise HTTPException(400, 'Precisione non valida')
    if not isinstance(glossary, str) or len(glossary) > 4000:
        raise HTTPException(400, 'Glossario: massimo 4000 caratteri')
    if exact:
        return
    for value in (minimum, maximum):
        if value is not None and (not isinstance(value, int) or not 1 <= value <= 20):
            raise HTTPException(400, 'Intervallo speaker: valori fra 1 e 20')
    if minimum and maximum and minimum > maximum:
        raise HTTPException(400, 'Minimo speaker maggiore del massimo')


_HEAVY_CONDITION = threading.Condition()
_HEAVY_QUEUE = deque()
_HEAVY_BUSY = False
_HEAVY_LOCAL = threading.local()


def heavy_job(function):
    @wraps(function)
    def queued(job_id, *args, **kwargs):
        global _HEAVY_BUSY
        if getattr(_HEAVY_LOCAL, 'running', False):
            return function(job_id, *args, **kwargs)
        ticket = object()
        job = jobs[job_id]
        with _HEAVY_CONDITION:
            _HEAVY_QUEUE.append(ticket)
            job.update(status='queued', stage='queued', message='In coda…', cancelable=True)
            while _HEAVY_BUSY or _HEAVY_QUEUE[0] is not ticket:
                if job.get('cancel_requested'):
                    _HEAVY_QUEUE.remove(ticket)
                    job.update(status='done' if job.get('segments') else 'canceled', stage='canceled', cancelable=False)
                    _HEAVY_CONDITION.notify_all()
                    return
                _HEAVY_CONDITION.wait(.2)
            _HEAVY_QUEUE.popleft()
            _HEAVY_BUSY = True
        _HEAVY_LOCAL.running = True
        previous = copy.deepcopy(job.get('segments', []))
        try:
            if job.get('cancel_requested'):
                job.update(status='done' if previous else 'canceled', stage='canceled', cancelable=False)
                return
            return function(job_id, *args, **kwargs)
        finally:
            if job.get('status') in ('error', 'canceled') and previous:
                job['segments'] = previous
            _HEAVY_LOCAL.running = False
            with _HEAVY_CONDITION:
                _HEAVY_BUSY = False
                _HEAVY_CONDITION.notify_all()
    return queued


# Load persisted jobs at startup
load_jobs_from_disk()


# ── Diarization helper (reusable) ─────────────────────────────────────────────

def _apply_diarization(*args, **kwargs):
    from backend.engine import infer_turns
    from backend.quality import assign_speakers
    turns = infer_turns(*args, **kwargs)
    return assign_speakers(args[1], turns["standard"], turns["exclusive"])


def _speaker_stats(segments: List[dict]) -> Dict[str, Dict[str, Any]]:
    stats: Dict[str, Dict[str, Any]] = {}
    for seg in segments:
        sp = seg.get("speaker")
        if not sp:
            continue
        item = stats.setdefault(sp, {"speaker": sp, "segments": 0, "duration": 0.0})
        item["segments"] += 1
        item["duration"] += max(0.0, float(seg.get("end", 0)) - float(seg.get("start", 0)))
    return stats


def _speaker_neighbor_scores(segments: List[dict], speaker: str) -> Dict[str, int]:
    scores: Dict[str, int] = {}
    for idx, seg in enumerate(segments):
        if seg.get("speaker") != speaker:
            continue
        for neighbor in (idx - 1, idx + 1):
            if neighbor < 0 or neighbor >= len(segments):
                continue
            other = segments[neighbor].get("speaker")
            if other and other != speaker:
                scores[other] = scores.get(other, 0) + 1
    return scores


def _merge_speaker_into(segments: List[dict], source: str, target: str) -> List[dict]:
    return [
        {**seg, "speaker": target} if seg.get("speaker") == source else seg
        for seg in segments
    ]


def _conservative_merge_speakers(segments: List[dict]) -> List[dict]:
    return copy.deepcopy(segments)


def _run_diarization_worker(
    job_id: str,
    diarization_audio: str,
    raw_segments: List[dict],
    hf_token: str,
    expected_speakers: Optional[int] = None,
    diarization_model: str = DEFAULT_DIARIZATION_MODEL,
    diarization_mode: str = DEFAULT_DIARIZATION_MODE,
    diarization_device: str = DEFAULT_DIARIZATION_DEVICE,
) -> List[dict]:
    job = jobs[job_id]
    d = _job_dir(job_id)
    d.mkdir(exist_ok=True)

    segments_path = d / "diarization_segments.json"
    request_path = d / "diarization_request.json"
    result_path = d / "diarization_result.json"
    stdout_path = d / "diarization_stdout.log"
    stderr_path = d / "diarization_stderr.log"

    segments_path.write_text(json.dumps(raw_segments, ensure_ascii=False))
    request_path.write_text(json.dumps({
        "audio_path": diarization_audio,
        "segments_path": str(segments_path),
        "expected_speakers": expected_speakers,
        "diarization_model": diarization_model,
        "diarization_mode": diarization_mode,
        "diarization_device": diarization_device,
        "diarization_precision": job.get("diarization_precision", "segments"),
        "min_speakers": job.get("min_speakers"),
        "max_speakers": job.get("max_speakers"),
    }, ensure_ascii=False))
    result_path.unlink(missing_ok=True)

    env = {**os.environ, "HF_TOKEN": hf_token, "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1"}
    cmd = [
        sys.executable,
        "-m",
        "backend.diarize_worker",
        "--input",
        str(request_path),
        "--output",
        str(result_path),
    ]

    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=(os.name == "posix"),
        )
        job_processes[job_id] = proc

        paused_process = False
        try:
            while proc.poll() is None:
                if job.get("cancel_requested"):
                    job["message"] = "Annullamento diarizzazione…"
                    if paused_process and os.name == "posix":
                        _signal_process(proc, signal.SIGCONT)
                        paused_process = False
                    _terminate_process(proc)
                    try:
                        proc.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        _terminate_process(proc, force=True)
                        proc.wait(timeout=3)
                    raise RuntimeError("Diarizzazione annullata")

                if job.get("pause_requested"):
                    if not paused_process and os.name == "posix":
                        _signal_process(proc, signal.SIGSTOP)
                        paused_process = True
                    job["status"] = "paused"
                    job["stage"] = "paused"
                    job["previous_stage"] = "diarizing"
                    job["message"] = "Diarizzazione in pausa…"
                else:
                    if paused_process and os.name == "posix":
                        _signal_process(proc, signal.SIGCONT)
                        paused_process = False
                    if job.get("status") == "paused":
                        job["status"] = "processing"
                        job["stage"] = "diarizing"
                        job["message"] = "Diarizzazione in corso…"

                time.sleep(0.5)

            if paused_process and os.name == "posix":
                _signal_process(proc, signal.SIGCONT)

            if not result_path.exists():
                err = _tail_text(stderr_path) or "Il worker diarizzazione non ha prodotto un risultato."
                raise RuntimeError(err.strip())

            result = json.loads(result_path.read_text())
            if not result.get("ok"):
                detail = result.get("error") or _tail_text(stderr_path) or "Diarizzazione fallita"
                raise RuntimeError(detail)
            job["turns"] = result.get("turns")
            job["diarization_cache_key"] = result.get("cache_key")
            job.setdefault("metrics", {}).update({
                "diarization_cache_hit": result.get("cache_hit", False),
                "speaker_assignment_seconds": result.get("assignment_seconds", 0),
                "diarization_model_load_seconds": 0 if result.get("cache_hit") else result.get("turns", {}).get("load_seconds"),
                "diarization_inference_seconds": 0 if result.get("cache_hit") else result.get("turns", {}).get("inference_seconds"),
            })
            job["diarization_actual_device"] = result.get("turns", {}).get("device")
            job["diarization_actual_model"] = result.get("turns", {}).get("model")
            return result["segments"]
        finally:
            job_processes.pop(job_id, None)


def _whisper_cpp_binary() -> Optional[Path]:
    env_bin = os.environ.get("WHISPER_CPP_BIN")
    path_bin = shutil.which("whisper-cli")
    candidates = [
        Path(env_bin) if env_bin else None,
        BASE_DIR / "whisper.cpp" / "build" / "bin" / "whisper-cli",
        BASE_DIR / "whisper.cpp" / "build" / "bin" / "main",
        BASE_DIR / "whisper.cpp" / "main",
        Path(path_bin) if path_bin else None,
    ]
    for candidate in candidates:
        if candidate and candidate.exists() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _whisper_cpp_model_path(model_name: str) -> Optional[Path]:
    env_model = os.environ.get("WHISPER_CPP_MODEL")
    if env_model:
        path = Path(env_model)
        if path.exists():
            return path
    candidates = [
        WHISPER_CPP_MODELS_DIR / f"ggml-{model_name}.bin",
        WHISPER_CPP_MODELS_DIR / f"{model_name}.bin",
    ]
    if model_name == "large-v3-turbo":
        candidates.append(WHISPER_CPP_MODELS_DIR / "ggml-large-v3-turbo-q5_0.bin")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _parse_timestamp_seconds(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > 1000 else float(value)
    if not isinstance(value, str):
        return None
    match = re.match(r"(?:(\d+):)?(\d+):(\d+)[,.](\d+)", value.strip())
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = int(match.group(4)[:3].ljust(3, "0"))
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def _whisper_cpp_segment_time(seg: dict, key: str) -> Optional[float]:
    return cpp_time(seg, key)


def _parse_whisper_cpp_progress(line: str) -> Optional[int]:
    match = re.search(r"progress\s*=\s*(\d{1,3})%", line)
    if not match:
        return None
    return max(0, min(100, int(match.group(1))))


def _prepare_whisper_cpp_audio(job_id: str, audio_path: str) -> str:
    return _prepare_diarization_audio(job_id, audio_path)


def _run_whisper_cpp(
    job_id: str,
    audio_path: str,
    model_name: str,
    language: Optional[str],
    profile: dict,
    progress,
) -> tuple[List[dict], dict]:
    binary = _whisper_cpp_binary()
    if not binary:
        raise RuntimeError(
            "whisper.cpp non configurato: installa whisper-cli o imposta WHISPER_CPP_BIN."
        )
    model_path = _whisper_cpp_model_path(model_name)
    if not model_path:
        raise RuntimeError(
            f"Modello whisper.cpp mancante: aggiungi ggml-{model_name}.bin in {WHISPER_CPP_MODELS_DIR} "
            "o imposta WHISPER_CPP_MODEL."
        )

    job = jobs[job_id]
    work_dir = _job_dir(job_id)
    output_prefix = work_dir / "whisper_cpp_result"
    stdout_path = work_dir / "whisper_cpp_stdout.log"
    stderr_path = work_dir / "whisper_cpp_stderr.log"
    json_path = output_prefix.with_suffix(".json")
    json_path.unlink(missing_ok=True)

    progress("transcribing", 8, "Preparazione audio per whisper.cpp…")
    wav_path = _prepare_whisper_cpp_audio(job_id, audio_path)
    threads = max(1, min((os.cpu_count() or 4), 8))
    cmd = [
        str(binary),
        "-m",
        str(model_path),
        "-f",
        wav_path,
        "-ojf",
        "-of",
        str(output_prefix),
        "-t",
        str(threads),
        "-bs",
        str(profile["beam_size"]),
        "-bo", str(profile["best_of"]),
        "-pp", "-l", language or "auto",
    ]
    use_dtw = profile.get("word_timestamps", False)
    if use_dtw:
        preset = model_name.replace("-", ".")
        cmd += ["-dtw", preset, "-nfa"]
    if job.get("glossary"):
        cmd += ["--prompt", job["glossary"]]

    progress("transcribing", 20, f"Trascrizione whisper.cpp in corso ({profile['label']})…")
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=(os.name == "posix"),
        )
        job_processes[job_id] = proc
        paused_process = False
        started = time.monotonic()
        last_progress_pct = 20
        try:
            while proc.poll() is None:
                if proc.stderr:
                    ready, _, _ = select.select([proc.stderr], [], [], 0.5)
                    if ready:
                        line = proc.stderr.readline()
                        stderr.write(line)
                        stderr.flush()
                        cli_progress = _parse_whisper_cpp_progress(line)
                        if cli_progress is not None:
                            mapped_pct = min(80, 20 + int(cli_progress * 0.6))
                            if mapped_pct > last_progress_pct:
                                last_progress_pct = mapped_pct
                                progress(
                                    "transcribing",
                                    mapped_pct,
                                    f"Trascrizione whisper.cpp {cli_progress}% ({profile['label']})…",
                                )
                if job.get("cancel_requested"):
                    if paused_process and os.name == "posix":
                        _signal_process(proc, signal.SIGCONT)
                    _terminate_process(proc)
                    try: proc.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        _terminate_process(proc, force=True)
                        proc.wait(timeout=3)
                    raise RuntimeError("Trascrizione whisper.cpp annullata")
                if job.get("pause_requested"):
                    if not paused_process and os.name == "posix":
                        _signal_process(proc, signal.SIGSTOP)
                        paused_process = True
                    job["status"] = "paused"
                    job["stage"] = "paused"
                    job["previous_stage"] = "transcribing"
                    job["message"] = "Trascrizione whisper.cpp in pausa…"
                else:
                    if paused_process and os.name == "posix":
                        _signal_process(proc, signal.SIGCONT)
                        paused_process = False
                        if job.get("status") == "paused":
                            progress(
                                "transcribing",
                                job.get("progress", last_progress_pct),
                                "Trascrizione whisper.cpp in corso…",
                            )
            if proc.stderr:
                for line in proc.stderr:
                    stderr.write(line)
                    cli_progress = _parse_whisper_cpp_progress(line)
                    if cli_progress is not None:
                        last_progress_pct = max(last_progress_pct, min(80, 20 + int(cli_progress * 0.6)))
                stderr.flush()
            if proc.returncode != 0:
                detail = _tail_text(stderr_path) or _tail_text(stdout_path) or "whisper.cpp fallito"
                raise RuntimeError(detail.strip())
            if not json_path.exists():
                raise RuntimeError("whisper.cpp non ha prodotto output JSON.")

            data = json.loads(json_path.read_text())
            items = data.get("transcription") or data.get("segments") or []
            raw_segments: List[dict] = []
            for idx, seg in enumerate(items, 1):
                start = _whisper_cpp_segment_time(seg, "from")
                end = _whisper_cpp_segment_time(seg, "to")
                if start is None:
                    start = _whisper_cpp_segment_time(seg, "start") or 0.0
                if end is None:
                    end = _whisper_cpp_segment_time(seg, "end") or start
                text = (seg.get("text") or "").strip()
                if text:
                    raw_segments.append({
                        "id": idx,
                        "start": float(start),
                        "end": float(end),
                        "text": text,
                        "words": cpp_words(seg, use_dtw),
                        "speaker": None,
                    })

            result = data.get("result") or {}
            duration = _audio_duration(wav_path)
            info = {
                "language": result.get("language") or language,
                "duration": duration,
                "backend": "whisper_cpp",
                "binary": str(binary),
                "model_path": str(model_path),
                "threads": threads,
                "word_timestamps": True,
                "alignment": "dtw" if use_dtw else "token",
                "device": "metal" if platform.system() == "Darwin" else "auto",
                "elapsed_seconds": round(time.monotonic() - started, 2),
            }
            log_text = stderr_path.read_text(errors="replace")
            timing = {}
            for metric, pattern in [("model_load_seconds", r"load time\s*=\s*([\d.]+) ms"), ("asr_seconds", r"total time\s*=\s*([\d.]+) ms")]:
                match = re.search(pattern, log_text)
                if match: timing[metric] = float(match.group(1)) / 1000
            # CLI total includes alignment; do not fabricate a separate DTW timer.
            timing["alignment_seconds"] = None if use_dtw else 0
            timing["alignment_included_in_asr"] = use_dtw
            job.setdefault("metrics", {}).update(timing)
            info["device"] = "metal" if "using device Metal" in log_text or "ggml_metal" in log_text else ("cuda" if "CUDA" in log_text else "cpu")
            return raw_segments, info
        finally:
            job_processes.pop(job_id, None)


def _run_faster_whisper_worker(job_id, audio_path, model_name, language, profile, progress):
    job = jobs[job_id]
    audio = _prepare_diarization_audio(job_id, audio_path)
    duration = _audio_duration(audio_path)
    device, compute_type = detect_device()
    if device == 'auto': device = 'cpu'  # CTranslate2 has no Metal backend.
    words = bool(profile['word_timestamps'] or job.get('diarization_precision') == 'words')
    job['transcription_options'].update(device=device, compute_type=compute_type, word_timestamps=words)
    folder = _job_dir(job_id)
    request, output = folder/'asr_request.json', folder/'asr_result.json'
    progress_path = Path(str(output) + '.progress')
    output.unlink(missing_ok=True)
    progress_path.unlink(missing_ok=True)
    atomic_json(request, dict(audio=audio, model=model_name, language=language or None,
        glossary=job.get('glossary'), device=device, compute_type=compute_type, models_dir=str(MODELS_DIR),
        beam_size=profile['beam_size'], best_of=profile['best_of'], word_timestamps=words))
    progress('transcribing', 5, f'Caricamento modello {model_name}…')
    with (folder/'asr_worker.log').open('w') as log:
        proc = subprocess.Popen([sys.executable, '-m', 'backend.asr_worker', str(request), str(output)],
            cwd=str(BASE_DIR), stdout=log, stderr=log, start_new_session=(os.name == 'posix'))
        job_processes[job_id] = proc
        paused = False
        try:
            while proc.poll() is None:
                _check_canceled(job)
                if job.get('pause_requested'):
                    if not paused and os.name == 'posix':
                        _signal_process(proc, signal.SIGSTOP)
                        paused = True
                    job.update(status='paused', stage='paused', previous_stage='transcribing')
                else:
                    if paused and os.name == 'posix':
                        _signal_process(proc, signal.SIGCONT)
                        paused = False
                    if progress_path.exists():
                        end = json.loads(progress_path.read_text())['end']
                        progress('transcribing', min(80, 20+int(end/max(duration, 1)*60)), f'Trascritto {end:.1f}s / {duration:.1f}s')
                time.sleep(.2)
            _check_canceled(job)
            if not output.exists():
                raise RuntimeError('Worker ASR terminato senza risultato')
            result = json.loads(output.read_text())
            if proc.returncode or not result.get('ok'):
                raise RuntimeError(result.get('error') or 'Worker ASR fallito')
            job['metrics'].update(result['metrics'])
            return result['segments'], {'language': result['language'], 'duration': duration}
        finally:
            if proc.poll() is None:
                if paused and os.name == 'posix': _signal_process(proc, signal.SIGCONT)
                _terminate_process(proc)
                try: proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    _terminate_process(proc, force=True)
                    proc.wait(timeout=3)
            job_processes.pop(job_id, None)


# ── Background task: transcribe ───────────────────────────────────────────────

@heavy_job
def _run_transcription(
    job_id: str,
    audio_path: str,
    model_name: str,
    language: Optional[str],
    diarize: bool,
    hf_token: str,
    expected_speakers: Optional[int] = None,
    diarization_model: str = DEFAULT_DIARIZATION_MODEL,
    diarization_mode: str = DEFAULT_DIARIZATION_MODE,
    diarization_start: str = DEFAULT_DIARIZATION_START,
    diarization_device: str = DEFAULT_DIARIZATION_DEVICE,
    performance_profile: str = DEFAULT_PERFORMANCE_PROFILE,
    transcription_backend: str = DEFAULT_TRANSCRIPTION_BACKEND,
) -> None:
    job = jobs[job_id]

    def progress(stage: str, pct: int, msg: str = "") -> None:
        if not job.get("pause_requested"):
            job["status"] = "processing"
        job["stage"] = stage
        job["progress"] = pct
        job["message"] = msg

    try:
        job["pausable"] = True
        job["cancelable"] = True
        profile_key, profile = _resolve_performance_profile(performance_profile)
        backend = _resolve_transcription_backend(transcription_backend)
        diarization_start = _resolve_diarization_start(diarization_start, diarize)
        diarization_device = _resolve_diarization_device(diarization_device)
        job["performance_profile"] = profile_key
        job["expected_speakers"] = expected_speakers
        job["diarization_model"] = diarization_model
        job["diarization_mode"] = diarization_mode
        job["transcription_backend"] = backend
        job["diarization_start"] = diarization_start
        job["diarization_device"] = diarization_device
        job["transcription_options"] = {
            "backend": backend,
            "beam_size": profile["beam_size"],
            "best_of": profile["best_of"],
            "word_timestamps": profile["word_timestamps"],
        }
        metrics = job.setdefault("metrics", {})
        transcribe_started = time.monotonic()
        if backend == "whisper_cpp":
            raw_segments, info = _run_whisper_cpp(
                job_id, audio_path, model_name, language, profile, progress
            )
            job["transcription_options"].update({
                "binary": info.get("binary"),
                "model_path": info.get("model_path"),
                "threads": info.get("threads"),
                "word_timestamps": info.get("word_timestamps", False),
                "alignment": info.get("alignment"),
                "device": info.get("device"),
            })
        else:
            raw_segments, info = _run_faster_whisper_worker(job_id, audio_path, model_name, language, profile, progress)
        progress("transcribing", 82, "Trascrizione completata.")
        _wait_if_paused(job)
        metrics["transcription_seconds"] = round(time.monotonic() - transcribe_started, 2)

        # Save raw transcript before diarization
        job["language"] = info.get("language") if isinstance(info, dict) else info.language
        job["duration"] = info.get("duration") if isinstance(info, dict) else info.duration
        job["model"] = model_name
        d = _job_dir(job_id)
        d.mkdir(exist_ok=True)
        if not (d / "transcript.json").exists():
            atomic_json(d / "transcript.json", raw_segments)
        job["raw_segments"] = raw_segments

        final_segments = raw_segments

        if diarize and hf_token and diarization_start == "auto":
            job["pausable"] = True
            job["cancelable"] = True
            progress("diarizing", 83, "Preparazione diarizzazione…")
            try:
                _check_canceled(job)
                diarization_audio = str(_job_dir(job_id) / "normalized.wav") if (_job_dir(job_id) / "normalized.wav").exists() else _prepare_diarization_audio(job_id, audio_path)
                _check_canceled(job)
                progress("diarizing", 84, "Diarizzazione in corso…")
                diarization_started = time.monotonic()
                final_segments = _run_diarization_worker(
                    job_id,
                    diarization_audio,
                    raw_segments,
                    hf_token,
                expected_speakers,
                diarization_model,
                diarization_mode,
                diarization_device,
            )
                metrics["diarization_seconds"] = round(time.monotonic() - diarization_started, 2)
                _check_canceled(job)
                job["diarization_ran"] = True
                job["diarization_error"] = None
                progress("diarizing", 96, "Diarizzazione completata.")
            except Exception as e:
                if job.get("cancel_requested"):
                    raise
                job["diarization_error"] = str(e)
                progress("diarizing", 96, f"Diarizzazione fallita: {e}")
                for seg in final_segments:
                    seg["speaker"] = None
        else:
            for seg in final_segments:
                seg["speaker"] = "Speaker 1"

        progress("saving", 98, "Salvataggio revisione…")
        job["pausable"] = False
        job["cancelable"] = False
        job["segments"] = final_segments
        job["metrics"]["total_seconds"] = round(time.monotonic() - transcribe_started, 3)
        save_job_to_disk(job_id)
        progress("done", 100, "Completato.")
        job["status"] = "done"

    except Exception as exc:
        (_job_dir(job_id) / "transcription_error.log").write_text(
            traceback.format_exc(), encoding="utf-8"
        )
        if job.get("cancel_requested"):
            job["status"] = "canceled"
            job["stage"] = "canceled"
            job["progress"] = 100
            job["message"] = "Operazione annullata."
        else:
            job["status"] = "error"
            job["stage"] = "error"
            job["message"] = str(exc)
        job["error"] = str(exc)
        job["pausable"] = False
        job["cancelable"] = False


# ── Background task: re-diarize existing job ─────────────────────────────────

@heavy_job
def _run_rediarization(
    job_id: str,
    hf_token: str,
    expected_speakers: Optional[int] = None,
    diarization_model: str = DEFAULT_DIARIZATION_MODEL,
    diarization_mode: str = DEFAULT_DIARIZATION_MODE,
    diarization_device: str = DEFAULT_DIARIZATION_DEVICE,
) -> None:
    job = jobs[job_id]
    before = copy.deepcopy(job)

    def progress(stage: str, pct: int, msg: str = "") -> None:
        job["stage"] = stage
        job["progress"] = pct
        job["message"] = msg
        job["status"] = "processing"

    try:
        job["pausable"] = True
        job["cancelable"] = True
        job["diarization_device"] = _resolve_diarization_device(diarization_device)
        audio_path = _find_audio_file(job_id)
        if not audio_path:
            raise RuntimeError("File audio non trovato. Impossibile ri-diarizzare.")

        # Load raw transcript (no speakers)
        transcript_file = _job_dir(job_id) / "transcript.json"
        if job.get("raw_segments"):
            raw_segments = copy.deepcopy(job["raw_segments"])
        elif transcript_file.exists():
            raw_segments = json.loads(transcript_file.read_text())
        else:
            # Fall back to current segments, stripped of speakers
            raw_segments = [{**s, "speaker": None} for s in job.get("segments", [])]

        # Speaker-only reprocessing must not undo manually corrected text.
        current_segments = job.get('segments') or []
        normalize_text = lambda items: ' '.join(' '.join(s.get('text', '') for s in items).split())
        if current_segments and normalize_text(current_segments) != normalize_text(raw_segments):
            raw_segments = copy.deepcopy(current_segments)
            for segment in raw_segments:
                segment['speaker'] = None
                for word in segment.get('words', []): word['speaker'] = None

        progress("diarizing", 10, "Preparazione diarizzazione…")
        _check_canceled(job)
        diarization_audio = _prepare_diarization_audio(job_id, str(audio_path))
        _check_canceled(job)
        progress("diarizing", 10, "Diarizzazione in corso…")
        diarization_started = time.monotonic()
        final_segments = _run_diarization_worker(
            job_id,
            diarization_audio,
            raw_segments,
            hf_token,
            expected_speakers,
            diarization_model,
            diarization_mode,
            job["diarization_device"],
        )
        job.setdefault("metrics", {})["diarization_seconds"] = round(time.monotonic() - diarization_started, 3)
        _check_canceled(job)

        job["diarization_ran"] = True
        job["diarization_error"] = None
        job["segments"] = final_segments
        job["status"] = "processing"
        job["pausable"] = False
        job["cancelable"] = False
        job["stage"] = "done"
        job["progress"] = 100
        job["message"] = "Diarizzazione completata."
        save_job_to_disk(job_id)
        job["status"] = "done"

    except Exception as exc:
        canceled = job.get("cancel_requested")
        job.update(before)
        job["cancel_requested"] = canceled
        if job.get("cancel_requested"):
            job["status"] = "done"
            job["stage"] = "done"
            job["progress"] = 100
            job["message"] = "Diarizzazione annullata. Trascrizione esistente preservata."
            job["error"] = None
            job["diarization_error"] = None if (_job_dir(job_id) / "diarized.json").exists() else "Diarizzazione annullata"
        else:
            job["status"] = "done"  # the existing transcript is still usable
            job["stage"] = "error"
            job["progress"] = 100
            job["error"] = str(exc)
            job["diarization_error"] = str(exc)
            job["message"] = str(exc)
        job["pausable"] = False
        job["cancelable"] = False



# ─────────────────────────────────────────────────────────────────────────────
# API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "platform": platform.machine()}


# ── Config ────────────────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    cfg = load_config()
    token = cfg.get("hf_token", "")
    return {**cfg, "hf_token_set": bool(token), "hf_token": ""}


@app.post("/api/config")
def post_config(body: dict):
    cfg = load_config()
    token_present = "hf_token" in body
    incoming_token = (body.get("hf_token") or "").strip() if token_present else None

    cfg["diarization_enabled"] = bool(
        body.get("diarization_enabled", cfg.get("diarization_enabled", True))
    )
    if body.get("diarization_model") in DIARIZATION_MODELS:
        cfg["diarization_model"] = body["diarization_model"]
    if body.get("diarization_mode") in ("balanced", "conservative"):
        cfg["diarization_mode"] = body["diarization_mode"]
    if body.get("diarization_start") in DIARIZATION_START_MODES:
        cfg["diarization_start"] = body["diarization_start"]
        cfg["diarization_enabled"] = body["diarization_start"] != "off"
    if body.get("diarization_device") in DIARIZATION_DEVICES:
        cfg["diarization_device"] = body["diarization_device"]
    if body.get("performance_profile") in PERFORMANCE_PROFILES:
        cfg["performance_profile"] = body["performance_profile"]
    if body.get("transcription_backend") in TRANSCRIPTION_BACKENDS:
        cfg["transcription_backend"] = body["transcription_backend"]
    if body.get("default_model") in WHISPER_MODELS:
        cfg["default_model"] = body["default_model"]
    if incoming_token:
        cfg["hf_token"] = incoming_token
    elif token_present and not cfg.get("hf_token"):
        cfg["hf_token"] = ""

    for key in ('glossary', 'diarization_precision'):
        if key in body: cfg[key] = body[key]
    _validate_quality_options(cfg.get('diarization_precision', 'segments'), cfg.get('glossary', ''))
    save_config(cfg)
    return {"ok": True}


# ── Models ────────────────────────────────────────────────────────────────────

@app.get("/api/models")
def list_models(backend: str = "faster_whisper"):
    return [
        {"name": n, "downloaded": bool(_whisper_cpp_model_path(n)) if backend == "whisper_cpp" else is_model_downloaded(n), "size_mb": APPROX_SIZES_MB.get(n, 0)}
        for n in WHISPER_MODELS
    ]


@app.get("/api/models/{model_name}/download")
async def download_model_sse(model_name: str, backend: str = "faster_whisper"):
    _validate_model_name(model_name)

    async def generator():
        def send(data: dict) -> str:
            return f"data: {json.dumps(data)}\n\n"

        yield send({"status": "starting", "progress": 0})

        if backend == 'whisper_cpp':
            import httpx
            target = WHISPER_CPP_MODELS_DIR / f'ggml-{model_name}.bin'
            temp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.part')
            try:
                if not _whisper_cpp_model_path(model_name):
                    url = f'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{model_name}.bin'
                    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
                        async with client.stream('GET', url) as response:
                            response.raise_for_status()
                            total = int(response.headers.get('content-length') or 0)
                            downloaded = 0
                            async with aiofiles.open(temp, 'wb') as output:
                                async for chunk in response.aiter_bytes(1024*1024):
                                    await output.write(chunk)
                                    downloaded += len(chunk)
                                    yield send({'status': 'downloading', 'progress': min(99, int(downloaded/total*100)) if total else 0})
                            if not downloaded or (total and downloaded != total):
                                raise RuntimeError('Download incompleto')
                    os.replace(temp, target)
                yield send({'status': 'done', 'progress': 100})
            except Exception as exc:
                yield send({'status': 'error', 'error': str(exc)})
            finally:
                temp.unlink(missing_ok=True)
            return

        loop = asyncio.get_event_loop()
        progress_state = {"pct": 0, "msg": ""}
        hf_token_for_dl = load_config().get("hf_token") or None

        def do_download():
            try:
                from huggingface_hub import snapshot_download
                snapshot_download(
                    repo_id=_model_hf_id(model_name),
                    cache_dir=str(MODELS_DIR),
                    token=hf_token_for_dl,
                    ignore_patterns=["*.msgpack", "*.h5", "flax_model*", "tf_model*"],
                )
                progress_state["pct"] = 100
                progress_state["msg"] = "ok"
            except Exception as e:
                progress_state["pct"] = -1
                progress_state["msg"] = str(e)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = loop.run_in_executor(executor, do_download)

        model_id = _model_hf_id(model_name)
        cache_key = "models--" + model_id.replace("/", "--")
        model_dir = MODELS_DIR / cache_key
        expected_bytes = APPROX_SIZES_MB.get(model_name, 500) * 1024 * 1024

        while not future.done():
            downloaded = sum(
                f.stat().st_size for f in model_dir.rglob("*") if f.is_file()
            ) if model_dir.exists() else 0
            pct = min(95, int(downloaded / expected_bytes * 100)) if expected_bytes else 0
            yield send({"status": "downloading", "progress": pct})
            await asyncio.sleep(1)

        await future
        executor.shutdown(wait=False)
        if progress_state["pct"] == 100:
            yield send({"status": "done", "progress": 100})
        else:
            yield send({"status": "error", "progress": 0, "error": progress_state["msg"]})

    return StreamingResponse(generator(), media_type="text/event-stream")


# ── YouTube ───────────────────────────────────────────────────────────────────

@app.post("/api/youtube/info")
async def youtube_info(body: dict):
    url = body.get("url", "")
    if not url:
        raise HTTPException(400, "URL mancante")
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": info.get("title"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "uploader": info.get("uploader"),
            }
    except Exception as e:
        raise HTTPException(400, str(e))


# ── Transcription job ─────────────────────────────────────────────────────────

@app.post("/api/transcribe")
async def create_transcription_job(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    youtube_url: Optional[str] = Form(None),
    model_name: str = Form("small"),
    language: Optional[str] = Form(None),
    diarize: bool = Form(True),
    expected_speakers: Optional[int] = Form(None),
    diarization_mode: Optional[str] = Form(None),
    diarization_start: Optional[str] = Form(None),
    diarization_device: Optional[str] = Form(None),
    performance_profile: Optional[str] = Form(None),
    transcription_backend: Optional[str] = Form(None),
    glossary: Optional[str] = Form(None),
    diarization_precision: Optional[str] = Form(None),
    min_speakers: Optional[int] = Form(None),
    max_speakers: Optional[int] = Form(None),
):
    _validate_model_name(model_name)
    expected_speakers = _normalize_expected_speakers(expected_speakers)

    cfg = load_config()
    glossary = glossary if glossary is not None else cfg.get("glossary", "")
    diarization_precision = diarization_precision or cfg.get("diarization_precision", "segments")
    _validate_quality_options(diarization_precision, glossary, expected_speakers, min_speakers, max_speakers)
    hf_token = cfg.get("hf_token", "")
    diarization_enabled = bool(cfg.get("diarization_enabled", True))
    diarization_model = cfg.get("diarization_model", DEFAULT_DIARIZATION_MODEL)
    diarization_mode = diarization_mode or cfg.get("diarization_mode", DEFAULT_DIARIZATION_MODE)
    diarization_start = _resolve_diarization_start(
        diarization_start or cfg.get("diarization_start", DEFAULT_DIARIZATION_START),
        diarization_enabled,
    )
    diarization_device = _resolve_diarization_device(
        diarization_device or cfg.get("diarization_device", DEFAULT_DIARIZATION_DEVICE)
    )
    if diarization_mode not in ("balanced", "conservative"):
        raise HTTPException(400, "Modalità diarizzazione non valida")
    performance_profile, _ = _resolve_performance_profile(
        performance_profile or cfg.get("performance_profile", DEFAULT_PERFORMANCE_PROFILE)
    )
    transcription_backend = _resolve_transcription_backend(
        transcription_backend or cfg.get("transcription_backend", DEFAULT_TRANSCRIPTION_BACKEND)
    )
    diarize = bool(diarize and diarization_enabled and diarization_start != "off")
    if diarize and not hf_token:
        diarize = False

    job_id = str(uuid.uuid4())
    job_dir = _job_dir(job_id)
    job_dir.mkdir(exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()

    jobs[job_id] = {
        "glossary": glossary,
        "diarization_precision": diarization_precision,
        "min_speakers": min_speakers,
        "max_speakers": max_speakers,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "In attesa…",
        "segments": [],
        "language": None,
        "duration": None,
        "error": None,
        "audio_url": None,
        "filename": "",
        "title": "",
        "model": model_name,
        "transcription_backend": transcription_backend,
        "created_at": now,
        "youtube_url": youtube_url or "",
        "diarization_ran": False,
        "diarization_error": None,
        "expected_speakers": expected_speakers,
        "diarization_model": diarization_model,
        "diarization_mode": diarization_mode,
        "diarization_start": diarization_start,
        "diarization_device": diarization_device,
        "performance_profile": performance_profile,
        "metrics": {},
    }
    _set_job_control_defaults(jobs[job_id])

    audio_path: Optional[str] = None

    if file:
        original_name = file.filename or "audio"
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_AUDIO_SUFFIXES:
            jobs.pop(job_id, None)
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(400, "Formato file non supportato")
        dest = job_dir / f"audio{suffix}"
        written = 0
        async with aiofiles.open(dest, "wb") as f:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    await f.close()
                    dest.unlink(missing_ok=True)
                    jobs.pop(job_id, None)
                    shutil.rmtree(job_dir, ignore_errors=True)
                    raise HTTPException(413, "File troppo grande")
                await f.write(chunk)
        audio_path = str(dest)
        jobs[job_id]["audio_url"] = f"/api/audio/{job_id}"
        jobs[job_id]["filename"] = original_name
        jobs[job_id]["title"] = Path(original_name).stem

    elif youtube_url:
        jobs[job_id]["status"] = "downloading_yt"
        jobs[job_id]["stage"] = "downloading_yt"
        jobs[job_id]["message"] = "Download YouTube…"

        def download_yt_then_transcribe():
            try:
                import yt_dlp
                out_template = str(job_dir / "audio.%(ext)s")
                ydl_opts = {
                    "format": "bestaudio/best",
                    "outtmpl": out_template,
                    "logger": _SilentYtDlpLogger(),
                    "noprogress": True,
                    "no_warnings": True,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                    "quiet": True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(youtube_url)
                    title = info.get("title", "YouTube video")
                    jobs[job_id]["filename"] = title
                    jobs[job_id]["title"] = title

                path = _find_audio_file(job_id)
                if not path:
                    raise RuntimeError("Nessun file audio trovato dopo il download")

                jobs[job_id]["audio_url"] = f"/api/audio/{job_id}"
                jobs[job_id]["status"] = "queued"
                _run_transcription(
                    job_id,
                    str(path),
                    model_name,
                    language,
                    diarize,
                    hf_token,
                    expected_speakers,
                    diarization_model,
                    diarization_mode,
                    diarization_start,
                    diarization_device,
                    performance_profile,
                    transcription_backend,
                )
            except Exception as exc:
                (job_dir / "youtube_error.log").write_text(
                    traceback.format_exc(), encoding="utf-8"
                )
                jobs[job_id]["status"] = "error"
                jobs[job_id]["stage"] = "error"
                jobs[job_id]["error"] = str(exc)
                jobs[job_id]["message"] = str(exc)

        background_tasks.add_task(download_yt_then_transcribe)
        return {"job_id": job_id}
    else:
        raise HTTPException(400, "Fornire un file o un URL YouTube")

    jobs[job_id]["status"] = "queued"
    background_tasks.add_task(
        _run_transcription,
        job_id,
        audio_path,
        model_name,
        language,
        diarize,
        hf_token,
        expected_speakers,
        diarization_model,
        diarization_mode,
        diarization_start,
        diarization_device,
        performance_profile,
        transcription_backend,
    )
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        if _load_job_from_disk(job_id):
            return jobs[job_id]
        raise HTTPException(404, "Job non trovato")
    if not _is_job_active(jobs[job_id]):
        try:
            _load_job_from_disk(job_id)
        except Exception as exc:
            print(f"[warn] Errore refresh job {job_id}: {exc}")
    return jobs[job_id]


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404)

    async def generator():
        last_progress, last_status = -1, ""
        while True:
            job = jobs.get(job_id, {})
            status = job.get("status", "")
            progress = job.get("progress", 0)
            if status != last_status or progress != last_progress:
                yield f"data: {json.dumps({**job, 'id': job_id})}\n\n"
                last_progress = progress
                last_status = status
            if status in ("done", "error", "canceled"):
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404)
    job = jobs[job_id]
    if job.get("status") not in ("queued", "processing"):
        raise HTTPException(400, "Job non in esecuzione")
    if not job.get("pausable"):
        raise HTTPException(
            409,
            "Questa fase non può essere messa in pausa.",
        )
    job["previous_stage"] = job.get("stage", "processing")
    job["pause_requested"] = True
    job["status"] = "paused"
    job["stage"] = "paused"
    job["message"] = "In pausa…"
    return {"ok": True}


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404)
    job = jobs[job_id]
    if not job.get("pause_requested") and job.get("status") != "paused":
        raise HTTPException(400, "Job non in pausa")
    job["pause_requested"] = False
    job["status"] = "processing"
    job["stage"] = job.get("previous_stage", "processing")
    job["message"] = "Ripresa…"
    return {"ok": True}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404)
    job = jobs[job_id]
    if job.get("status") not in ("queued", "processing", "paused"):
        raise HTTPException(400, "Job non annullabile")
    if not job.get("cancelable"):
        raise HTTPException(
            409,
            "Questa fase non può essere interrotta in modo sicuro. Attendi la fine o riavvia il backend.",
        )
    job["cancel_requested"] = True
    job["pause_requested"] = False
    job["cancelable"] = False
    job["message"] = "Annullamento richiesto…"
    return {"ok": True}


# ── Re-diarize ────────────────────────────────────────────────────────────────

@app.post("/api/jobs/{job_id}/diarize")
def rediarize_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    body: Optional[dict] = Body(None),
):
    if job_id not in jobs:
        raise HTTPException(404)
    if _is_job_active(jobs[job_id]):
        raise HTTPException(409, "Questo job è già in elaborazione")
    with LOCK:
        _check_edit_version(job_id, (body or {}).get('version'), required=False)
    expected_speakers = _normalize_expected_speakers((body or {}).get("expected_speakers"))
    precision = (body or {}).get('diarization_precision') or jobs[job_id].get('diarization_precision') or 'segments'
    minimum, maximum = (body or {}).get('min_speakers'), (body or {}).get('max_speakers')
    _validate_quality_options(precision, '', expected_speakers, minimum, maximum)
    jobs[job_id].update(diarization_precision=precision, min_speakers=minimum, max_speakers=maximum)
    cfg = load_config()
    hf_token = cfg.get("hf_token", "")
    diarization_model = (body or {}).get("diarization_model") or cfg.get("diarization_model", DEFAULT_DIARIZATION_MODEL)
    diarization_mode = (body or {}).get("diarization_mode") or cfg.get("diarization_mode", DEFAULT_DIARIZATION_MODE)
    diarization_device = _resolve_diarization_device(
        (body or {}).get("diarization_device") or cfg.get("diarization_device", DEFAULT_DIARIZATION_DEVICE)
    )
    if diarization_model not in DIARIZATION_MODELS:
        raise HTTPException(400, "Modello diarizzazione non valido")
    if diarization_mode not in ("balanced", "conservative"):
        raise HTTPException(400, "Modalità diarizzazione non valida")
    if not hf_token:
        raise HTTPException(400, "Token HuggingFace non configurato")

    audio_path = _find_audio_file(job_id)
    if not audio_path:
        raise HTTPException(400, "File audio non presente — impossibile ri-diarizzare senza audio")

    jobs[job_id]["status"] = "processing"
    jobs[job_id]["stage"] = "diarizing"
    jobs[job_id]["progress"] = 0
    jobs[job_id]["message"] = "Avvio diarizzazione…"
    jobs[job_id]["error"] = None
    jobs[job_id]["diarization_error"] = None
    jobs[job_id]["expected_speakers"] = expected_speakers
    jobs[job_id]["diarization_model"] = diarization_model
    jobs[job_id]["diarization_mode"] = diarization_mode
    jobs[job_id]["diarization_device"] = diarization_device
    jobs[job_id]["pause_requested"] = False
    jobs[job_id]["cancel_requested"] = False
    jobs[job_id]["pausable"] = True
    jobs[job_id]["cancelable"] = True

    background_tasks.add_task(
        _run_rediarization,
        job_id,
        hf_token,
        expected_speakers,
        diarization_model,
        diarization_mode,
        diarization_device,
    )
    return {"job_id": job_id}


# ── Rename job ────────────────────────────────────────────────────────────────

@app.patch("/api/jobs/{job_id}")
def rename_job(job_id: str, body: dict):
    if job_id not in jobs:
        raise HTTPException(404)
    new_title = (body.get("title") or "").strip()
    if not new_title:
        raise HTTPException(400, "Titolo vuoto")
    jobs[job_id]["title"] = new_title
    save_job_to_disk(job_id)
    return {"ok": True}


# ── Update speaker names ──────────────────────────────────────────────────────

@app.post("/api/jobs/{job_id}/speakers")
def update_speakers(job_id: str, body: dict):
    with LOCK:
        _check_edit_version(job_id, body.get('version'), required=False)
        job = jobs[job_id]
        previous = copy.deepcopy(job)
        mapping = body.get('mapping', {})
        if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in mapping.items()):
            raise HTTPException(400, 'Nomi speaker non validi')
        try:
            job['segments'] = copy.deepcopy(job['segments'])
            for seg in job['segments']:
                seg['speaker'] = mapping.get(seg.get('speaker'), seg.get('speaker'))
                seg['speaker_candidates'] = list(dict.fromkeys(mapping.get(sp, sp) for sp in seg.get('speaker_candidates', [])))
                for word in seg.get('words', []):
                    word['speaker'] = mapping.get(word.get('speaker'), word.get('speaker'))
                    word['speaker_candidates'] = list(dict.fromkeys(mapping.get(sp, sp) for sp in word.get('speaker_candidates', [])))
            job['_revision_reason'] = 'speaker_names'
            save_job_to_disk(job_id)
        except Exception:
            job.update(previous)
            raise
        return {'ok': True, 'version': job.get('version')}


@app.get("/api/jobs/{job_id}/speakers/suggestions")
def speaker_merge_suggestions(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404)
    segments = jobs[job_id].get("segments", [])
    stats = _speaker_stats(segments)
    suggestions = []
    total_duration = sum(item["duration"] for item in stats.values()) or 1.0
    total_segments = sum(item["segments"] for item in stats.values()) or 1

    for speaker, item in stats.items():
        if len(stats) <= 1:
            break
        duration_share = item["duration"] / total_duration
        segment_share = item["segments"] / total_segments
        neighbor_scores = _speaker_neighbor_scores(segments, speaker)
        if not neighbor_scores:
            continue
        target = max(
            neighbor_scores,
            key=lambda sp: (neighbor_scores[sp], stats.get(sp, {}).get("duration", 0.0)),
        )
        confidence = min(0.95, 0.35 + neighbor_scores[target] * 0.12)
        if item["segments"] <= 4 or item["duration"] <= 25 or duration_share <= 0.06 or segment_share <= 0.06:
            suggestions.append({
                "source": speaker,
                "target": target,
                "source_segments": item["segments"],
                "source_duration": round(item["duration"], 1),
                "target_segments": stats.get(target, {}).get("segments", 0),
                "target_duration": round(stats.get(target, {}).get("duration", 0.0), 1),
                "confidence": round(confidence, 2),
            })

    suggestions.sort(key=lambda s: (s["source_duration"], -s["confidence"]))
    return {"speakers": list(stats.values()), "suggestions": suggestions[:6]}


# ── Delete job / audio ────────────────────────────────────────────────────────

@app.delete("/api/jobs/{job_id}/audio")
def delete_audio(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404)
    if _is_job_active(jobs[job_id]):
        raise HTTPException(409, "Job in elaborazione: annulla o attendi la fine prima di eliminare l'audio")
    audio = _find_audio_file(job_id)
    if audio and audio.exists():
        audio.unlink()
    jobs[job_id]["audio_url"] = None
    # Update meta on disk
    meta_file = _job_dir(job_id) / "meta.json"
    if meta_file.exists():
        meta = json.loads(meta_file.read_text())
        meta["has_audio"] = False
        meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404)
    if _is_job_active(jobs[job_id]):
        raise HTTPException(409, "Job in elaborazione: annulla o attendi la fine prima di eliminarlo")
    d = _job_dir(job_id)
    if d.exists():
        shutil.rmtree(d)
    jobs.pop(job_id, None)
    return {"ok": True}


# ── History ───────────────────────────────────────────────────────────────────

@app.get("/api/history")
def get_history():
    result = []
    for job_id in list(jobs.keys()):
        if job_id.startswith("_work"):
            continue
        job = jobs[job_id]
        if not _is_job_active(job):
            try:
                _load_job_from_disk(job_id)
                job = jobs[job_id]
            except Exception as exc:
                print(f"[warn] Errore refresh history job {job_id}: {exc}")
        if job.get("status") not in ("done", "processing", "paused", "queued", "downloading_yt"):
            continue
        d = _job_dir(job_id)
        result.append({
            "id": job_id,
            "status": job.get("status", ""),
            "stage": job.get("stage", ""),
            "progress": job.get("progress", 0),
            "message": job.get("message", ""),
            "pausable": job.get("pausable", False),
            "cancelable": job.get("cancelable", False),
            "title": job.get("title") or job.get("filename", job_id),
            "filename": job.get("filename", ""),
            "created_at": job.get("created_at", ""),
            "model": job.get("model", ""),
            "transcription_backend": job.get("transcription_backend", DEFAULT_TRANSCRIPTION_BACKEND),
            "language": job.get("language", ""),
            "duration": job.get("duration"),
            "has_audio": job.get("audio_url") is not None,
            "has_transcript": (d / "transcript.json").exists(),
            "has_diarization": (d / "diarized.json").exists(),
            "segment_count": len(job.get("segments", [])),
            "youtube_url": job.get("youtube_url", ""),
            "diarization_model": job.get("diarization_model", DEFAULT_DIARIZATION_MODEL),
            "diarization_mode": job.get("diarization_mode", DEFAULT_DIARIZATION_MODE),
        })
    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return result


# ── Audio serving ─────────────────────────────────────────────────────────────

@app.get("/api/audio/{job_id}")
def serve_audio(job_id: str):
    audio = _find_audio_file(job_id)
    if not audio:
        raise HTTPException(404)
    return FileResponse(str(audio))


@app.get("/api/jobs/{job_id}/export/{fmt}")
def export_transcript(job_id: str, fmt: str, variant: str = "speakers"):
    if job_id not in jobs:
        raise HTTPException(404)
    variant = variant.lower()
    if variant not in ("raw", "speakers"):
        raise HTTPException(400, "Variante export non supportata")

    segments = jobs[job_id].get("segments", [])
    if variant == "raw":
        transcript_file = _job_dir(job_id) / "transcript.json"
        if transcript_file.exists():
            segments = json.loads(transcript_file.read_text())
        else:
            segments = [{**s, "speaker": None} for s in segments]

    if not segments:
        raise HTTPException(400, "Nessun segmento disponibile")

    fmt = fmt.lower()
    try:
        content, media_type = render_export(segments, fmt, variant)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    filename = jobs[job_id].get("title") or jobs[job_id].get("filename", "transcript")
    safe_name = re.sub(r"[^\w\-]", "_", Path(filename).stem)
    suffix = "raw" if variant == "raw" else "speakers"

    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_{suffix}.{fmt}"'},
    )


def _check_edit_version(job_id, version, required=True):
    if job_id not in jobs:
        raise HTTPException(404, 'Job non trovato')
    if _is_job_active(jobs[job_id]):
        raise HTTPException(409, 'Elaborazione in corso')
    _load_job_from_disk(job_id)
    current = jobs[job_id].get('version') or legacy_version(_job_dir(job_id), jobs[job_id].get('segments', []))
    if (required and not version) or (version and version != current):
        raise HTTPException(409, 'Versione modificata: ricarica prima di applicare')
    return jobs[job_id]


from backend.review import register_review_routes
register_review_routes(app, sys.modules[__name__])
