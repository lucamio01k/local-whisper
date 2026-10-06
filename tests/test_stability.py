"""Regression checks for restart recovery and independent worker failures."""
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from backend import main
from backend.storage import atomic_json


class StabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        for name, value in [('UPLOAD_DIR', self.root), ('jobs', {}), ('job_processes', {})]:
            p = patch.object(main, name, value)
            p.start()
            self.addCleanup(p.stop)

    def saved_job(self, status='queued', segments=None):
        folder = self.root / 'test'
        folder.mkdir(exist_ok=True)
        (folder / 'audio.wav').write_bytes(b'audio')
        main.jobs['test'] = dict(status=status, model=main.QWEN_MODEL,
            transcription_backend='qwen3_asr', request_diarize=False,
            segments=segments or [], title='会議 🎙️')
        main.save_job_to_disk('test')
        return folder

    def test_restart_marks_incomplete_job_and_keeps_checkpoint(self):
        folder = self.saved_job()
        atomic_json(folder / 'qwen_partial.json', dict(duration=60, covered_seconds=30))
        main.jobs.clear()
        main.load_jobs_from_disk()
        self.assertEqual(main.jobs['test']['status'], 'error')
        self.assertIn('riavvio', main.jobs['test']['error'])
        self.assertEqual(main.get_job('test')['qwen_resume_available']['covered_seconds'], 30)

    def test_canceled_job_remains_in_history(self):
        self.saved_job('canceled')
        self.assertEqual(main.get_history()[0]['status'], 'canceled')

    def test_unicode_export_filename(self):
        self.saved_job('done', [dict(id=1, start=0, end=1, text='test', speaker=None)])
        response = TestClient(main.app).get('/api/jobs/test/export/txt')
        self.assertEqual(response.status_code, 200)
        self.assertIn("filename*=UTF-8''", response.headers['content-disposition'])
        self.assertIn('%E4%BC%9A', response.headers['content-disposition'])

    def test_simultaneous_resume_schedules_only_once(self):
        folder = self.saved_job('error')
        atomic_json(folder / 'qwen_partial.json', dict(duration=60, covered_seconds=30))
        barrier = threading.Barrier(2)
        original = main._qwen_resume_details
        def slow_details(*args):
            result = original(*args)
            time.sleep(.05)
            return result
        def resume():
            tasks = BackgroundTasks()
            barrier.wait()
            try:
                main.resume_qwen_job('test', tasks)
                return len(tasks.tasks)
            except HTTPException as error:
                return error.status_code
        with patch.object(main, '_qwen_downloaded', return_value=True), \
             patch.object(main, '_qwen_supported', return_value=True), \
             patch.object(main, 'load_config', return_value={}), \
             patch.object(main, '_qwen_resume_details', side_effect=slow_details), \
             ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: resume(), range(2)))
        self.assertEqual(sorted(results), [1, 409])

    def test_worker_is_terminated_if_supervisor_raises(self):
        self.saved_job()
        proc = Mock()
        proc.poll.return_value = None
        with patch.object(main.subprocess, 'Popen', return_value=proc), \
             patch.object(main.time, 'sleep', side_effect=RuntimeError('supervisor failed')), \
             patch.object(main, '_terminate_process') as terminate:
            with self.assertRaisesRegex(RuntimeError, 'supervisor failed'):
                main._run_diarization_worker('test', 'audio.wav', [], '')
        terminate.assert_called_once_with(proc)
        proc.wait.assert_called_once_with(timeout=5)
        self.assertNotIn('test', main.job_processes)

    def test_bad_progress_does_not_abort_worker(self):
        folder = self.saved_job()
        proc = Mock()
        proc.poll.side_effect = [None, 0, 0]
        def started(*args, **kwargs):
            (folder / 'diarization_progress.json').write_text('{broken')
            atomic_json(folder / 'diarization_result.json', dict(ok=True, segments=[], turns={}))
            return proc
        with patch.object(main.subprocess, 'Popen', side_effect=started), \
             patch.object(main.time, 'sleep'):
            self.assertEqual(main._run_diarization_worker('test', 'audio.wav', [], ''), [])

    def test_upload_is_persisted_before_background_work(self):
        with patch.object(main, 'load_config', return_value={}), patch.object(main, '_run_transcription'):
            response = TestClient(main.app).post('/api/transcribe', data={'diarize': 'false'},
                files={'file': ('audio.wav', b'audio', 'audio/wav')})
        self.assertEqual(response.status_code, 200)
        folder = self.root / response.json()['job_id']
        self.assertEqual(json.loads((folder / 'meta.json').read_text())['status'], 'queued')

    def test_missing_source_does_not_leave_ghost_job(self):
        with patch.object(main, 'load_config', return_value={}):
            response = TestClient(main.app).post('/api/transcribe', data={'diarize': 'false'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(main.jobs, {})
        self.assertEqual(list(self.root.iterdir()), [])
