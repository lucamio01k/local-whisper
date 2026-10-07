import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from backend.storage import atomic_json, digest_file
from backend.lab_download import DiskBudget, download
from backend.lab_sources import ami_reference, elan_reference, crop_gold, canonical_clip, prepare_public, exclusive, heavy_gate, vox_metadata, vox_utterances
from backend.lab_suite import resources, snapshot_key, seal_snapshot, read_snapshot, select
from backend.lab_metrics import aggregate, evaluate
from backend.lab_worker import replay
from backend.refinement import RefinementConfig
from dataclasses import asdict


class Reply:
    def __init__(self, chunks, status=200, length=6):
        self.chunks=iter(chunks); self.status=status
        self.headers={'Content-Length':str(length),'ETag':'"v1"'}
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self,n):
        value=next(self.chunks,b'')
        if isinstance(value,Exception): raise value
        return value


class SuiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()

    def test_resume_transfer_hash_and_no_second_transfer(self):
        dest=self.root/'data.bin'; budget=DiskBudget(self.root,5000)
        first=Reply([b'abc',OSError('interrupted')])
        with patch('urllib.request.urlopen',return_value=first):
            with self.assertRaises(OSError): download('https://example.test/audio',dest,budget)
        self.assertEqual(dest.with_suffix('.bin.part').read_bytes(),b'abc')
        second=Reply([b'def'],206,3); second.headers['Content-Range']='bytes 3-5/6'
        with patch('urllib.request.urlopen',return_value=second) as get:
            download('https://example.test/audio',dest,budget)
            self.assertEqual(get.call_args.args[0].get_header('Range'),'bytes=3-')
        self.assertEqual(dest.read_bytes(),b'abcdef')
        with patch('urllib.request.urlopen') as get:
            download('https://example.test/audio',dest,budget); get.assert_not_called()
        self.assertFalse(dest.with_suffix('.bin.part').exists())
        dest.write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'mismatch'): download('https://example.test/audio',dest,budget)

    def test_budget_preflight_and_live_transfer_bounds(self):
        (self.root/'existing').write_bytes(b'x'*10)
        budget=DiskBudget(self.root,15)
        with self.assertRaises(ValueError): budget.reserve(6)
        with patch('urllib.request.urlopen',return_value=Reply([b'abcdef'])):
            with self.assertRaises(ValueError): download('https://example.test/a',self.root/'a',budget)
        self.assertFalse((self.root/'a.part').exists())
        response=Reply([b'12345',b'67890']); response.headers.pop('Content-Length')
        with patch('urllib.request.urlopen',return_value=response):
            with self.assertRaises(ValueError): download('https://example.test/a',self.root/'a',DiskBudget(self.root,2000),max_bytes=8)
        self.assertEqual((self.root/'a.part').read_bytes(),b'12345')

    def test_manual_ami_words_and_turns_with_overlap(self):
        z=self.root/'ami.zip'
        with zipfile.ZipFile(z,'w') as archive:
            for speaker in ['A','B']:
                archive.writestr(f'words/M.{speaker}.words.xml','<root><w starttime="1" endtime="2">hello</w><w starttime="2" endtime="2" punc="true">.</w></root>')
                archive.writestr(f'segments/M.{speaker}.segments.xml','<root><segment transcriber_start="0.8" transcriber_end="2.1"/></root>')
        ref=ami_reference(z,'M')
        self.assertTrue(ref['verified']); self.assertEqual(len(ref['words']),2)
        self.assertEqual(ref['turns'],[[.8,2.1,'M:A'],[.8,2.1,'M:B']])

    def test_voxforge_prompt_audio_pairing_and_documented_microphone(self):
        import tarfile
        archive=self.root/'vox.tgz'
        with tarfile.open(archive,'w:gz') as tar:
            for name,data in [('speaker/etc/PROMPTS',b'speaker/wav/one ciao mondo\n'),('speaker/etc/README',b'Audio Microphone: USB Headset mic\n'),('speaker/wav/one.wav',b'audio')]:
                member=tarfile.TarInfo(name); member.size=len(data); tar.addfile(member,io.BytesIO(data))
        with tarfile.open(archive) as tar:
            rows=list(vox_utterances(tar)); self.assertEqual(rows[0][1:],('speaker/wav/one','ciao mondo'))
            self.assertEqual(vox_metadata(tar)[1],'USB Headset mic')
        with tarfile.open(archive,'w:gz') as tar:
            data=b'missing text'; member=tarfile.TarInfo('speaker/etc/PROMPTS');member.size=len(data);tar.addfile(member,io.BytesIO(data))
        with tarfile.open(archive) as tar:
            with self.assertRaisesRegex(ValueError,'mismatch'): list(vox_utterances(tar))

    def test_elan_reference_inheritance_overlap_and_anonymization(self):
        path=self.root/'x.eaf'
        path.write_text('''<ANNOTATION_DOCUMENT><TIME_ORDER><TIME_SLOT TIME_SLOT_ID="t0" TIME_VALUE="0"/><TIME_SLOT TIME_SLOT_ID="t1" TIME_VALUE="1000"/><TIME_SLOT TIME_SLOT_ID="t2" TIME_VALUE="2000"/><TIME_SLOT TIME_SLOT_ID="t3" TIME_VALUE="3000"/></TIME_ORDER>
        <TIER TIER_ID="A" PARTICIPANT="Alice"><ANNOTATION><ALIGNABLE_ANNOTATION ANNOTATION_ID="a" TIME_SLOT_REF1="t0" TIME_SLOT_REF2="t1"><ANNOTATION_VALUE>ciao</ANNOTATION_VALUE></ALIGNABLE_ANNOTATION></ANNOTATION><ANNOTATION><ALIGNABLE_ANNOTATION ANNOTATION_ID="b" TIME_SLOT_REF1="t1" TIME_SLOT_REF2="t3"><ANNOTATION_VALUE>[nome] anonimizzato</ANNOTATION_VALUE></ALIGNABLE_ANNOTATION></ANNOTATION></TIER>
        <TIER TIER_ID="B" PARTICIPANT="Bob"><ANNOTATION><REF_ANNOTATION ANNOTATION_ID="c" ANNOTATION_REF="a"><ANNOTATION_VALUE>hello</ANNOTATION_VALUE></REF_ANNOTATION></ANNOTATION></TIER></ANNOTATION_DOCUMENT>''')
        ref=elan_reference(path,3,['A','B'])
        self.assertEqual(ref['uem'],[[0,1.]])
        self.assertEqual(ref['turns'],[[0.,1.,'Alice'],[0.,1.,'Bob']])
        self.assertTrue(all('start' not in w for w in ref['words']))
        with self.assertRaises(ValueError): elan_reference(path,3,['missing'])

    def test_crop_keeps_uem_and_excludes_partial_words(self):
        ref={'verified':True,'words':[{'word':'a','start':0,'end':1,'speaker':'A'},{'word':'b','start':1,'end':2,'speaker':'A'}],
             'turns':[[0,2,'A']],'text':'a b','uem':[[0,.8],[1.2,2]]}
        cropped=crop_gold(ref,.5,2)
        self.assertEqual(cropped['text'],'b')
        self.assertEqual(cropped['words'][0]['start'],.5)
        self.assertEqual(cropped['uem'],[[.7,1.5]])
        self.assertEqual(ref['words'][0]['start'],0)

    def test_snapshot_threshold_reuse_detector_invalidation_and_tamper(self):
        d={'audio_sha256':'audio','reference_sha256':'gold'}; params=asdict(RefinementConfig())
        key=snapshot_key(d,{'model':'a'},params,resources('light'),{'v':1})
        for name in ['context_turns','change_confidence','decision_margin','semantic_confidence','acoustic_confidence']:
            changed={**params,name:0}
            self.assertEqual(key,snapshot_key(d,{'model':'a'},changed,resources('light'),{'v':1}))
        for name in ['ambiguity_margin','audio_window','min_audio']:
            self.assertNotEqual(key,snapshot_key(d,{'model':'a'},{**params,name:0},resources('light'),{'v':1}))
        path=seal_snapshot(self.root,{'baseline_segments':[]},key)
        self.assertEqual(read_snapshot(path,key),{'baseline_segments':[]})
        with self.assertRaises(ValueError): seal_snapshot(self.root,{},key)
        with self.assertRaises(ValueError): read_snapshot(path,'wrong')
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'modified'): read_snapshot(path,key)

    def test_canonical_degradation_selection_repeatability_no_duplicates(self):
        import wave
        audio=self.root/'source.wav'
        with wave.open(str(audio),'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\0\0'*16000*40)
        ids=[]
        for language in ['it','en']:
            for split in ['evaluation','calibration']:
                p=dict(title=language+split,scenario=language+' meeting',corpus='fixture',language=language,split=split,group=split)
                ref={'verified':True,'words':[{'word':'hello','start':24.,'end':25.,'speaker':'A'}],'turns':[[24.,25.,'A']],'text':'hello'}
                first=canonical_clip(self.root,audio,ref,p)
                again=canonical_clip(self.root,audio,ref,p)
                self.assertEqual(first['id'],again['id']); ids.append(first['id'])
        atomic_json(self.root/'suites'/'fixture.json',{'id':'fixture','dataset_ids':ids})
        body={'suite_id':'fixture','selection':'small'}
        one,_=select(self.root,body); two,_=select(self.root,body)
        self.assertEqual([d['id'] for d in one],[d['id'] for d in two])
        self.assertAlmostEqual(sum(d['duration'] for d in one),120)
        self.assertEqual(len(list((self.root/'datasets').glob('*/dataset.json'))),8)
        filtered,_=select(self.root,{**body,'split':'evaluation'})
        self.assertTrue(all(d['split']=='evaluation' for d in filtered))
        p=dict(title='noise',scenario='IT controlled noise',corpus='fixture',language='it',split='evaluation',group='A')
        recipe={'kind':'noise','seed':42}
        degraded=canonical_clip(self.root,audio,{'verified':False},p,0,1,recipe)
        self.assertEqual(degraded['audio_sha256'],canonical_clip(self.root,audio,{'verified':False},p,0,1,recipe)['audio_sha256'])
        self.assertNotEqual(degraded['audio_sha256'],canonical_clip(self.root,audio,{'verified':False},p,0,1,{'kind':'noise','seed':43})['audio_sha256'])

    def test_micro_aggregation_absent_gold_never_zero(self):
        def row(words,errors):
            return {'metrics':{'wer':errors/words if words else None,'reference_words':words,'word_errors':errors,'wder':None},
                    'performance':{'total_seconds':1,'refiner_seconds':0,'peak_ram_bytes':1}}
        result=aggregate([row(100,10),row(10,10),row(0,0)])
        self.assertAlmostEqual(result['wer'],20/110); self.assertIsNone(result['wder']); self.assertIsNone(result['der'])
        self.assertEqual(result['coverage']['wer'],2)

    def test_frozen_word_mapping_cannot_hide_new_errors_by_global_swap(self):
        gold={'verified':True,'text':'one two','turns':[[0,1,'G1'],[1,2,'G2']],
              'words':[{'word':'one','speaker':'G1'},{'word':'two','speaker':'G2'}]}
        swapped=[{'id':1,'start':0.,'end':1.,'text':'one','speaker':'B'},
                 {'id':2,'start':1.,'end':2.,'text':'two','speaker':'A'}]
        freely_mapped=evaluate(gold,swapped,2)
        frozen=evaluate(gold,swapped,2,fixed_mapping={'A':'G1','B':'G2'})
        self.assertEqual(freely_mapped['wder'],0); self.assertEqual(frozen['wder'],1)
        self.assertEqual(frozen['der'],0); self.assertEqual(frozen['wer'],0)

    def test_partial_uem_filters_gold_and_hypothesis_together(self):
        words=[{'word':'one','start':0.,'end':.9,'speaker':'A'},{'word':'two','start':1.1,'end':1.9,'speaker':'B'}]
        gold={'verified':True,'words':words,'text':'one two','uem':[[1.,2.]],'turns':[[0,.9,'A'],[1.1,1.9,'B']]}
        hyp=[{'id':i,'start':w['start'],'end':w['end'],'text':w['word'],'speaker':w['speaker'],'words':[w]} for i,w in enumerate(words)]
        scores=evaluate(gold,hyp,2)
        self.assertEqual(scores['reference_words'],1);self.assertEqual(scores['wer'],0);self.assertEqual(scores['wder'],0)

    def test_process_gate_blocks_and_queued_cancellation_releases(self):
        import threading
        import time
        events=[]; job={}
        def worker():
            with heavy_gate(self.root,job,'normal') as acquired: events.append(acquired)
        with exclusive(self.root,'heavy'):
            thread=threading.Thread(target=worker); thread.start()
            time.sleep(.05); self.assertFalse(events)
            job['cancel_requested']=True
            thread.join(2); self.assertFalse(thread.is_alive()); self.assertEqual(events,[False])
        with heavy_gate(self.root,{},'normal') as acquired: self.assertTrue(acquired)

    def test_replay_semantic_context_never_uses_acoustic_changes(self):
        baseline=[{'id':1,'start':0.,'end':2.,'text':'hello','speaker':None}]
        obs={'segment_id':1,'evidence':{'acoustic':{'speaker':'Speaker 2','confidence':.99,'scores':{'Speaker 2':.99,'Speaker 1':.01},'refiner':'pyannote_acoustic','reason':'similarity','metadata':{}}}}
        source={'baseline_segments':baseline,'turns':{'standard':[[0,1,'A'],[1,2,'B']],'exclusive':None},'metrics':{},'pipeline_metrics':{},
                'performance':{'total_seconds':10,'peak_ram_bytes':100},'acoustic_observations':{'status':'done','decisions':[obs]}}
        spec={'snapshot':'x','snapshot_key':'k','variant':'acoustic_laya','parameters':asdict(RefinementConfig())}
        with patch('backend.lab_suite.read_snapshot',return_value=source),patch('backend.lab_worker.semantic_pass',return_value=({'segments':baseline,'decisions':[]},None)) as semantic,patch('backend.lab_metrics.evaluate',return_value={}):
            result=replay(spec,{'duration':2},{},MagicMock())
        self.assertIsNone(semantic.call_args.args[0][0]['speaker']); self.assertEqual(result['segments'][0]['speaker'],'Speaker 2')
        self.assertIsNone(baseline[0]['speaker']); self.assertGreaterEqual(result['performance']['total_seconds'],10)


if __name__=='__main__': unittest.main()
