import asyncio
import json
import sys
import tempfile
import types
import unittest
import numpy as np
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from backend import engine, main
from backend.diarization_progress import DiarizationProgress
from backend.storage import atomic_json


class DiarizationProgressTests(unittest.TestCase):
    def test_numpy_counters_are_json_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'progress.json'
            hook = DiarizationProgress(path)
            for scalar in (np.int64, np.int32, np.uint64):
                hook('segmentation', None, total=scalar(100), completed=scalar(42))
                saved = json.loads(path.read_text())
                self.assertEqual(saved['percent'], 42)
                self.assertEqual(saved['completed'], 42)
                self.assertEqual(saved['total'], 100)

    def test_progress_write_error_does_not_abort_pipeline(self):
        hook = DiarizationProgress('progress.json')
        with patch('backend.diarization_progress.atomic_json', side_effect=OSError('disk unavailable')) as write:
            hook('segmentation', None, total=np.int64(100), completed=np.int64(42))
            hook('embeddings', None, total=np.int64(200), completed=np.int64(50))
        self.assertEqual(write.call_count, 1)
        self.assertIsNone(hook.path)

    def test_real_counters_and_uncounted_clustering(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'progress.json'
            hook = DiarizationProgress(path)
            hook('segmentation', None, total=200, completed=50)
            self.assertEqual(json.loads(path.read_text())['percent'], 25)
            hook('embeddings', None, total=10, completed=7)
            self.assertEqual(json.loads(path.read_text())['percent'], 70)
            hook('embeddings', object())
            result = json.loads(path.read_text())
            self.assertEqual(result['step'], 'clustering')
            self.assertIsNone(result['percent'])
            self.assertNotIn('%', result['message'])
            hook('unknown', None, total=0, completed=0)
            self.assertIsNone(json.loads(path.read_text())['percent'])

    def test_engine_passes_hook_to_pipeline(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'progress.json'
            observed = []
            class Pipeline:
                device = 'cpu'
                @classmethod
                def from_pretrained(cls, model, token=None): return cls()
                def to(self, device): return self
                def __call__(self, audio, **kwargs):
                    kwargs['hook']('segmentation', None, file=audio, total=4, completed=2)
                    observed.append(json.loads(path.read_text())['percent'])
                    kwargs['hook']('embeddings', None, file=audio, total=8, completed=6)
                    observed.append(json.loads(path.read_text())['percent'])
                    return types.SimpleNamespace(itertracks=lambda **kw: [])
            pyannote_audio = types.ModuleType('pyannote.audio')
            pyannote_audio.Pipeline = Pipeline
            torch = types.ModuleType('torch')
            torch.device = lambda value: value
            with patch.dict(sys.modules, {'pyannote': types.ModuleType('pyannote'),
                                         'pyannote.audio': pyannote_audio, 'torch': torch}), \
                 patch.object(engine, '_load_diarization_waveform', return_value={'uri': 'test'}):
                result = engine.infer_turns('audio.wav', [], '', diarization_device='cpu',
                                            progress=DiarizationProgress(path))
            self.assertEqual(observed, [50, 75])
            self.assertEqual(result['standard'], [])

    def test_supervisor_forwards_worker_progress_without_changing_overall_percent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job = dict(status='processing', stage='diarizing', progress=84, message='Diarizzazione in corso…')
            proc = Mock()
            proc.poll.side_effect = [None, 0, 0]
            def launch(*args, **kwargs):
                payload = json.loads((root / 'diarization_request.json').read_text())
                hook = DiarizationProgress(payload['progress_path'])
                hook('segmentation', None, total=100, completed=42)
                atomic_json(root / 'diarization_result.json', dict(ok=True, segments=[], turns={}))
                return proc
            with patch.dict(main.jobs, {'progress-test': job}), \
                 patch.object(main, '_job_dir', return_value=root), \
                 patch.object(main.subprocess, 'Popen', side_effect=launch), \
                 patch.object(main.time, 'sleep'):
                main._run_diarization_worker('progress-test', 'audio.wav', [], '')
            self.assertIn('42%', job['message'])
            self.assertEqual(job['diarization_progress']['percent'], 42)
            self.assertEqual(job['progress'], 84)

    def test_sse_emits_message_changes_at_same_overall_percent(self):
        async def exercise():
            job = dict(status='processing', stage='diarizing', progress=84, message='Segmentazione audio: 10%')
            with patch.dict(main.jobs, {'events-test': job}), patch.object(main.asyncio, 'sleep', new=AsyncMock()):
                response = await main.job_events('events-test')
                stream = response.body_iterator
                first = await anext(stream)
                job['message'] = 'Segmentazione audio: 20%'
                second = await anext(stream)
                await stream.aclose()
            self.assertIn('10%', first)
            self.assertIn('20%', second)
        asyncio.run(exercise())


if __name__ == '__main__':
    unittest.main()
