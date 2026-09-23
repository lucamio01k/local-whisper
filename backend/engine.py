"""Isolated pyannote inference. Importing this module never loads application history."""
import inspect
import os
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
import platform
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from backend.quality import assign_speakers, PIPELINE_VERSION
from backend.storage import atomic_json, digest_file
import hashlib
import json
import importlib.metadata
DIARIZATION_MODELS = {"community-1": "pyannote/speaker-diarization-community-1", "3.1": "pyannote/speaker-diarization-3.1"}
DEFAULT_DIARIZATION_MODEL = "community-1"
DEFAULT_DIARIZATION_MODE = "conservative"
DEFAULT_DIARIZATION_DEVICE = "auto"
DIARIZATION_DEVICES = {"auto", "cpu", "mps"}
def _resolve_diarization_device(value: Optional[str]) -> str:
    return value if value in DIARIZATION_DEVICES else DEFAULT_DIARIZATION_DEVICE

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

def infer_turns(
    audio_path: str,
    segments: List[dict],
    hf_token: str,
    expected_speakers: Optional[int] = None,
    diarization_model: str = DEFAULT_DIARIZATION_MODEL,
    diarization_mode: str = DEFAULT_DIARIZATION_MODE,
    diarization_device: str = DEFAULT_DIARIZATION_DEVICE,
    min_speakers=None,
    max_speakers=None,
) -> dict:
    # pyannote checkpoints currently require the legacy torch.load behavior on
    # PyTorch >= 2.6. This is limited to the trusted pyannote models requested
    # explicitly by the user through their HuggingFace token.
    os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")

    from pyannote.audio import Pipeline
    import torch

    model_key = diarization_model if diarization_model in DIARIZATION_MODELS else DEFAULT_DIARIZATION_MODEL
    model_ids = [DIARIZATION_MODELS[model_key]]
    fallback_id = DIARIZATION_MODELS["3.1"]
    # Model selection is explicit; do not silently change it.

    started = time.monotonic()
    pipeline = None
    load_errors = []
    for model_id in model_ids:
        try:
            if "token" in inspect.signature(Pipeline.from_pretrained).parameters:
                pipeline = Pipeline.from_pretrained(model_id, token=hf_token)
            else:
                pipeline = Pipeline.from_pretrained(model_id, use_auth_token=hf_token)
            if pipeline is not None:
                break
        except Exception as exc:
            load_errors.append(f"{model_id}: {exc}")

    if pipeline is None:
        detail = " | ".join(load_errors) or "pipeline non disponibile"
        requested = DIARIZATION_MODELS.get(model_key, model_key)
        raise RuntimeError(
            f"Impossibile caricare {requested}. "
            "Verifica token HuggingFace e accettazione dei termini dei modelli pyannote. "
            f"Dettaglio: {detail}"
        )

    device_key = _resolve_diarization_device(diarization_device)
    try:
        if device_key == "cpu":
            pipeline = pipeline.to(torch.device("cpu"))
        elif device_key == "mps":
            mps = getattr(torch.backends, "mps", None)
            if not (mps and mps.is_available()):
                raise RuntimeError("MPS non disponibile per pyannote")
            pipeline = pipeline.to(torch.device("mps"))
        else:
            _dev, _ = detect_device()
            if _dev in ("cuda", "auto"):
                mps = getattr(torch.backends, "mps", None)
                if mps and mps.is_available():
                    pipeline = pipeline.to(torch.device("mps"))
                elif torch.cuda.is_available():
                    pipeline = pipeline.to(torch.device("cuda"))
    except Exception as exc:
        if device_key != "auto":
            raise RuntimeError(f"Device diarizzazione {device_key} non utilizzabile: {exc}") from exc

    load_seconds = time.monotonic() - started
    pipeline_kwargs = {}
    if expected_speakers:
        pipeline_kwargs["num_speakers"] = expected_speakers
    else:
        if min_speakers: pipeline_kwargs["min_speakers"] = min_speakers
        if max_speakers: pipeline_kwargs["max_speakers"] = max_speakers

    # Passing preloaded audio bypasses pyannote's TorchCodec decoder. TorchCodec
    # currently cannot load against some newer Homebrew FFmpeg releases.
    diarization_audio = _load_diarization_waveform(audio_path)
    diarization = pipeline(diarization_audio, **pipeline_kwargs)
    def tracks(annotation):
        return [(float(t.start), float(t.end), sp) for t, _, sp in annotation.itertracks(yield_label=True)]
    turns = tracks(getattr(diarization, "speaker_diarization", diarization))
    raw_speakers = sorted({speaker for _, _, speaker in turns})
    print(
        "Diarization turns:",
        len(turns),
        "speakers:",
        raw_speakers,
        "expected:",
        expected_speakers,
        "mode:",
        diarization_mode,
        flush=True,
    )

    exclusive = getattr(diarization, "exclusive_speaker_diarization", None)
    return {"standard": turns, "exclusive": tracks(exclusive) if exclusive is not None else None,
            "model": model_id, "device": str(pipeline.device), "load_seconds": load_seconds,
            "inference_seconds": time.monotonic() - started - load_seconds}

def cache_signature(audio, model, device, expected=None, minimum=None, maximum=None):
    from huggingface_hub.constants import HF_HUB_CACHE
    cache = Path(HF_HUB_CACHE)
    revisions = {str(p.relative_to(cache)): p.read_text().strip()
                 for p in cache.glob('models--pyannote--*/refs/*') if p.is_file()}
    settings = {"audio": digest_file(audio), "model": model, "device": device,
                "expected": expected, "min": None if expected else minimum, "max": None if expected else maximum,
                "revisions": revisions, "pipeline": PIPELINE_VERSION,
                "torch": importlib.metadata.version('torch'), "pyannote": importlib.metadata.version('pyannote.audio')}
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest(), settings


def run(payload, token):
    audio = payload['audio_path']
    segments = json.loads(Path(payload['segments_path']).read_text())
    model, device = payload.get('diarization_model', 'community-1'), payload.get('diarization_device', 'auto')
    # Resolve actual device before cache lookup.
    import torch
    if device == 'auto':
        device = 'mps' if torch.backends.mps.is_available() else ('cuda' if torch.cuda.is_available() else 'cpu')
    expected, minimum, maximum = payload.get('expected_speakers'), payload.get('min_speakers'), payload.get('max_speakers')
    key, signature = cache_signature(audio, model, device, expected, minimum, maximum)
    cache_dir = Path(audio).parent / 'turns_cache'
    cache_path = cache_dir / (key + '.json')
    cached = cache_path.exists()
    if cached:
        turns = json.loads(cache_path.read_text())
    else:
        turns = infer_turns(audio, [], token, expected, model, payload.get('diarization_mode', 'conservative'), device, minimum, maximum)
        key, signature = cache_signature(audio, model, device, expected, minimum, maximum)
        turns['signature'] = signature
        atomic_json(cache_dir / (key + '.json'), turns)
    started = time.monotonic()
    result = assign_speakers(segments, turns['standard'], turns['exclusive'], payload.get('diarization_precision', 'segments'))
    return {"ok": True, "segments": result, "turns": turns, "cache_hit": cached,
            "assignment_seconds": time.monotonic() - started, "cache_key": key}
