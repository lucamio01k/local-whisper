import json
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from fastapi import BackgroundTasks, HTTPException

from backend import main, qwen_worker
from backend.storage import atomic_json


def audio_file(path, seconds=61):
    with wave.open(str(path), 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b'\0\0' * 16000 * seconds)


def runtime(asr, aligner=None):
    stt = types.ModuleType('mlx_audio.stt')
    stt.load = lambda path: asr if path == 'asr' else aligner
    core = types.ModuleType('mlx.core')
    core.set_memory_limit = lambda limit: None
    core.set_cache_limit = lambda limit: None
    core.clear_cache = lambda: None
    core.get_peak_memory = lambda: 0
    return {'mlx_audio': types.ModuleType('mlx_audio'), 'mlx_audio.stt': stt,
            'mlx': types.ModuleType('mlx'), 'mlx.core': core}


class QwenResumeTests(unittest.TestCase):
    def test_exhausted_block_is_split_without_accepting_failed_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / 'audio.wav'
            audio_file(audio, 30)
            calls = []
            class ASR:
                def generate(self, path, **kwargs):
                    with wave.open(path) as reader:
                        duration = reader.getnframes() / reader.getframerate()
                    calls.append(duration)
                    return types.SimpleNamespace(generation_tokens=1024 if duration > 15 else 5,
                        segments=[dict(start=0, end=duration, text='failed text' if duration > 15 else 'ok')])
            with patch.dict(sys.modules, runtime(ASR())):
                result = qwen_worker.run(dict(audio=str(audio), asr_model='asr', language='it', words=False), root / 'result.json')
            self.assertEqual(calls, [30, 15, 15])
            self.assertEqual([s['text'] for s in result['segments']], ['ok', 'ok'])
            self.assertEqual([s['start'] for s in result['segments']], [0, 15])
            self.assertEqual(result['segments'][-1]['end'], 30)

    def test_resume_reuses_completed_blocks_and_rejects_changed_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / 'audio.wav'
            audio_file(audio)
            request = dict(audio=str(audio), asr_model='asr', language='it', words=False)
            output = root / 'result.json'
            calls = []
            class ASR:
                fail = True
                def generate(self, path, **kwargs):
                    calls.append(path)
                    if self.fail and len(calls) == 2:
                        raise RuntimeError('test interruption')
                    with wave.open(path) as reader:
                        duration = reader.getnframes() / reader.getframerate()
                    return types.SimpleNamespace(generation_tokens=5, segments=[dict(start=0, end=duration, text='ok')])
            asr = ASR()
            with patch.dict(sys.modules, runtime(asr)):
                with self.assertRaisesRegex(RuntimeError, 'interruption'):
                    qwen_worker.run(request, output)
                asr.fail = False
                calls.clear()
                result = qwen_worker.run({**request, 'resume': True}, output)
            self.assertEqual(len(calls), 2)
            self.assertEqual(result['metrics']['qwen_resumed_seconds'], 30)
            self.assertEqual([s['start'] for s in result['segments']], [0, 30, 60])
            self.assertEqual([s['id'] for s in result['segments']], [1, 2, 3])
            altered = {**qwen_worker.checkpoint_signature(request, 61), 'audio_sha256': 'changed'}
            with self.assertRaisesRegex(RuntimeError, 'non compatibile'):
                qwen_worker.load_checkpoint({**request, 'resume': True}, output, 61, altered)

    def test_resume_alignment_skips_completed_words(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / 'audio.wav'
            audio_file(audio)
            request = dict(audio=str(audio), asr_model='asr', aligner_model='aligner', language='it', words=True)
            class ASR:
                calls = 0
                def generate(self, path, **kwargs):
                    self.calls += 1
                    with wave.open(path) as reader:
                        duration = reader.getnframes() / reader.getframerate()
                    return types.SimpleNamespace(generation_tokens=5, segments=[dict(start=0, end=duration, text='ok')])
            class Aligner:
                calls = 0
                fail = True
                def generate(self, **kwargs):
                    self.calls += 1
                    if self.fail and self.calls == 2: raise RuntimeError('alignment interruption')
                    return types.SimpleNamespace(items=[types.SimpleNamespace(text='ok', start_time=.1, end_time=.5)])
            asr, aligner = ASR(), Aligner()
            with patch.dict(sys.modules, runtime(asr, aligner)):
                with self.assertRaisesRegex(RuntimeError, 'alignment interruption'):
                    qwen_worker.run(request, root / 'result.json')
                asr.calls = 0
                aligner.fail = False
                aligner.calls = 0
                result = qwen_worker.run({**request, 'resume': True}, root / 'result.json')
            self.assertEqual(asr.calls, 0)
            self.assertEqual(aligner.calls, 2)
            self.assertEqual(result['segments'][-1]['words'][0]['start'], 60.1)

    def test_legacy_checkpoint_matches_source_and_original_request(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / 'audio.wav'
            audio_file(audio, 30)
            request = dict(audio=str(audio), asr_model='asr', language=None, resume=True, source_sha256='original')
            request['legacy_request'] = {k: request[k] for k in ('audio', 'asr_model', 'language')}
            atomic_json(root / 'normalized.json', dict(sha256='original'))
            atomic_json(root / 'qwen_partial.json', dict(duration=30, covered_seconds=30,
                                                       segments=[dict(start=0, end=30, text='ok')]))
            signature = qwen_worker.checkpoint_signature(request, 30)
            self.assertEqual(qwen_worker.load_checkpoint(request, root / 'result.json', 30, signature)[1], 30)
            request['source_sha256'] = 'different'
            with self.assertRaisesRegex(RuntimeError, 'non compatibile'):
                qwen_worker.load_checkpoint(request, root / 'result.json', 30, signature)

    def test_resume_endpoint_queues_original_job_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio_file(root / 'audio.wav', 61)
            atomic_json(root / 'qwen_partial.json', dict(duration=61, covered_seconds=30, segments=[]))
            job = dict(status='error', transcription_backend='qwen3_asr', model=main.QWEN_MODEL,
                       request_diarize=False, request_language='it', performance_profile='balanced')
            tasks = BackgroundTasks()
            with patch.dict(main.jobs, {'resume-test': job}), patch.object(main, '_job_dir', return_value=root), \
                 patch.object(main, '_find_audio_file', return_value=root / 'audio.wav'), \
                 patch.object(main, '_qwen_supported', return_value=True), \
                 patch.object(main, '_qwen_downloaded', return_value=True), \
                 patch.object(main, 'load_config', return_value={}):
                result = main.resume_qwen_job('resume-test', tasks)
                self.assertEqual(result['resumed_seconds'], 30)
                self.assertEqual(job['status'], 'queued')
                self.assertTrue(job['qwen_resume'])
                self.assertEqual(tasks.tasks[0].args[0], 'resume-test')
                with self.assertRaises(HTTPException) as duplicate:
                    main.resume_qwen_job('resume-test', BackgroundTasks())
                self.assertEqual(duplicate.exception.status_code, 409)


if __name__ == '__main__':
    unittest.main()
