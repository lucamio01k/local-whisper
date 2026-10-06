import asyncio
import json
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend import main, qwen_worker
from backend.quality import assign_speakers


class QwenIntegrationTests(unittest.TestCase):
    def test_model_backend_pair_and_catalog(self):
        with patch.object(main, '_qwen_supported', return_value=True), patch.object(main, '_qwen_downloaded', return_value=True):
            self.assertEqual(main.list_models('qwen3_asr')[0]['name'], main.QWEN_MODEL)
            main._validate_model_name(main.QWEN_MODEL, 'qwen3_asr')
            with self.assertRaises(HTTPException):
                main._validate_model_name('large-v3', 'qwen3_asr')
            with self.assertRaises(HTTPException):
                main._validate_model_name(main.QWEN_MODEL, 'whisper_cpp')
            with self.assertRaises(HTTPException):
                asyncio.run(main.download_model_sse(main.QWEN_MODEL, 'whisper_cpp'))

    def test_config_accepts_qwen_pair(self):
        cfg = {'transcription_backend': 'whisper_cpp', 'default_model': 'large-v3-turbo',
               'diarization_precision': 'segments', 'glossary': ''}
        with patch.object(main, '_qwen_supported', return_value=True), patch.object(main, 'load_config', return_value=cfg), patch.object(main, 'save_config') as save:
            main.post_config({'transcription_backend': 'qwen3_asr', 'default_model': main.QWEN_MODEL})
            self.assertEqual(save.call_args.args[0]['default_model'], main.QWEN_MODEL)

    def test_worker_adapts_segments_and_word_times(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / 'input.wav'
            with wave.open(str(audio), 'wb') as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(16000)
                writer.writeframes(b'\0\0' * 16000)

            calls = []
            class ASR:
                def generate(self, path, **kwargs):
                    calls.append(kwargs)
                    return types.SimpleNamespace(segments=[dict(start=0, end=1, text='ciao mondo', language='Italian')])
            class Aligner:
                def generate(self, **kwargs):
                    return types.SimpleNamespace(items=[types.SimpleNamespace(text='ciao', start_time=.1, end_time=.4)])
            stt = types.ModuleType('mlx_audio.stt')
            stt.load = lambda path: ASR() if path == 'asr' else Aligner()
            mlx_audio = types.ModuleType('mlx_audio')
            mlx_audio.stt = stt
            mlx = types.ModuleType('mlx')
            core = types.ModuleType('mlx.core')
            core.clear_cache = lambda: None
            core.set_memory_limit = lambda limit: None
            core.set_cache_limit = lambda limit: None
            core.get_peak_memory = lambda: 0
            mlx.core = core
            modules = {'mlx_audio': mlx_audio, 'mlx_audio.stt': stt, 'mlx': mlx, 'mlx.core': core}
            request = dict(audio=str(audio), language='it', words=True, asr_model='asr', aligner_model='aligner')
            with patch.dict(sys.modules, modules):
                result = qwen_worker.run(request, root / 'result.json')
            self.assertEqual(result['language'], 'it')
            self.assertEqual(result['segments'][0]['words'][0]['start'], .1)
            self.assertEqual(calls[0]['chunk_duration'], 30.0)
            self.assertGreater(calls[0]['max_tokens'], 512)
            self.assertFalse(list(root.glob('qwen_align_*.wav')))

    def test_worker_rejects_truncated_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / 'input.wav'
            with wave.open(str(audio), 'wb') as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(16000)
                writer.writeframes(b'\0\0' * 16000 * 60)
            class ASR:
                def generate(self, path, **kwargs):
                    return types.SimpleNamespace(segments=[dict(start=0, end=15, text='ciao')])
            stt = types.ModuleType('mlx_audio.stt')
            stt.load = lambda path: ASR()
            mlx_audio = types.ModuleType('mlx_audio')
            mlx_audio.stt = stt
            core = types.ModuleType('mlx.core')
            core.set_memory_limit = lambda limit: None
            core.set_cache_limit = lambda limit: None
            with patch.dict(sys.modules, {'mlx_audio': mlx_audio, 'mlx_audio.stt': stt,
                                         'mlx': types.ModuleType('mlx'), 'mlx.core': core}):
                with self.assertRaisesRegex(RuntimeError, 'risultato incompleto'):
                    qwen_worker.run(dict(audio=str(audio), language='it', words=False, asr_model='asr'), root / 'result.json')

    def test_chunks_bound_budget_offset_times_and_publish_incremental_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / 'input.wav'
            with wave.open(str(audio), 'wb') as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(16000)
                writer.writeframes(b'\0\0' * 16000 * 61)
            output = root / 'result.json'
            calls = []
            class ASR:
                def generate(self, path, **kwargs):
                    with wave.open(path) as reader:
                        duration = reader.getnframes() / reader.getframerate()
                    self_test.assertLessEqual(duration, 30)
                    self_test.assertEqual(kwargs['max_tokens'], 1024)
                    if calls:
                        checkpoint = json.loads((root / 'qwen_partial.json').read_text())
                        self_test.assertEqual(checkpoint['covered_seconds'], len(calls) * 30)
                    calls.append(duration)
                    return types.SimpleNamespace(segments=[dict(start=0, end=duration, text='ciao', language='Italian')], generation_tokens=10)
            self_test = self
            class Aligner:
                def generate(self, **kwargs):
                    return types.SimpleNamespace(items=[types.SimpleNamespace(text='ciao', start_time=.1, end_time=.5)])
            stt = types.ModuleType('mlx_audio.stt')
            stt.load = lambda path: ASR() if path == 'asr' else Aligner()
            core = types.ModuleType('mlx.core')
            core.set_memory_limit = lambda limit: None
            core.set_cache_limit = lambda limit: None
            core.clear_cache = lambda: None
            core.get_peak_memory = lambda: 123
            with patch.dict(sys.modules, {'mlx_audio': types.ModuleType('mlx_audio'),
                                         'mlx_audio.stt': stt, 'mlx': types.ModuleType('mlx'), 'mlx.core': core}):
                result = qwen_worker.run(dict(audio=str(audio), language='it', words=True,
                                             asr_model='asr', aligner_model='aligner'), output)
            self.assertEqual(calls, [30, 30, 1])
            self.assertEqual([s['start'] for s in result['segments']], [0, 30, 60])
            self.assertEqual(result['segments'][-1]['end'], 61)
            self.assertEqual(result['segments'][-1]['words'][0]['start'], 60.1)
            self.assertEqual(json.loads(Path(str(output) + '.progress').read_text())['end'], 61)
            self.assertFalse((root / 'qwen_chunk.wav').exists())

    def test_worker_stops_at_token_limit_without_accepting_truncated_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            audio = root / 'input.wav'
            with wave.open(str(audio), 'wb') as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(16000)
                writer.writeframes(b'\0\0' * 16000)
            stt = types.ModuleType('mlx_audio.stt')
            stt.load = lambda path: types.SimpleNamespace(generate=lambda *a, **kw:
                types.SimpleNamespace(generation_tokens=1024, text='ripetizione'))
            core = types.ModuleType('mlx.core')
            core.set_memory_limit = lambda limit: None
            core.set_cache_limit = lambda limit: None
            core.clear_cache = lambda: None
            with patch.dict(sys.modules, {'mlx_audio': types.ModuleType('mlx_audio'),
                                         'mlx_audio.stt': stt, 'mlx': types.ModuleType('mlx'), 'mlx.core': core}):
                with self.assertRaisesRegex(RuntimeError, 'generazione eccessiva'):
                    qwen_worker.run(dict(audio=str(audio), language='it', words=False, asr_model='asr'), root / 'result.json')
            self.assertFalse((root / 'qwen_chunk.wav').exists())

    def test_supervisor_terminates_worker_on_memory_limit_or_stall(self):
        for memory, now, error in ((qwen_worker.MEMORY_LIMIT_BYTES + 1, 1, 'limite memoria'),
                                   (0, 301, 'entro 5 minuti')):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                job_id = 'qwen-limit-test'
                job = dict(transcription_options={}, metrics={})
                class Process:
                    pid = 123
                    stopped = False
                    def poll(self): return 1 if self.stopped else None
                    def wait(self, **kw): self.stopped = True
                proc = Process()
                def terminate(*a, **kw): proc.stopped = True
                with patch.dict(main.jobs, {job_id: job}), patch.object(main, '_job_dir', return_value=root), \
                     patch.object(main, '_prepare_diarization_audio', return_value='audio.wav'), \
                     patch.object(main, '_audio_duration', return_value=60), \
                     patch.object(main, 'digest_file', return_value='test-audio-digest'), \
                     patch.object(main, '_qwen_snapshot', return_value=root), \
                     patch.object(main.subprocess, 'Popen', return_value=proc), \
                     patch.object(main, '_terminate_process', side_effect=terminate), \
                     patch.object(main.time, 'monotonic', side_effect=[0, now]), \
                     patch.object(qwen_worker, 'process_memory_bytes', return_value=memory):
                    with self.assertRaisesRegex(RuntimeError, error):
                        main._run_qwen_worker(job_id, 'audio.wav', 'it', {'word_timestamps': False}, lambda *a: None)
                self.assertTrue(proc.stopped)
                self.assertNotIn(job_id, main.job_processes)

    def test_partial_word_alignment_preserves_speaker_changes(self):
        segments = [dict(id=1, start=0, end=4, text='uno due tre quattro', words=[
            dict(word='uno', start=0, end=1),
            dict(word='due', start=1, end=1),
            dict(word='tre', start=2, end=3),
            dict(word='quattro', start=3, end=4),
        ])]
        turns = [(0, 2, 'A'), (2, 4, 'B')]
        result = assign_speakers(segments, turns, turns, 'words')
        self.assertEqual([item['speaker'] for item in result], ['Speaker 1', 'Speaker 2'])
        self.assertEqual(' '.join(item['text'] for item in result), segments[0]['text'])
        self.assertTrue(result[0]['words'][1]['timing_issue'])


if __name__ == '__main__':
    unittest.main()
