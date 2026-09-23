#!/usr/bin/env python3
"""Offline, reproducible corpus and ASR matrix. Scores require a human-verified reference."""
import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.storage import atomic_json, digest_file
from backend.quality import cpp_words, cpp_time, assign_speakers, diagnostics

SOURCES = {'lesson': ROOT/'uploads/b12cfe14-e01a-41a6-afac-d18c31eb7046/audio.mp3',
           'meeting': ROOT/'uploads/dc48742f-baa9-44d6-b0cb-0ddb737ea9ba/audio.mp3'}


def prepare(out):
    manifest_path = out/'manifest.json'
    if manifest_path.exists(): return json.loads(manifest_path.read_text())
    windows = [('lesson',255,285), ('lesson',380,430), ('meeting',20,65), ('meeting',315,400)]
    rng = random.Random(20260909)
    for length in [60]*6+[30]:
        while True:
            source = rng.choice(['lesson','meeting'])
            start = rng.randint(10, (505 if source=='lesson' else 3070)-length)
            if all(source != key or start+length <= a-5 or start >= b+5 for key,a,b in windows): break
        windows.append((source,start,start+length))
    out.mkdir(parents=True, exist_ok=True)
    records=[]
    for i,(key,start,end) in enumerate(windows):
        sample_id = f'{i+1:02d}-{key}'
        audio=out/(sample_id+'.wav')
        subprocess.run(['ffmpeg','-v','error','-y','-ss',str(start),'-i',str(SOURCES[key]),'-t',str(end-start),'-ar','16000','-ac','1','-c:a','pcm_s16le',str(audio)],check=True)
        records.append({'id':sample_id,'source':key,'start':start,'end':end,'audio':str(audio.resolve()),'sha256':digest_file(audio)})
    manifest={'seed':20260909,'duration_seconds':sum(b-a for _,a,b in windows),'sources':{k:digest_file(v) for k,v in SOURCES.items()},'samples':records}
    atomic_json(manifest_path,manifest)
    atomic_json(out/'reference.draft.json',{'verified':False,'instructions':'Correggere testo, segmenti speaker e tempi sull’audio; poi verified=true e freeze-reference. Non usare output automatico come verità.',
        'manifest_sha256':digest_file(manifest_path),'samples':{r['id']:{'text':'','turns':[],'words':[],'terms':[]} for r in records}})
    return manifest


def run_matrix(out, manifest, repeats, limit):
    cli=ROOT/'whisper.cpp/build/bin/whisper-cli'; models=ROOT/'models_cache/whisper.cpp'
    variants=[('baseline','large-v3-turbo',False),('turbo-words','large-v3-turbo',True),('large-words','large-v3',True)]
    rows=[]
    for sample in manifest['samples'][:limit or None]:
        for name,model,words in variants:
            for vad in [False,True]:
                if not (models/f'ggml-{model}.bin').exists(): raise RuntimeError(f'Modello mancante: {model}')
                for repeat in range(repeats):
                    case=f"{sample['id']}-{name}-vad{int(vad)}-r{repeat+1}"
                    folder=out/'runs'/case;folder.mkdir(parents=True,exist_ok=True)
                    meta_path=folder/'metrics.json'
                    if meta_path.exists():
                        rows.append(json.loads(meta_path.read_text()));continue
                    command=[str(cli),'-m',str(models/f'ggml-{model}.bin'),'-f',sample['audio'],'-l','it','-t','8','-bs','5' if words else '3','-bo','5','-of',str(folder/'asr'),'-ojf' if words else '-oj']
                    if words: command += ['-dtw',model.replace('-','.'),'-nfa']
                    if vad: command += ['--vad','-vm',str(models/'ggml-silero-v6.2.0.bin')]
                    started=time.monotonic()
                    with (folder/'cli.log').open('w') as log:
                        result=subprocess.run(command,stdout=log,stderr=log)
                    if result.returncode: raise RuntimeError(f'CLI fallita: {folder}/cli.log')
                    elapsed=time.monotonic()-started
                    raw=json.loads((folder/'asr.json').read_text())['transcription']
                    segs=[{'id':i+1,'start':cpp_time(s,'from'),'end':cpp_time(s,'to'),'text':s['text'].strip(),'words':cpp_words(s,True) if words else [],'speaker':None} for i,s in enumerate(raw) if s.get('text','').strip()]
                    atomic_json(folder/'segments.json',segs)
                    metrics={'case':case,'sample':sample['id'],'variant':name,'model':model,'vad':vad,'repeat':repeat+1,'seconds':elapsed,'rtf':elapsed/(sample['end']-sample['start']),
                        'model_cold_each_run':True,'filesystem_cache':'uncontrolled-first' if repeat==0 else 'warm-repeat','command':command,'text':' '.join(s['text'] for s in segs),'issues':len(diagnostics(segs))}
                    atomic_json(meta_path,metrics);rows.append(metrics)
                    atomic_json(out/'matrix.json',rows)
                    print(case,round(elapsed,2),'s',flush=True)
    atomic_json(out/'matrix.json',rows)


def diarize_matrix(out,manifest):
    # Speaker count belongs to full recording, not arbitrary short windows.
    # Infer full sources once, then crop fixed turns for every ASR variant.
    from backend.engine import run
    token=json.loads((ROOT/'config.json').read_text()).get('hf_token','')
    source_turns={}
    for name,source in SOURCES.items():
        folder=out/'sources'/name;folder.mkdir(parents=True,exist_ok=True)
        turnfile=folder/'turns.json'
        if not turnfile.exists():
            audio=folder/'normalized.wav'
            if not audio.exists():
                subprocess.run(['ffmpeg','-v','error','-y','-i',str(source),'-ac','1','-ar','16000','-c:a','pcm_s16le',str(audio)],check=True)
            empty=folder/'segments.json';atomic_json(empty,[])
            output=run({'audio_path':str(audio),'segments_path':str(empty),'diarization_model':'community-1','diarization_device':'auto','expected_speakers':2 if name=='lesson' else 4,'diarization_precision':'words'},token)
            atomic_json(turnfile,output['turns'])
            atomic_json(folder/'metrics.json', {k:v for k,v in output.items() if k != 'turns' and k != 'segments'})
        source_turns[name]=json.loads(turnfile.read_text())
        print('full source diarization',name,flush=True)
    for sample in manifest['samples']:
        folder=out/'turns'/sample['id'];folder.mkdir(parents=True,exist_ok=True)
        original=source_turns[sample['source']]
        def crop(turns):
            if turns is None: return None
            return [[max(a,sample['start'])-sample['start'],min(b,sample['end'])-sample['start'],sp]
                    for a,b,sp in turns if b>sample['start'] and a<sample['end']]
        turns={'standard':crop(original['standard']),'exclusive':crop(original['exclusive'])}
        atomic_json(folder/'turns.json',turns)
        for path in (out/'runs').glob(sample['id']+'-*/segments.json'):
            raw=json.loads(path.read_text());baseline='-baseline-' in str(path)
            if baseline:
                result=[]
                for seg in raw:
                    best='SPEAKER_00';amount=0
                    for a,b,sp in turns['standard']:
                        overlap=max(0,min(seg['end'],b)-max(seg['start'],a))
                        if overlap>amount: best,amount=sp,overlap
                    result.append({**seg,'speaker':best})
            else:
                result=assign_speakers(raw,turns['standard'],turns['exclusive'],'words')
            atomic_json(path.parent/'diarized.json',result)
        print('diarized',sample['id'],flush=True)


def normalized(text):
    return re.findall(r'\w+',text.lower())


def edit_distance(a,b):
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        nxt=[i]
        for j,y in enumerate(b,1): nxt.append(min(nxt[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=nxt
    return row[-1]


def score(out,manifest):
    reference_path=out/'reference.frozen.json'
    if not reference_path.exists(): raise RuntimeError('Riferimento umano non congelato: nessuna misura WER/DER disponibile')
    signature=json.loads((out/'reference.frozen.sha256.json').read_text())
    if signature['sha256'] != digest_file(reference_path):
        raise RuntimeError('Riferimento congelato modificato: scoring interrotto')
    ref=json.loads(reference_path.read_text())
    if not ref.get('verified') or ref['manifest_sha256']!=digest_file(out/'manifest.json'):
        raise RuntimeError('Riferimento non verificato o relativo a diverso campione')
    from pyannote.core import Annotation,Segment,Timeline
    from pyannote.metrics.diarization import DiarizationErrorRate
    def annotation(turns):
        ann=Annotation()
        for i,(a,b,sp) in enumerate(turns):
            if sp is not None and b>a: ann[Segment(a,b),i]=sp
        return ann
    scores=[]
    for path in (out/'runs').glob('*/metrics.json'):
        metrics=json.loads(path.read_text());target=ref['samples'][metrics['sample']]
        hypothesis=json.loads((path.parent/'diarized.json').read_text())
        ref_ann=annotation(target['turns'])
        hyp_ann=annotation([(s['start'],s['end'],s['speaker']) for s in hypothesis])
        der=DiarizationErrorRate(collar=.25,skip_overlap=False)
        sample=next(x for x in manifest['samples'] if x['id']==metrics['sample'])
        duration=sample.get('end', max([t[1] for t in target['turns']], default=0)) - sample.get('start', 0)
        uem=Timeline([Segment(0,duration)])
        detail=der(ref_ann,hyp_ann,uem=uem,detailed=True)
        mapping=der.optimal_mapping(ref_ann,hyp_ann,uem=uem)
        # Attribution scored at reference word midpoints, independent of ASR wording.
        attributed=0;errors=0
        for w in target.get('words',[]):
            if w.get('speaker') is None: continue
            midpoint=(w['start']+w['end'])/2
            labels={mapping.get(s['speaker']) for s in hypothesis if s['start']<=midpoint<s['end']}
            errors+=int(labels!={w['speaker']});attributed+=1
        a,b=normalized(target['text']),normalized(metrics['text'])
        digits=lambda text:re.findall(r'\b\d+(?:[.,]\d+)?\b',text)
        scores.append({**{k:metrics[k] for k in ('case','sample','variant','vad','repeat','seconds')},'word_errors':edit_distance(a,b),'reference_words':len(a),'wer':edit_distance(a,b)/max(1,len(a)),
            'der':detail,'speaker_word_errors':errors,'speaker_reference_words':attributed,
            'digits_match':digits(target['text'])==digits(metrics['text']),
            'negation_count_match':a.count('non')==b.count('non'),
            'term_counts':{term:{'reference':target['text'].lower().count(term.lower()),'hypothesis':metrics['text'].lower().count(term.lower())} for term in target.get('terms',[])}})
    atomic_json(out/'scores.json',scores)
    from pyannote.metrics.identification import IER_TOTAL, IER_CONFUSION, IER_FALSE_ALARM, IER_MISS
    grouped={}
    for row in scores:
        key=f"{row['variant']}-vad{int(row['vad'])}"
        grouped.setdefault(key,[]).append(row)
    aggregates={}
    for key,rows in grouped.items():
        nwords=sum(r['reference_words'] for r in rows)
        nsp=sum(r['speaker_reference_words'] for r in rows)
        duration=sum(r['der'][IER_TOTAL] for r in rows)
        aggregates[key]={'wer':sum(r['word_errors'] for r in rows)/max(1,nwords),
            'speaker_word_error_rate':sum(r['speaker_word_errors'] for r in rows)/max(1,nsp),
            'der':sum(r['der'][IER_CONFUSION]+r['der'][IER_FALSE_ALARM]+r['der'][IER_MISS] for r in rows)/max(1,duration),
            'runs':len(rows),'reference_words':nwords,'speaker_reference_words':nsp}
    atomic_json(out/'aggregate_scores.json',aggregates)
    review_path=out/'content_review.json'
    if not review_path.exists():
        atomic_json(review_path,{'reference_sha256':digest_file(reference_path),
            'cases':{r['case']:{'digits_negations_verified':False} for r in scores}})
    review=json.loads(review_path.read_text())
    baseline=aggregates.get('baseline-vad0')
    promotion={}
    for key,aggregate in aggregates.items():
        reviewed=review.get('reference_sha256')==digest_file(reference_path) and all(review.get('cases',{}).get(r['case'],{}).get('digits_negations_verified') for r in grouped[key])
        promotion[key]={'eligible':bool(baseline and aggregate['runs']==baseline['runs'] and aggregate['wer']<=baseline['wer'] and aggregate['speaker_reference_words']>0 and aggregate['speaker_word_error_rate']<baseline['speaker_word_error_rate'] and reviewed),
                       'content_review_complete':reviewed,'automatic_config_change':False}
    atomic_json(out/'promotion.json',promotion)
    print('Scores written. Digit/negation contextual review in content_review.json is required; configuration unchanged.')


def import_reference(out,sample,annotation):
    draft=json.loads((out/'reference.draft.json').read_text())
    if sample not in draft['samples']: raise RuntimeError('Campione non valido')
    incoming=json.loads(Path(annotation).read_text())
    if not incoming.get('verified'): raise RuntimeError('Annotazione non verificata: controllare audio prima di impostare verified=true')
    draft['samples'][sample]={k:incoming.get(k, []) for k in ('text','turns','words','terms','verified')}
    draft['verified']=all(v.get('verified',False) for v in draft['samples'].values())
    atomic_json(out/'reference.draft.json',draft)


def freeze(out):
    source=out/'reference.draft.json';data=json.loads(source.read_text())
    if not data.get('verified') or not all(v.get('text') and v.get('turns') and v.get('words') for v in data['samples'].values()):
        raise RuntimeError('Completare verifica umana, testo, turni e parole prima di congelare')
    if (out/'reference.frozen.json').exists(): raise RuntimeError('Riferimento già congelato; non sovrascritto')
    atomic_json(out/'reference.frozen.json',data)
    atomic_json(out/'reference.frozen.sha256.json',{'sha256':digest_file(out/'reference.frozen.json')})


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','run','diarize','score','freeze-reference','import-reference'])
    parser.add_argument('--out',type=Path,default=ROOT/'reports/evaluation-v1')
    parser.add_argument('--sample')
    parser.add_argument('--annotation',type=Path)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--limit',type=int,default=0)
    args=parser.parse_args()
    if not 1 <= args.repeats <= 10 or not 0 <= args.limit <= 11:
        parser.error('repeats: 1–10; limit: 0–11')
    if args.action == 'import-reference' and (not args.sample or not args.annotation):
        parser.error('import-reference richiede --sample e --annotation')
    manifest=prepare(args.out)
    if args.action=='run': run_matrix(args.out,manifest,args.repeats,args.limit)
    elif args.action=='diarize': diarize_matrix(args.out,manifest)
    elif args.action=='score': score(args.out,manifest)
    elif args.action=='freeze-reference': freeze(args.out)
    elif args.action=='import-reference': import_reference(args.out,args.sample,args.annotation)
