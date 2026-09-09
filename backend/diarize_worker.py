import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run pyannote diarization in an isolated worker.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    request_path = Path(args.input)
    output_path = Path(args.output)

    try:
        payload = json.loads(request_path.read_text())
        hf_token = os.environ.get("HF_TOKEN", "")
        if not hf_token:
            raise RuntimeError("Token HuggingFace mancante nel worker")

        from backend.main import _apply_diarization

        segments_path = Path(payload["segments_path"])
        segments = json.loads(segments_path.read_text())
        diarized = _apply_diarization(
            payload["audio_path"],
            segments,
            hf_token,
            payload.get("expected_speakers"),
            payload.get("diarization_model", "community-1"),
            payload.get("diarization_mode", "conservative"),
            payload.get("diarization_device", "auto"),
        )
        output_path.write_text(json.dumps({"ok": True, "segments": diarized}, ensure_ascii=False))
        return 0
    except Exception as exc:
        output_path.write_text(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
