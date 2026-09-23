"""Foreground transcription runner with no HTTP server lifecycle."""
import copy
import shutil
import uuid
from pathlib import Path


def run_file(request: dict, work_dir: Path, progress, context) -> dict:
    """Run one existing local input through the application's proven job pipeline.

    ``context`` is the application's job adapter. Its state is isolated in
    ``work_dir`` for this process, so no audio, transcript, token, or history is
    persisted in the normal uploads directory. This module has no FastAPI import.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    previous_upload_dir = context.UPLOAD_DIR
    context.UPLOAD_DIR = work_dir
    try:
        context.jobs[job_id] = {
            "status": "queued", "stage": "queued", "progress": 0, "message": "In attesa…",
            "segments": [], "raw_segments": [], "metrics": {}, "error": None,
            "filename": Path(request["audio_path"]).name, "title": Path(request["audio_path"]).stem,
            "glossary": request.get("glossary", ""),
            "diarization_precision": request.get("diarization_precision", "segments"),
            "min_speakers": None, "max_speakers": None,
        }
        context._set_job_control_defaults(context.jobs[job_id])
        context._run_transcription(
            job_id, request["audio_path"], request["model"], request.get("language"),
            request["diarize"], request.get("hf_token", ""), request.get("expected_speakers"),
            request["diarization_model"], request["diarization_mode"], request["diarization_start"],
            request["diarization_device"], request["performance_profile"], request["transcription_backend"],
        )
        job = context.jobs[job_id]
        if job.get("status") == "error":
            raise RuntimeError(job.get("error") or job.get("message") or "Trascrizione fallita")
        progress("done", 100, "Trascrizione completata.")
        return {
            "segments": copy.deepcopy(job["segments"]),
            "raw_segments": copy.deepcopy(job.get("raw_segments", [])),
            "language": job.get("language"), "duration": job.get("duration"),
            "model": job.get("model"), "backend": job.get("transcription_backend"),
            "metrics": copy.deepcopy(job.get("metrics", {})),
            "diarization_ran": bool(job.get("diarization_ran")),
            "diarization_error": job.get("diarization_error"),
        }
    finally:
        context.jobs.pop(job_id, None)
        context.UPLOAD_DIR = previous_upload_dir
        shutil.rmtree(work_dir / job_id, ignore_errors=True)
