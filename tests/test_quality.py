import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend import main as app
from backend.quality import cpp_time, cpp_words, assign_speakers, diagnostics, subtitle_segments
from backend.storage import atomic_json, active, commit, legacy_version


def word(text, start, end, **kw):
    return {'word': text, 'start': start, 'end': end, **kw}


class AlignmentTests(unittest.TestCase):
    def test_offsets(self):
        for value in (0, 500, 1000, 1500):
            self.assertEqual(cpp_time({'offsets': {'from': value}}, 'from'), value / 1000)
        self.assertEqual(cpp_time({'timestamps': {'from': '00:00:00,000'}, 'offsets': {'from': 100}}, 'from'), 0)

    def test_tokens_and_dtw(self):
        seg = {'text': ' Ciao, mondo!', 'offsets': {'from': 0, 'to': 3000}, 'tokens': [
            {'text': '[_BEG_]', 't_dtw': -1},
            {'text': ' Ci', 't_dtw': 20, 'p': .9}, {'text': 'ao', 't_dtw': 35, 'p': .8},
            {'text': ',', 't_dtw': 40, 'p': .8}, {'text': ' mondo', 't_dtw': 170, 'p': .9}, {'text': '!', 't_dtw': 200, 'p': .9}]}
        words = cpp_words(seg, True)
        self.assertEqual(''.join(w['word'] for w in words), seg['text'])
        self.assertEqual(len(words), 2)
        self.assertEqual(words[0]['end'], words[1]['start'])
        self.assertEqual(words[0]['start'], 0)
        self.assertEqual(words[-1]['end'], 3)
        seg['tokens'][1]['t_dtw'] = -1
        self.assertTrue(all(w['timing_issue'] for w in cpp_words(seg, True)))
        seg['tokens'][1]['text'] = 'missing'
        self.assertEqual(cpp_words(seg, True), [])

    def test_attribution_and_brief_speaker(self):
        segments = [{'id': 1, 'start': 0, 'end': 3, 'text': 'Ciao sì bene', 'words': [word('Ciao', 0, 1), word(' sì', 1, 1.2), word(' bene', 1.2, 3)]}]
        turns = [(0, 1, 'A'), (1, 1.2, 'B'), (1.2, 3, 'A')]
        result = assign_speakers(segments, turns, turns)
        self.assertEqual([s['speaker'] for s in result], ['Speaker 1', 'Speaker 2', 'Speaker 1'])
        self.assertEqual(''.join(s['text'] for s in result), segments[0]['text'])
        self.assertEqual(len(app._conservative_merge_speakers(result)), 3)

    def test_sum_tie_and_missing(self):
        segments = [{'id': 1, 'start': 0, 'end': 7, 'text': 'test'}]
        result = assign_speakers(segments, [(0, 2, 'A'), (2, 5, 'B'), (5, 7, 'A')])
        self.assertEqual(result[0]['speaker'], 'Speaker 1')
        result = assign_speakers(segments, [(0, 3.5, 'A'), (3.5, 7, 'B')])
        self.assertIsNone(result[0]['speaker'])
        self.assertIsNone(assign_speakers(segments, [])[0]['speaker'])

    def test_diagnostics(self):
        segs = [{'id': 1, 'start': 0, 'end': 1, 'text': 'come va oggi ' * 3, 'speaker': None, 'words': [word('come', 0, 1, prob=.2)]}]
        kinds = {x['kind'] for x in diagnostics(segs)}
        self.assertTrue({'repetition', 'unknown_speaker', 'low_probability'} <= kinds)

    def test_captions_and_legacy(self):
        words = [word(' parola', i * .5, (i+1)*.5) for i in range(40)]
        segs = [{'start': 0, 'end': 20, 'speaker': 'A', 'text': ' parola'*40, 'words': words}]
        cues = subtitle_segments(segs)
        for cue in cues:
            self.assertLessEqual(cue['end'] - cue['start'], 7)
            self.assertLessEqual(len(cue['text'].splitlines()), 2)
            self.assertTrue(all(len(line) <= 42 for line in cue['text'].splitlines()))
        legacy = {'start': 0, 'end': 90, 'text': 'a legacy passage', 'speaker': None}
        self.assertEqual(subtitle_segments([legacy])[0]['end'], 90)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(app, 'UPLOAD_DIR', self.root), patch.object(app, 'CONFIG_FILE', self.root/'config.json'), patch.object(app, 'jobs', {})]
        for p in self.patches: p.start()
        self.client = TestClient(app.app)
        self.directory = self.root / 'test'; self.directory.mkdir()
        self.segs = [{'id': 1, 'start': 0., 'end': 2., 'text': 'Ciao mondo', 'speaker': 'Luca', 'words': [word('Ciao', 0, 1, speaker='Luca'), word(' mondo', 1, 2, speaker='Luca')]}]
        atomic_json(self.directory/'transcript.json', [{**s, 'speaker': None} for s in self.segs])
        atomic_json(self.directory/'diarized.json', self.segs)
        atomic_json(self.directory/'meta.json', {'id': 'test', 'title': 'Originale', 'duration': 2., 'model': 'large-v3-turbo'})
        app._load_job_from_disk('test')
        self.version = app.jobs['test']['version']

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def test_edit_stale_restore_and_raw(self):
        raw = (self.directory/'transcript.json').read_bytes()
        r = self.client.patch('/api/jobs/test/segments/1', json={'version': self.version, 'speaker': 'Pawel'})
        self.assertEqual(r.status_code, 200, r.text)
        version = r.json()['version']
        r = self.client.patch('/api/jobs/test/segments/1', json={'version': self.version, 'text': 'stale'})
        self.assertEqual(r.status_code, 409)
        r = self.client.post('/api/jobs/test/restore', json={'version': version, 'target_version': self.version})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['segments'][0]['speaker'], 'Luca')
        self.assertEqual(raw, (self.directory/'transcript.json').read_bytes())
        app._load_job_from_disk('test')
        self.assertEqual(app.jobs['test']['segments'][0]['speaker'], 'Luca')

    def test_split_and_manual_text(self):
        r = self.client.patch('/api/jobs/test/segments/1', json={'version': self.version, 'split_word': 1})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(''.join(s['text'] for s in r.json()['segments']), 'Ciao mondo')
        r = self.client.patch('/api/jobs/test/segments/1', json={'version': r.json()['version'], 'text': 'Buongiorno'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['segments'][0]['words'], [])

    def test_atomic_head_failure(self):
        first = commit(self.directory, self.segs, {}, 'first')
        original = active(self.directory)
        import backend.storage as storage
        real = storage.atomic_json
        def fail(path, value):
            if Path(path).name == 'HEAD.json': raise OSError('simulated disk failure')
            return real(path, value)
        with patch.object(storage, 'atomic_json', fail):
            with self.assertRaises(OSError): commit(self.directory, [], {}, 'failed')
        self.assertEqual(active(self.directory), original)

    def test_proposal_conflict_and_apply(self):
        pid = 'abc123'
        proposal = {'status': 'ready', 'base_version': self.version, 'full': False, 'start': 0, 'end': 2,
                    'segments': [{'id': 1, 'start': 0, 'end': 2, 'text': 'Nuovo testo', 'speaker': 'Luca'}]}
        atomic_json(self.directory/'proposals'/f'{pid}.json', proposal)
        r = self.client.post(f'/api/jobs/test/proposals/{pid}/apply', json={'version': self.version})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['segments'][0]['text'], 'Nuovo testo')
        again = self.client.post(f'/api/jobs/test/proposals/{pid}/apply', json={'version': r.json()['version']})
        self.assertEqual(again.status_code, 409)

    def test_rename_is_versioned_and_keeps_words(self):
        r = self.client.post('/api/jobs/test/speakers', json={'version': self.version, 'mapping': {'Luca': 'Pawel'}})
        self.assertEqual(r.status_code, 200, r.text)
        current = active(self.directory)
        self.assertEqual(current['segments'][0]['words'][0]['speaker'], 'Pawel')
        self.assertNotEqual(current['version'], self.version)

    def test_rediarization_passes_device_and_preserves_failure(self):
        with patch.object(app, '_find_audio_file', return_value=self.directory/'audio.wav'), patch.object(app, '_prepare_diarization_audio', return_value='audio.wav'), patch.object(app, '_run_diarization_worker', return_value=self.segs) as worker:
            app._run_rediarization('test', 'test-token', diarization_device='cpu')
            self.assertEqual(worker.call_args.args[-1], 'cpu')
        before = active(self.directory)
        with patch.object(app, '_find_audio_file', return_value=self.directory/'audio.wav'), patch.object(app, '_prepare_diarization_audio', side_effect=RuntimeError('decoder failed')):
            app._run_rediarization('test', 'test-token')
        self.assertEqual(active(self.directory), before)


    def test_rediarization_preserves_manual_text(self):
        response=self.client.patch('/api/jobs/test/segments/1',json={'version':self.version,'text':'Testo corretto'})
        self.assertEqual(response.status_code,200,response.text)
        def worker(job_id,audio,segments,*args):
            self.assertEqual(segments[0]['text'],'Testo corretto')
            return [{**s,'speaker':'Speaker 1'} for s in segments]
        with patch.object(app,'_find_audio_file',return_value=self.directory/'audio.wav'),patch.object(app,'_prepare_diarization_audio',return_value='audio.wav'),patch.object(app,'_run_diarization_worker',side_effect=worker):
            app._run_rediarization('test','token')
        self.assertEqual(active(self.directory)['segments'][0]['text'],'Testo corretto')



class ExecutionTests(unittest.TestCase):
    def test_faster_worker_boundary_and_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); directory=root/'j';directory.mkdir()
            process=unittest.mock.MagicMock()
            process.poll.return_value=0;process.returncode=0
            def launch(command, **kwargs):
                self.assertEqual(command[2], 'backend.asr_worker')
                request=json.loads((directory/'asr_request.json').read_text())
                self.assertEqual(request['beam_size'],5)
                self.assertEqual(request['device'],'cpu')
                self.assertIsNone(request['language'])
                self.assertTrue(request['word_timestamps'])
                atomic_json(directory/'asr_result.json',dict(ok=True,segments=[dict(start=0,end=1,text='ciao')],language='it',metrics={}))
                return process
            job={'metrics':{},'transcription_options':{},'diarization_precision':'words'}
            with patch.object(app,'UPLOAD_DIR',root), patch.object(app,'jobs',{'j':job}), patch.object(app,'detect_device',return_value=('auto','int8')), patch.object(app,'_prepare_diarization_audio',return_value='normalized.wav'), patch.object(app,'_audio_duration',return_value=2), patch.object(app.subprocess,'Popen',side_effect=launch):
                segments,info=app._run_faster_whisper_worker('j','input.wav','tiny',None,app.PERFORMANCE_PROFILES['quality'],lambda *args:None)
                self.assertEqual(info['duration'],2)
                self.assertEqual(segments[0]['end'],1)
                self.assertNotIn('j',app.job_processes)

    def test_cli_effective_flags_and_zero_time(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); directory=root/'j';directory.mkdir()
            fake=unittest.mock.MagicMock()
            fake.poll.return_value=0; fake.returncode=0; fake.stderr=None
            def launch(command, **kwargs):
                atomic_json(directory/'whisper_cpp_result.json', {'transcription':[{'offsets':{'from':0,'to':500},'text':'ciao'}], 'result':{'language':'it'}})
                return fake
            job={'metrics':{},'glossary':'EMV, PSP'}
            with patch.object(app,'UPLOAD_DIR',root), patch.object(app,'jobs',{'j':job}), patch.object(app,'_whisper_cpp_binary',return_value=root/'cli'), patch.object(app,'_whisper_cpp_model_path',return_value=root/'model'), patch.object(app,'_prepare_whisper_cpp_audio',return_value='audio.wav'), patch.object(app,'_audio_duration',return_value=.5), patch.object(app.subprocess,'Popen',side_effect=launch) as popen:
                raw,info=app._run_whisper_cpp('j','input.wav','large-v3-turbo',None,app.PERFORMANCE_PROFILES['quality'],lambda *args:None)
                command=popen.call_args.args[0]
                self.assertEqual(command[command.index('-l')+1],'auto')
                self.assertEqual(command[command.index('-bo')+1],'5')
                self.assertEqual(command[command.index('-dtw')+1],'large.v3.turbo')
                self.assertIn('-nfa',command);self.assertIn('-ojf',command)
                self.assertEqual(raw[0]['start'],0)
                self.assertEqual(raw[0]['end'],.5)

    def test_cache_signature_invalidates_parameters(self):
        from backend.engine import cache_signature
        with tempfile.TemporaryDirectory() as folder:
            audio=Path(folder)/'audio.wav';audio.write_bytes(b'audio')
            first=cache_signature(audio,'community-1','cpu',2)[0]
            self.assertEqual(first,cache_signature(audio,'community-1','cpu',2,1,8)[0])
            self.assertNotEqual(first,cache_signature(audio,'community-1','mps',2)[0])
            self.assertNotEqual(first,cache_signature(audio,'community-1','cpu',3)[0])
            audio.write_bytes(b'new audio')
            self.assertNotEqual(first,cache_signature(audio,'community-1','cpu',2)[0])

    def test_queue_cancellation(self):
        import threading,time
        entered=threading.Event(); release=threading.Event();calls=[]
        @app.heavy_job
        def work(job_id):
            calls.append(job_id);entered.set();release.wait(2)
        jobs={'a':{'segments':[]},'b':{'segments':[]}}
        with patch.object(app,'jobs',jobs):
            a=threading.Thread(target=work,args=('a',));b=threading.Thread(target=work,args=('b',))
            a.start();self.assertTrue(entered.wait(1));b.start()
            jobs['b']['cancel_requested']=True
            b.join(1);release.set();a.join(1)
            self.assertEqual(calls,['a'])
            self.assertEqual(jobs['b']['status'],'canceled')

    def test_engine_import_has_no_app_history(self):
        import subprocess,sys
        result=subprocess.run([sys.executable,'-c',"import sys; import backend.engine; import backend.asr_worker; assert 'backend.main' not in sys.modules"],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

class WorkflowTests(unittest.TestCase):
    def test_upload_propose_apply_restore(self):
        import io,wave
        wav=io.BytesIO()
        with wave.open(wav,'wb') as handle:
            handle.setnchannels(1);handle.setsampwidth(2);handle.setframerate(16000);handle.writeframes(b'\0'*64000)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);calls=[]
            def asr(job_id,audio,model,language,profile,progress):
                calls.append(job_id)
                text='Ciao mondo' if len(calls)==1 else 'Salve mondo'
                seg={'id':1,'start':0.,'end':2.,'text':text,'words':[word(text.split()[0],0,1),word(' mondo',1,2)],'speaker':None}
                return [seg],{'language':'it','duration':2.,'word_timestamps':True,'alignment':'dtw'}
            with patch.object(app,'UPLOAD_DIR',root),patch.object(app,'jobs',{}),patch.object(app,'CONFIG_FILE',root/'config.json'),patch.object(app,'_whisper_cpp_model_path',return_value=root/'model'),patch.object(app,'_run_whisper_cpp',side_effect=asr):
                client=TestClient(app.app)
                response=client.post('/api/transcribe',data={'model_name':'tiny','transcription_backend':'whisper_cpp','diarize':'false','diarization_precision':'words','performance_profile':'quality'},files={'file':('sample.wav',wav.getvalue(),'audio/wav')})
                self.assertEqual(response.status_code,200,response.text)
                jid=response.json()['job_id'];original=client.get(f'/api/jobs/{jid}').json()
                raw=(root/jid/'transcript.json').read_bytes()
                response=client.post(f'/api/jobs/{jid}/proposals',json={'version':original['version'],'start':0,'end':2,'model_name':'tiny'})
                self.assertEqual(response.status_code,200,response.text)
                pid=response.json()['id']
                proposal=client.get(f'/api/jobs/{jid}/proposals/{pid}').json()
                self.assertEqual(proposal['status'],'ready',proposal)
                self.assertEqual(client.get(f'/api/jobs/{jid}').json()['segments'][0]['text'],'Ciao mondo')
                applied=client.post(f'/api/jobs/{jid}/proposals/{pid}/apply',json={'version':original['version']})
                self.assertEqual(applied.status_code,200,applied.text)
                self.assertEqual(''.join(s['text'] for s in applied.json()['segments']),'Salve mondo')
                restored=client.post(f'/api/jobs/{jid}/restore',json={'version':applied.json()['version'],'target_version':original['version']})
                self.assertEqual(restored.status_code,200,restored.text)
                self.assertEqual(restored.json()['segments'][0]['text'],'Ciao mondo')
                self.assertEqual((root/jid/'transcript.json').read_bytes(),raw)
                self.assertFalse(list(root.glob('_work*')))

    def test_reference_scoring_synthetic(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('evaluation',Path(__file__).resolve().parents[1]/'scripts/evaluate_quality.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);manifest={'samples':[{'id':'test'}]};atomic_json(root/'manifest.json',manifest)
            reference={'verified':True,'manifest_sha256':module.digest_file(root/'manifest.json'),'samples':{'test':{'text':'ciao mondo','turns':[[0,2,'A']], 'words':[{'word':'ciao','start':0,'end':1,'speaker':'A'},{'word':'mondo','start':1,'end':2,'speaker':'A'}],'terms':['mondo']}}}
            atomic_json(root/'reference.draft.json',reference);module.freeze(root)
            run=root/'runs'/'case';run.mkdir(parents=True)
            atomic_json(run/'metrics.json',{'case':'case','sample':'test','variant':'test','vad':False,'repeat':1,'seconds':1,'text':'ciao mondo'})
            atomic_json(run/'diarized.json',[{'start':0,'end':2,'text':'ciao mondo','speaker':'Speaker 1'}])
            module.score(root,manifest)
            score=json.loads((root/'scores.json').read_text())[0]
            self.assertEqual(score['wer'],0)
            self.assertEqual(score['speaker_word_errors'],0)
            aggregate=json.loads((root/'aggregate_scores.json').read_text())['test-vad0']
            self.assertEqual(aggregate['wer'],0)
            self.assertEqual(aggregate['der'],0)
            self.assertEqual(aggregate['speaker_reference_words'],2)
            self.assertFalse(json.loads((root/'promotion.json').read_text())['test-vad0']['eligible'])
            reference['samples']['test']['text']='modified';atomic_json(root/'reference.frozen.json',reference)
            with self.assertRaises(RuntimeError):module.score(root,manifest)

if __name__ == '__main__': unittest.main()
