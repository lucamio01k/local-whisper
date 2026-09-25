import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import cli
from backend.exporting import render
from backend import runner


RESULT = {
    "segments": [{"id": 1, "start": 0.0, "end": 1.0, "speaker": "Speaker 1", "text": "Ciao mondo", "words": []}],
    "raw_segments": [], "language": "it", "duration": 1.0, "model": "small", "backend": "faster_whisper",
    "metrics": {}, "diarization_ran": True, "diarization_error": None,
}


class CliTests(unittest.TestCase):
    def test_parser_defaults_and_repeated_formats(self):
        args = cli.build_parser().parse_args(["transcribe", "input.mp3", "--format", "srt", "--format", "json"])
        self.assertEqual(args.formats, ["srt", "json"])
        self.assertIsNone(args.diarize)
        self.assertIsNone(cli.build_parser().parse_args(["transcribe", "input.mp3"]).formats)

    def test_parser_rejects_conflicting_diarization_and_invalid_speakers(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["transcribe", "input.mp3", "--diarize", "--no-diarize"])
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["transcribe", "input.mp3", "--speakers", "21"])

    def test_default_txt_output_and_existing_directory_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_path = root / "meeting.mp3"
            input_path.write_bytes(b"not-decoded-in-test")
            args = cli.build_parser().parse_args(["transcribe", str(input_path)])
            with patch("backend.cli.run_file", return_value=RESULT), patch("backend.main.load_config", return_value={"default_model": "small", "diarization_enabled": False}), patch("pathlib.Path.cwd", return_value=root):
                self.assertEqual(cli.transcribe(args), 0)
            output = root / "local-whisper-output" / "meeting" / "transcript.txt"
            self.assertIn("Ciao mondo", output.read_text())
            with patch("pathlib.Path.cwd", return_value=root), self.assertRaisesRegex(RuntimeError, "Output già esistente"):
                cli.transcribe(args)

    def test_requested_json_and_shared_exports(self):
        text, media_type = render(RESULT["segments"], "srt")
        self.assertEqual(media_type, "text/srt")
        self.assertIn("Speaker 1: Ciao mondo", text)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            input_path = root / "meeting.mp3"
            input_path.write_bytes(b"test")
            destination = root / "result"
            args = cli.build_parser().parse_args(["transcribe", str(input_path), "--output", str(destination), "--format", "txt", "--format", "json"])
            with patch("backend.cli.run_file", return_value=RESULT), patch("backend.main.load_config", return_value={"default_model": "small", "diarization_enabled": False}):
                self.assertEqual(cli.transcribe(args), 0)
            self.assertTrue((destination / "transcript.txt").exists())
            self.assertIn('"segments"', (destination / "transcript.json").read_text())

    def test_speaker_export_groups_consecutive_turns_without_changing_source(self):
        segments = [
            {"id": 1, "start": 0.0, "end": 30.0, "speaker": "Speaker 1", "text": "Prima parte"},
            {"id": 2, "start": 35.0, "end": 60.0, "speaker": "Speaker 1", "text": "Seconda parte"},
            {"id": 3, "start": 61.0, "end": 62.0, "speaker": "Speaker 2", "text": "Risposta"},
            {"id": 4, "start": 63.0, "end": 64.0, "speaker": "Speaker 1", "text": "Riprendo"},
        ]
        text, _ = render(segments, "txt", "speakers")
        lines = text.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("Prima parte Seconda parte", lines[0])
        self.assertIn("Speaker 2: Risposta", lines[1])
        self.assertIn("Speaker 1: Riprendo", lines[2])
        self.assertEqual(segments[0]["text"], "Prima parte")
        self.assertEqual(len(render(segments, "srt", "speakers")[0].split("Speaker 1:")), 4)

    def test_forced_diarization_requires_a_token(self):
        args = cli.build_parser().parse_args(["transcribe", "input.mp3", "--diarize"])
        with self.assertRaisesRegex(RuntimeError, "token HuggingFace"):
            cli._request(args, {"diarization_enabled": True, "hf_token": ""})

    def test_runner_uses_isolated_work_directory_and_cleans_job(self):
        from backend import main
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            previous_upload = main.UPLOAD_DIR
            captured = {}
            def fake_transcription(job_id, *args):
                captured["args"] = args
                main.jobs[job_id].update(status="done", segments=RESULT["segments"], raw_segments=RESULT["segments"], language="it", duration=1.0, model="small", transcription_backend="faster_whisper")
            with patch.object(main, "_run_transcription", side_effect=fake_transcription):
                result = runner.run_file({"audio_path": str(work / "input.mp3"), "model": "small", "diarize": False, "hf_token": "", "expected_speakers": None, "diarization_model": "community-1", "diarization_mode": "conservative", "diarization_start": "off", "diarization_device": "auto", "performance_profile": "balanced", "transcription_backend": "faster_whisper"}, work, lambda *_: None, main)
            self.assertEqual(result["segments"][0]["text"], "Ciao mondo")
            self.assertEqual(captured["args"][0], str(work / "input.mp3"))
            self.assertEqual(main.UPLOAD_DIR, previous_upload)
            self.assertFalse(any(work.iterdir()))


if __name__ == "__main__":
    unittest.main()
