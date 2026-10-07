import copy
import io
import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from backend import main, lab
from backend.storage import atomic_json, digest_file
from backend.refinement import (RefinementConfig, SpeakerRefiner, Resolution, Evidence, UNKNOWN,
                                AcousticSpeakerResolver, detect, refine, labeled_turns)
from backend.lab_dataset import parse_rttm, validate_reference, load_dataset
from backend.lab_metrics import evaluate, divergences
from backend.lab_models import LayaSpeakerRefiner, semantic_state
from backend.lab_worker import semantic_pass
from scripts.prepare_lab_subset import convert, crop_reference


def segment(speaker=None, **extra):
    return {'id': 1, 'start': 0., 'end': 2., 'text': 'ciao mondo', 'speaker': speaker,
            'words': [{'word': 'ciao', 'start': 0., 'end': 1., 'speaker': speaker},
                      {'word': 'mondo', 'start': 1., 'end': 2., 'speaker': speaker}], **extra}


class Fixed(SpeakerRefiner):
    name = 'test'
    def __init__(self, result): self.result, self.calls, self.closed = result, 0, False
    def resolve(self, evidence): self.calls += 1; return self.result
    def close(self): self.closed = True


class RefinementTests(unittest.TestCase):
    def test_safe_segments_never_called_and_inputs_immutable(self):
        original = [segment('A')]
        snapshot = copy.deepcopy(original)
        refiner = Fixed(Resolution('B', .99, {'B': .99, 'A': .01}, 'test'))
        final, decisions = refine(original, {'standard': [[0, 2, 'A']], 'exclusive': None}, refiner, RefinementConfig())
        self.assertEqual(refiner.calls, 0); self.assertTrue(refiner.closed)
        self.assertEqual(original, snapshot); self.assertEqual(final, snapshot); self.assertFalse(decisions)

    def test_tiny_secondary_candidate_does_not_expose_safe_case(self):
        refiner = Fixed(Resolution('B', .99, {'B': .99, 'A': .01}, 'test'))
        source = [segment('A', speaker_candidates=['A', 'B'])]
        final, log = refine(source, {'standard': [[0, 1.99, 'A'], [1.99, 2, 'B']], 'exclusive': None}, refiner, RefinementConfig())
        self.assertEqual(final, source); self.assertFalse(log); self.assertEqual(refiner.calls, 0)

    def test_unknown_tie_margin_changes_and_word_consistency(self):
        segs = [segment()]
        turns = {'standard': [[0, 1, 'A'], [1, 2, 'B']], 'exclusive': None}
        refiner = Fixed(Resolution('B', .96, {'B': .96, 'A': .1}, 'test', 'evidence'))
        final, decisions = refine(segs, turns, refiner, RefinementConfig())
        self.assertEqual(final[0]['speaker'], 'B')
        self.assertTrue(all(w['speaker'] == 'B' for w in final[0]['words']))
        self.assertIsNone(segs[0]['speaker'])
        self.assertTrue(decisions[0]['changed'])
        self.assertEqual(decisions[0]['evidence']['temporal'], {'A': .5, 'B': .5})
        for result in [Resolution(UNKNOWN, .99, {UNKNOWN: .99}), Resolution('B', .9, {'B': .9, 'A': .88}), Resolution('B', .6, {'B': .6, 'A': .2})]:
            output, decisions = refine(segs, turns, Fixed(result), RefinementConfig())
            self.assertIsNone(output[0]['speaker']); self.assertFalse(decisions[0]['accepted'])

    def test_semantic_conflict_and_no_support_abstain(self):
        segs = [segment()]
        turns = {'standard': [[0, 1, 'A'], [1, 2, 'B']], 'exclusive': None}
        prior = [{'segment_id': 1, 'accepted': False, 'evidence': {'acoustic': {'scores': {'A': .85, 'B': .7}}}}]
        fixed = Fixed(Resolution('B', .95, {'A': .01, 'B': .95, UNKNOWN: .04}, 'test'))
        out, decisions = refine(segs, turns, fixed, RefinementConfig(), prior=prior, semantic=True)
        self.assertIsNone(out[0]['speaker']); self.assertEqual(decisions[0]['policy'], 'acoustic_semantic_conflict')
        turns = {'standard': [[3, 4, 'B']], 'exclusive': None}
        fixed = Fixed(Resolution('B', .95, {'B': .95, UNKNOWN: .05}, 'test'))
        out, decisions = refine(segs, turns, fixed, RefinementConfig(), semantic=True)
        self.assertEqual(decisions[0]['policy'], 'no_independent_speaker_support')

    def test_failures_invalid_scores_and_invalid_candidate_fallback(self):
        class Broken(Fixed):
            def resolve(self, evidence): raise RuntimeError('boom')
        for fixed in [Broken(None), Fixed(Resolution('A', float('nan'))), Fixed(Resolution('Z', 1., {'Z': 1.}))]:
            out, decisions = refine([segment()], {'standard': [[0, 1, 'A']], 'exclusive': None}, fixed, RefinementConfig())
            self.assertIsNone(out[0]['speaker']); self.assertEqual(decisions[0]['policy'], 'fallback_original')

    def test_config_validation(self):
        for params in [{'acoustic_confidence': float('nan')}, {'context_turns': 2.5}, {'audio_window': 1}, {'unknown': 1}]:
            with self.assertRaises((ValueError, TypeError)): RefinementConfig(**params)

    def test_centroid_order_matches_chronological_display_labels(self):
        turns, labels = labeled_turns({'standard': [[2, 3, 'SPEAKER_00'], [0, 1, 'SPEAKER_01']], 'exclusive': None})
        self.assertEqual(labels, {'SPEAKER_01': 'Speaker 1', 'SPEAKER_00': 'Speaker 2'})
        self.assertEqual(turns['standard'][0][2], 'Speaker 2')

    def test_acoustic_short_mixed_and_real_cosine(self):
        import torch
        config = RefinementConfig()
        embed = MagicMock(return_value=[[1., 0.]])
        resolver = AcousticSpeakerResolver(embed, {'A': [1., 0.], 'B': [0., 1.]}, torch.zeros(1, 48000), 16000, config)
        evidence = Evidence(segment(), ['A', 'B'], [], [], {}, ['unknown_speaker'])
        output = resolver.resolve(evidence)
        self.assertEqual(output.speaker, 'A'); self.assertEqual(output.scores, {'A': 1., 'B': 0.})
        self.assertEqual(embed.call_args.args[0].shape, (1, 1, 32000))
        embed.reset_mock(); evidence.segment['end'] = .5
        self.assertEqual(resolver.resolve(evidence).reason, 'audio_too_short'); embed.assert_not_called()
        evidence.segment['end'] = 2; evidence.reasons.append('overlap')
        self.assertEqual(resolver.resolve(evidence).reason, 'mixed_speaker_window'); embed.assert_not_called()

    def test_laya_lazy_local_closed_choices_and_no_numeric_evidence(self):
        model = MagicMock()
        model.predict.return_value = {'answers': {'speaker': {'choice': 'B', 'probabilities': {'A': .01, 'B': .95, UNKNOWN: .04}}}}
        fake_sdk = MagicMock(); fake_sdk.load.return_value = model
        evidence = Evidence(segment(), ['A', 'B'], [segment('A')], [segment('B')], {'A': .6}, [], {'scores': {'B': .99}})
        with tempfile.TemporaryDirectory() as temp, patch('backend.lab_models.availability', return_value={'available': True}), patch.dict('os.environ', {'LOCAL_WHISPER_LAYA_PATH': temp}), patch.dict('sys.modules', {'laya': fake_sdk}):
            refiner = LayaSpeakerRefiner(); fake_sdk.load.assert_not_called()
            result = refiner.resolve(evidence)
            self.assertEqual(result.speaker, 'B'); self.assertEqual(result.confidence, .95)
            state = model.predict.call_args.args[0]
            self.assertNotIn('acoustic', state); self.assertNotIn('temporal', state)
            self.assertEqual(set(model.predict.call_args.args[1]['speaker']['criteria']), {'A', 'B', UNKNOWN})
            self.assertEqual(fake_sdk.load.call_args.kwargs['device'], 'cpu')
            refiner.close(); self.assertIsNone(refiner.model)


class SemanticWorkerTests(unittest.TestCase):
    def test_missing_interpreter_keeps_assignment_and_correct_provenance(self):
        source = [segment()]
        turns = {'standard': [[0, 1, 'A'], [1, 2, 'B']], 'exclusive': None}
        with patch('backend.lab_models.semantic_python', return_value='/nonexistent/local-whisper-python'):
            result, failure = semantic_pass(source, turns, {}, [], 'laya', main)
        self.assertTrue(failure)
        self.assertEqual(result['segments'], source)
        self.assertEqual(result['decisions'][0]['refiner'], 'laya_multilingual')
        self.assertEqual(result['decisions'][0]['reason'], 'model_unavailable')
        self.assertNotIn('lab-semantic', main.job_processes)

    def test_crashed_worker_preserves_acoustic_result_and_removes_scratch(self):
        fake = MagicMock(); fake.returncode = 1; fake.poll.return_value = 1
        source = [segment()]
        turns = {'standard': [[0, 1, 'A'], [1, 2, 'B']], 'exclusive': None}
        with patch('backend.lab_worker.subprocess.Popen', return_value=fake) as launch:
            result, failure = semantic_pass(source, turns, {}, [], 'laya', main)
        self.assertIn('Semantic worker failed', failure)
        self.assertEqual(result['segments'], source)
        self.assertFalse(Path(launch.call_args.args[0][-2]).parent.exists())
        self.assertNotIn('lab-semantic', main.job_processes)

    def test_timeout_terminates_separate_process_group_and_clears_registry(self):
        fake = MagicMock(); fake.poll.return_value = None
        fake.wait.side_effect = [subprocess.TimeoutExpired('semantic', 600), None]
        turns = {'standard': [[0, 1, 'A'], [1, 2, 'B']], 'exclusive': None}
        with patch('backend.lab_worker.subprocess.Popen', return_value=fake), patch.object(main, '_terminate_process') as stop:
            result, failure = semantic_pass([segment()], turns, {}, [], 'laya', main)
        self.assertTrue(failure); self.assertIsNone(result['segments'][0]['speaker'])
        stop.assert_called_once_with(fake)
        self.assertNotIn('lab-semantic', main.job_processes)


class DatasetConversionTests(unittest.TestCase):
    def test_ami_rejects_mixed_meetings_and_preserves_speakers(self):
        xml = '<root><w starttime="1" endtime="2">ciao</w></root>'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'ES2002a.A.words.xml').write_text(xml)
            (root/'ES2002a.B.words.xml').write_text(xml)
            reference = convert(root, 'ami')
            self.assertEqual({w['speaker'] for w in reference['words']}, {'A', 'B'})
            self.assertFalse(reference['verified'])
            (root/'ES2003a.A.words.xml').write_text(xml)
            with self.assertRaisesRegex(ValueError, 'one meeting'): convert(root, 'ami')

    def test_chime_requires_single_clock_and_whole_untimed_utterances(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'reference.json'
            path.write_text(json.dumps([{'start_time': {'mic1': 0, 'mic2': 1}, 'end_time': 2, 'speaker': 'A'}]))
            with self.assertRaisesRegex(ValueError, 'multi-clock'): convert(path, 'chime')
            path.write_text(json.dumps([{'start_time': 1, 'end_time': 3, 'speaker': 'A', 'words': 'ciao mondo'}]))
            reference = convert(path, 'chime')
            with self.assertRaisesRegex(ValueError, 'cuts'): crop_reference(reference, 2, 4)
            cropped = crop_reference(reference, 1, 3)
            self.assertEqual(cropped['turns'], [[0, 2, 'A']])
            self.assertEqual(cropped['words'], [{'word': 'ciao', 'speaker': 'A'}, {'word': 'mondo', 'speaker': 'A'}])
            self.assertNotIn('start', cropped['words'][0])


class MetricTests(unittest.TestCase):
    def test_permutation_wer_distinct_from_attribution_and_missing_gold(self):
        gold = {'verified': True, 'text': 'ciao mondo', 'turns': [[0, 2, 'gold']], 'words': [{'word': 'ciao', 'start': 0, 'end': 1, 'speaker': 'gold'}, {'word': 'mondo', 'start': 1, 'end': 2, 'speaker': 'gold'}]}
        result = evaluate(gold, [segment('other')], 2, [[0, 2, 'raw']])
        for name in ('wer', 'der', 'wder', 'speaker_confusion', 'missed_speech', 'false_alarm', 'pyannote_der'): self.assertEqual(result[name], 0)
        self.assertEqual(result['word_speaker_accuracy'], 1)
        wrong_text = [segment('other', text='ciao terra', words=[])]
        result = evaluate(gold, wrong_text, 2)
        self.assertEqual(result['wer'], .5); self.assertEqual(result['wder'], 0); self.assertEqual(result['aligned_correct_words'], 1)
        missing = evaluate({'verified': False}, [segment('A')], 2)
        self.assertIsNone(missing['der']); self.assertIsNone(missing['wer']); self.assertIsNone(missing['wder'])

    def test_wrong_speaker_confusion_overlap_and_no_speech(self):
        gold = {'verified': True, 'text': 'ciao mondo', 'turns': [[0, 1, 'A'], [1, 2, 'B']], 'words': [{'word': 'ciao', 'speaker': 'A'}, {'word': 'mondo', 'speaker': 'B'}]}
        result = evaluate(gold, [segment('X')], 2)
        self.assertEqual(result['wder'], .5)
        self.assertGreater(result['speaker_confusion'], 0)
        result = evaluate({'verified': True, 'text': '', 'turns': []}, [], 2)
        self.assertIsNone(result['wer']); self.assertIsNone(result['der'])
        result = evaluate({'verified': True, 'text': 'ciao mondo'}, [segment('A')], 2)
        self.assertEqual(result['wer'], 0); self.assertIsNone(result['der'])

    def test_rttm_and_reference_validation(self):
        self.assertEqual(parse_rttm('SPEAKER clip 1 0.5 1 <NA> <NA> A <NA> <NA>'), [[.5, 1.5, 'A']])
        for bad in [{'verified': 'true'}, {'turns': [[0, 3, 'A']]}, {'words': [{'word': 'hi', 'start': 1, 'end': 0}]}, {'uem': [[0, 1], [.5, 2]]}]:
            with self.assertRaises(ValueError): validate_reference(bad, 2)


class LabApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.patches = [patch.object(lab, 'LAB_DIR', self.root), patch.object(main, 'jobs', {}), patch.object(main, 'CONFIG_FILE', self.root/'config.json')]
        for p in self.patches: p.start()
        # Worker-mode harness skips startup/history; register isolated HTTP routes.
        if not any(getattr(route,'path',None)=='/api/lab' for route in main.app.routes):
            lab.register_lab_routes(main.app,main)
        self.client = TestClient(main.app)
        self.audio = io.BytesIO()
        with wave.open(self.audio, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(b'\0'*64000)
        self.gold = {'verified': True, 'text': 'ciao mondo', 'turns': [[0, 2, 'gold']], 'words': [{'word': 'ciao', 'start': 0, 'end': 1, 'speaker': 'gold'}, {'word': 'mondo', 'start': 1, 'end': 2, 'speaker': 'gold'}]}

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def upload(self):
        response = self.client.post('/api/lab/datasets', data={'title': 'fixture synthetic', 'verified': 'true'},
            files={'audio': ('fixture.wav', self.audio.getvalue(), 'audio/wav'), 'gold': ('gold.json', json.dumps(self.gold), 'application/json')})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def test_persistent_import_frozen_hash_bad_dataset_and_no_models(self):
        ds = self.upload()
        self.assertEqual(len(self.client.get('/api/lab/datasets').json()), 1)
        record, audio, reference = load_dataset(self.root, ds)
        self.assertTrue(reference['verified'])
        audio.write_bytes(b'tampered')
        response = self.client.post('/api/lab/runs', json={'dataset_id': ds})
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/lab/runs', json={'dataset_id': '../bad'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/lab').status_code, 200)

    def test_end_to_end_api_baseline_order_metrics_changes_and_reload(self):
        ds = self.upload(); calls = []
        def supervise(spec, folder, context, job):
            calls.append(spec['variant'])
            final = [segment('Speaker 1')]
            decisions = [] if spec['variant'] == 'baseline' else [{'segment_id': 1, 'changed': True}]
            metrics = evaluate(self.gold, final, 2, [[0, 2, 'SPEAKER_00']])
            result = {'status': 'done', 'segments': final, 'baseline_segments': final, 'decisions': decisions, 'warnings': [], 'metrics': metrics,
                      'performance': {'total_seconds': 1., 'rtf': .5, 'peak_ram_bytes': 1024, 'refiner_seconds': 0., 'ram_method': 'test'}}
            atomic_json(folder/'result.json', result)
            return result
        with patch.object(lab, 'supervise', side_effect=supervise):
            response = self.client.post('/api/lab/runs', json={'dataset_id': ds, 'variants': ['acoustic', 'acoustic_laya']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls, ['baseline', 'acoustic', 'acoustic_laya'])
        rid = response.json()['id']
        persisted = self.client.get('/api/lab/runs').json()[0]
        self.assertEqual(persisted['status'], 'done')
        self.assertEqual(persisted['results'][0]['metrics']['wer'], 0)
        self.assertEqual(persisted['results'][1]['delta']['der'], 0)
        self.assertEqual(self.client.get(f'/api/lab/runs/{rid}').status_code, 200)
        self.assertNotIn('hf_token', json.dumps(persisted))
        self.assertFalse(main.jobs); self.assertFalse(main.job_processes)

    def test_partial_results_failure_and_cleanup(self):
        ds = self.upload()
        with patch.object(lab, 'supervise', side_effect=RuntimeError('model failed')):
            response = self.client.post('/api/lab/runs', json={'dataset_id': ds})
        result = lab.read_record(response.json()['id'])
        self.assertEqual(result['status'], 'error'); self.assertEqual(result['error'], 'model failed')
        self.assertFalse(main.jobs)

    def test_supervisor_exception_terminates_worker_and_clears_registry(self):
        fake = MagicMock(); fake.pid = 999999; fake.poll.return_value = None
        job = {'id': 'lab-test', 'cancel_requested': False}
        folder = self.root/'case'; folder.mkdir()
        with patch.object(lab.subprocess, 'Popen', return_value=fake), patch.object(lab, 'sample_tree', return_value=(1, {fake.pid})), patch.object(lab.time, 'sleep', side_effect=RuntimeError('supervisor failed')), patch.object(main, '_terminate_process') as terminate:
            with self.assertRaisesRegex(RuntimeError, 'supervisor failed'): lab.supervise({}, folder, main, job)
            terminate.assert_called_once_with(fake)
        self.assertNotIn(job['id'], main.job_processes)

    def test_http_audio_range_reference_and_exports(self):
        ds=self.upload()
        response=self.client.get(f'/api/lab/datasets/{ds}/audio',headers={'Range':'bytes=0-9'})
        self.assertEqual(response.status_code,206); self.assertEqual(len(response.content),10)
        self.assertEqual(response.headers['content-range'],f'bytes 0-9/{len(self.audio.getvalue())}')
        self.assertEqual(self.client.get(f'/api/lab/datasets/{ds}/reference').json()['text'],self.gold['text'])
        record=lab.prepare_run({'dataset_id':ds},main)
        self.assertEqual(self.client.get(f"/api/lab/runs/{record['id']}/export?format=json").status_code,200)
        csv=self.client.get(f"/api/lab/runs/{record['id']}/export?format=csv")
        self.assertIn('provenance_json',csv.text); self.assertNotIn('hf_token',csv.text)

    def test_shared_deadline_preserves_completed_case_and_cleans_worker(self):
        ds=self.upload()
        result={'status':'done','segments':[segment('Speaker 1')],'baseline_segments':[segment('Speaker 1')],
                'decisions':[],'warnings':[],'metrics':evaluate(self.gold,[segment('Speaker 1')],2),
                'performance':{'total_seconds':1.,'rtf':.5,'peak_ram_bytes':1,'refiner_seconds':0}}
        with patch.object(lab,'supervise',side_effect=[result,TimeoutError('budget reached')]):
            response=self.client.post('/api/lab/runs',json={'dataset_id':ds,'variants':['baseline','acoustic']})
        record=lab.read_record(response.json()['id'])
        self.assertEqual(record['status'],'partial'); self.assertEqual(len(record['results']),1)
        self.assertFalse(main.jobs); self.assertFalse(main.job_processes)
        fake=MagicMock(); fake.pid=999999; fake.poll.return_value=None
        folder=self.root/'timeout'; folder.mkdir()
        with patch.object(lab.subprocess,'Popen',return_value=fake),patch.object(lab,'sample_tree',return_value=(1,{fake.pid})),patch.object(main,'_terminate_process') as terminate:
            with self.assertRaises(TimeoutError): lab.supervise({'deadline':0},folder,main,{'id':'timeout'})
            terminate.assert_called_once_with(fake)
        self.assertFalse(main.job_processes)

    def test_resource_and_full_mode_validation(self):
        ds=self.upload()
        for body in [{'mode':'other'},{'budget_seconds':float('inf')},{'resource_profile':'unknown'},
                     {'mode':'complete','variants':['acoustic','acoustic_laya']},{'mode':'frozen','repetitions':2}]:
            with self.assertRaises(ValueError): lab.prepare_run({'dataset_id':ds,**body},main)

    def test_canceled_queued_run_never_starts_worker(self):
        ds = self.upload()
        record = lab.prepare_run({'dataset_id': ds}, main)
        record['status'] = 'canceled'; atomic_json(lab.path_for(record['id'])/'record.json', record)
        with patch.object(lab, 'supervise') as worker:
            lab.execute_run(record['id'], main)
            worker.assert_not_called()
        self.assertFalse(main.jobs)

    def test_invalid_gold_removes_temporary_dataset(self):
        response = self.client.post('/api/lab/datasets', files={'audio': ('a.wav', self.audio.getvalue()), 'gold': ('a.json', '{invalid')})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(list((self.root/'datasets').glob('*')))


if __name__ == '__main__': unittest.main()
