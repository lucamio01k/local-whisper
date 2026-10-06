import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient

from backend import main


class AutomaticBackendTests(unittest.TestCase):
    def test_diarization_override_and_effective_word_options(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = {'whisper_backend_preference': 'auto', 'diarization_enabled': False,
                   'diarization_start': 'off', 'diarization_precision': 'segments',
                   'glossary': '', 'hf_token': 'test-token'}
            with patch.object(main, 'UPLOAD_DIR', Path(folder)), patch.object(main, 'jobs', {}), patch.object(main, 'load_config', return_value=cfg), patch.object(main, '_run_transcription'), patch.object(main, '_qwen_supported', return_value=True), patch.object(main, '_qwen_downloaded', return_value=True):
                client = TestClient(main.app)
                for model in (main.QWEN_MODEL, 'large-v3-turbo'):
                    for precision, profile, enabled, expected_profile in (
                        ('segments', 'balanced', 'true', 'balanced'),
                        ('segments', 'quality', 'true', 'quality'),
                        ('words', 'balanced', 'true', 'quality'),
                        ('words', 'balanced', 'false', 'balanced'),
                    ):
                        response = client.post('/api/transcribe', data={
                            'model_name': model, 'diarize': enabled,
                            'diarization_start': 'auto' if enabled == 'true' else 'off',
                            'diarization_precision': precision, 'performance_profile': profile,
                            'expected_speakers': '3' if enabled == 'true' else '',
                        }, files={'file': ('sample.wav', b'audio', 'audio/wav')})
                        self.assertEqual(response.status_code, 200, response.text)
                        job = main.jobs[response.json()['job_id']]
                        self.assertEqual(job['request_diarize'], enabled == 'true')
                        self.assertEqual(job['performance_profile'], expected_profile)
                        self.assertEqual(job['diarization_precision'], precision if enabled == 'true' else 'segments')
                        self.assertEqual(job['expected_speakers'], 3 if enabled == 'true' else None)

                cfg['hf_token'] = ''
                response = client.post('/api/transcribe', data={
                    'model_name': main.QWEN_MODEL, 'diarize': 'true', 'diarization_start': 'auto',
                }, files={'file': ('sample.wav', b'audio', 'audio/wav')})
                self.assertEqual(response.status_code, 400)
                self.assertIn('HuggingFace', response.json()['detail'])

    def test_transcribe_api_routes_model_and_rejects_incompatible_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = {'whisper_backend_preference': 'auto', 'diarization_enabled': False,
                   'diarization_start': 'off', 'diarization_precision': 'segments',
                   'glossary': '', 'hf_token': ''}
            with patch.object(main, 'UPLOAD_DIR', Path(folder)), patch.object(main, 'jobs', {}), patch.object(main, 'load_config', return_value=cfg), patch.object(main, '_run_transcription'), patch.object(main, '_qwen_supported', return_value=True), patch.object(main, '_qwen_downloaded', return_value=True), patch.object(main.platform, 'system', return_value='Darwin'), patch.object(main.platform, 'machine', return_value='arm64'):
                client = TestClient(main.app)
                for model, expected in [('large-v3-turbo', 'whisper_cpp'), (main.QWEN_MODEL, 'qwen3_asr')]:
                    response = client.post('/api/transcribe', data={'model_name': model, 'diarize': 'false'}, files={'file': ('sample.wav', b'audio', 'audio/wav')})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(main.jobs[response.json()['job_id']]['transcription_backend'], expected)
                cfg['whisper_backend_preference'] = 'faster_whisper'
                response = client.post('/api/transcribe', data={'model_name': 'large-v3-turbo', 'diarize': 'false'}, files={'file': ('sample.wav', b'audio', 'audio/wav')})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(main.jobs[response.json()['job_id']]['transcription_backend'], 'faster_whisper')
                response = client.post('/api/transcribe', data={'model_name': main.QWEN_MODEL, 'transcription_backend': 'whisper_cpp'}, files={'file': ('sample.wav', b'audio', 'audio/wav')})
                self.assertEqual(response.status_code, 400)

    def test_config_saves_auto_preference_independently_of_legacy_backend(self):
        cfg = {'transcription_backend': 'faster_whisper', 'default_model': 'large-v3-turbo',
               'diarization_precision': 'segments', 'glossary': ''}
        with patch.object(main, 'load_config', return_value=cfg), patch.object(main, 'save_config') as save, patch.object(main.platform, 'system', return_value='Darwin'), patch.object(main.platform, 'machine', return_value='arm64'):
            main.post_config({'whisper_backend_preference': 'auto', 'default_model': 'large-v3-turbo'})
        self.assertEqual(save.call_args.args[0]['whisper_backend_preference'], 'auto')

    def test_model_routes_to_correct_backend(self):
        with patch.object(main.platform, 'system', return_value='Darwin'), patch.object(main.platform, 'machine', return_value='arm64'):
            self.assertEqual(main._backend_for_model('large-v3-turbo'), 'whisper_cpp')
            self.assertEqual(main._backend_for_model('large-v3', 'faster_whisper'), 'faster_whisper')
            self.assertEqual(main._backend_for_model(main.QWEN_MODEL, 'faster_whisper'), 'qwen3_asr')
        with patch.object(main.platform, 'system', return_value='Linux'):
            self.assertEqual(main._backend_for_model('large-v3-turbo'), 'faster_whisper')

    def test_catalog_uses_resolved_backend_and_configuration(self):
        with patch.object(main, 'load_config', return_value={'whisper_backend_preference': 'faster_whisper'}), patch.object(main, 'list_models', side_effect=lambda backend: [{'name': main.QWEN_MODEL if backend == 'qwen3_asr' else 'large-v3-turbo', 'downloaded': True}]):
            catalog = main.model_catalog()
        self.assertEqual([item['backend'] for item in catalog], ['faster_whisper', 'qwen3_asr'])

    def test_retry_preserves_failed_job_and_copies_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original_dir = root / 'failed'
            original_dir.mkdir()
            (original_dir / 'audio.wav').write_bytes(b'audio')
            original = {
                'status': 'error', 'fallback_available': True,
                'transcription_backend': 'whisper_cpp', 'model': 'large-v3-turbo',
                'filename': 'meeting.wav', 'request_language': 'it',
                'request_diarize': False, 'performance_profile': 'balanced',
                'glossary': 'Nexi', 'diarization_precision': 'segments',
            }
            tasks = BackgroundTasks()
            with patch.object(main, 'UPLOAD_DIR', root), patch.object(main, 'jobs', {'failed': original}), patch.object(main, 'is_model_downloaded', return_value=True), patch.object(main, 'load_config', return_value={'hf_token': ''}):
                result = main.retry_with_faster_whisper('failed', tasks)
                new_id = result['job_id']
                self.assertEqual(main.jobs['failed']['status'], 'error')
                self.assertEqual(main.jobs[new_id]['transcription_backend'], 'faster_whisper')
                self.assertEqual(main.jobs[new_id]['retry_of'], 'failed')
                self.assertEqual((root / new_id / 'audio.wav').read_bytes(), b'audio')
                self.assertEqual(tasks.tasks[0].args[1], str(root / new_id / 'audio.wav'))
                main.jobs['failed']['fallback_available'] = False
                with self.assertRaises(HTTPException) as error:
                    main.retry_with_faster_whisper('failed', BackgroundTasks())
                self.assertEqual(error.exception.status_code, 409)

    def test_failed_job_survives_reload_without_becoming_done(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'failed').mkdir()
            (root / 'failed' / 'audio.wav').write_bytes(b'audio')
            jobs = {'failed': {
                'status': 'error', 'stage': 'error', 'error': 'Metal error',
                'fallback_available': True, 'model': 'large-v3-turbo',
                'transcription_backend': 'whisper_cpp', 'segments': [],
                'request_language': 'it', 'request_diarize': False,
            }}
            with patch.object(main, 'UPLOAD_DIR', root), patch.object(main, 'jobs', jobs):
                main.save_job_to_disk('failed')
                jobs.clear()
                self.assertTrue(main._load_job_from_disk('failed'))
                self.assertEqual(jobs['failed']['status'], 'error')
                self.assertTrue(jobs['failed']['fallback_available'])


if __name__ == '__main__':
    unittest.main()
