"""Local Whisper command-line interface. Run with: python -m backend.cli."""
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from backend.exporting import FORMATS, TEXT_SUFFIXES, render
from backend.runner import run_file


def _positive_speakers(value: str) -> int:
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("numero speaker non valido") from exc
    if not 1 <= count <= 20:
        raise argparse.ArgumentTypeError("numero speaker deve essere fra 1 e 20")
    return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local-whisper",
        description="Trascrizione audio e diarizzazione speaker, completamente locale.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Comando disponibile:
  transcribe INPUT [opzioni]    Trascrive un file audio o video locale.

Opzioni principali di transcribe:
  --output CARTELLA             Directory finale; deve non esistere.
  --format FORMATO              Ripetibile: txt, srt, vtt, md, csv, json.
  --language CODICE             Lingua, ad es. it; omesso = rilevamento automatico.
  --model MODELLO               tiny, base, small, medium, large-v2, large-v3, large-v3-turbo.
  --backend BACKEND             faster_whisper oppure whisper_cpp.
  --profile PROFILO             fast, balanced oppure quality.
  --diarize / --no-diarize      Forza o disabilita speaker diarization.
  --speakers NUMERO             Numero speaker attesi, da 1 a 20.

Esempi:
  local-whisper transcribe riunione.mp3
  local-whisper transcribe lezione.m4a --language it --speakers 4
  local-whisper transcribe audio.mp3 --format txt --format srt --format json

Output predefinito: ./local-whisper-output/<nome-file>/transcript.txt
Formati: txt, srt, vtt, md, csv, json
Le preferenze dell'app (modello, backend e diarizzazione) vengono usate per default.

Per tutte le opzioni: local-whisper transcribe --help""",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    transcribe = commands.add_parser("transcribe", help="Trascrive un file audio o video locale.")
    transcribe.add_argument("input", type=Path, help="File audio/video locale")
    transcribe.add_argument("--output", type=Path, help="Directory output finale")
    transcribe.add_argument("--format", dest="formats", choices=sorted(FORMATS), action="append", help="Formato export; ripetibile (default: txt)")
    transcribe.add_argument("--language", help="Codice lingua, es. it; omesso = rilevamento automatico")
    transcribe.add_argument("--model", choices=("tiny", "base", "small", "medium", "large-v2", "large-v3", "large-v3-turbo"))
    transcribe.add_argument("--backend", choices=("faster_whisper", "whisper_cpp"))
    transcribe.add_argument("--profile", choices=("fast", "balanced", "quality"))
    diarization = transcribe.add_mutually_exclusive_group()
    diarization.add_argument("--diarize", dest="diarize", action="store_true", help="Forza diarizzazione")
    diarization.add_argument("--no-diarize", dest="diarize", action="store_false", help="Disabilita diarizzazione")
    transcribe.set_defaults(diarize=None)
    transcribe.add_argument("--speakers", type=_positive_speakers, help="Numero speaker attesi (1-20)")
    return parser


def _default_output(input_path: Path) -> Path:
    safe_stem = re.sub(r"[^\w.-]+", "_", input_path.stem).strip("._") or "transcript"
    return Path.cwd() / "local-whisper-output" / safe_stem


def _request(args, config: dict) -> dict:
    diarization_start = config.get("diarization_start", "auto")
    diarize = config.get("diarization_enabled", True) and diarization_start != "off"
    if args.diarize is True:
        diarize, diarization_start = True, "auto"
    elif args.diarize is False:
        diarize, diarization_start = False, "off"
    token = os.environ.get("HF_TOKEN", config.get("hf_token", ""))
    if args.diarize is True and not token:
        raise RuntimeError("--diarize richiede un token HuggingFace in config.json o HF_TOKEN")
    return {
        "audio_path": str(args.input.resolve()), "model": args.model or config.get("default_model", "small"),
        "language": args.language, "diarize": bool(diarize and token), "hf_token": token,
        "expected_speakers": args.speakers,
        "diarization_model": config.get("diarization_model", "community-1"),
        "diarization_mode": config.get("diarization_mode", "conservative"),
        "diarization_start": diarization_start if diarize else "off",
        "diarization_device": config.get("diarization_device", "auto"),
        "performance_profile": args.profile or config.get("performance_profile", "balanced"),
        "transcription_backend": args.backend or config.get("transcription_backend", "faster_whisper"),
        "glossary": config.get("glossary", ""), "diarization_precision": config.get("diarization_precision", "segments"),
    }


def _write_outputs(destination: Path, formats: list[str], result: dict, request: dict) -> list[Path]:
    destination.mkdir(parents=True)
    written = []
    for fmt in formats:
        output = destination / f"transcript.{fmt}"
        if fmt == "json":
            payload = {
                "input": request["audio_path"], "language": result["language"], "duration": result["duration"],
                "model": result["model"], "backend": result["backend"], "diarization_ran": result["diarization_ran"],
                "diarization_error": result["diarization_error"], "metrics": result["metrics"], "segments": result["segments"],
            }
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            content, _media_type = render(result["segments"], fmt)
            output.write_text(content + ("" if content.endswith("\n") else "\n"), encoding="utf-8")
        written.append(output)
    return written


def transcribe(args) -> int:
    from backend import main

    input_path = args.input.expanduser()
    if not input_path.is_file():
        raise RuntimeError(f"File non trovato: {input_path}")
    if input_path.suffix.lower() not in main.ALLOWED_AUDIO_SUFFIXES:
        raise RuntimeError(f"Formato non supportato: {input_path.suffix or '(senza estensione)'}")

    destination = (args.output.expanduser() if args.output else _default_output(input_path)).resolve()
    if destination.exists():
        raise RuntimeError(f"Output già esistente: {destination}. Scegli --output diverso.")
    config = main.load_config()
    request = _request(args, config)
    formats = args.formats or ["txt"]

    def progress(stage, percent, message):
        print(f"[{percent:3d}%] {message or stage}", file=sys.stderr, flush=True)

    with tempfile.TemporaryDirectory(prefix="local-whisper-cli-") as temporary:
        result = run_file(request, Path(temporary), progress, main)
    written = _write_outputs(destination, formats, result, request)
    if request["diarize"] and result.get("diarization_error"):
        print(f"Avviso: diarizzazione non completata: {result['diarization_error']}", file=sys.stderr)
    for output in written:
        print(output)
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return transcribe(args)
    except RuntimeError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
