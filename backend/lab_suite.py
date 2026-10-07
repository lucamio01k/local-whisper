"""Deterministic selections and immutable preparation snapshots."""
import hashlib
import json
import os
from pathlib import Path
from backend.lab_dataset import load_dataset
from backend.lab_sources import canonical_clip
from backend.storage import atomic_json, digest_file

RESOURCE_PROFILES = {'light': {'threads':2, 'rss_gib':8}, 'balanced': {'threads':4, 'rss_gib':10}, 'heavy': {'threads':8, 'rss_gib':10}}
SELECTIONS = {'small':120., 'medium':600., 'complete':None}


def resources(name):
    if name not in RESOURCE_PROFILES: raise ValueError('Unknown resource profile')
    return {**RESOURCE_PROFILES[name], 'threads':min(RESOURCE_PROFILES[name]['threads'], os.cpu_count() or 1), 'name':name}


def suite_path(root, name):
    if not isinstance(name,str) or not name or len(name)>80 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in name):
        raise ValueError('Invalid suite ID')
    return root/'suites'/(name+'.json')


def select(root, body):
    selection = body.get('selection','complete' if body.get('dataset_id') else 'small')
    if selection not in SELECTIONS: raise ValueError('Unknown selection')
    seed = body.get('seed',42)
    if type(seed) is not int: raise ValueError('Selection seed must be integer')
    suite = json.loads(suite_path(root,body['suite_id']).read_text()) if body.get('suite_id') else None
    ids = suite['dataset_ids'] if suite else [body.get('dataset_id')]
    datasets = [load_dataset(root,id)[0] for id in ids]
    if body.get('split'):
        if body['split'] not in ('calibration','evaluation'): raise ValueError('Invalid split')
        datasets = [d for d in datasets if d.get('split') == body['split']]
    if not datasets: raise ValueError('Selection empty')
    if body.get('interval'):
        if len(datasets)!=1: raise ValueError('Custom interval requires single audio')
        a,b = map(float,body['interval'])
        d,audio,ref = load_dataset(root,datasets[0]['id'])
        if not 0<=a<b<=d['duration']: raise ValueError('Interval outside audio')
        p = d.get('provenance') or dict(title=d['title'], scenario=d['scenario'], corpus=d['corpus'],language='unknown',split='user',group=d['id'])
        return [canonical_clip(root,audio,{**ref,'duration':d['duration']},p,a,b)],suite
    if SELECTIONS[selection] is None: return datasets,suite
    target = SELECTIONS[selection]
    languages = sorted({d.get('language','unknown') for d in datasets})
    chosen=[]
    for language in languages:
        candidates=[d for d in datasets if d.get('language','unknown')==language]
        strata={}
        for d in candidates: strata.setdefault((d.get('scenario'),d.get('split')),[]).append(d)
        # Equal scenario/split quotas, independent of corpus archive order.
        for stratum, items in sorted(strata.items()):
            remaining = target/len(languages)/len(strata)
            items.sort(key=lambda d:hashlib.sha256(f"{seed}:{d['id']}".encode()).hexdigest())
            for d in items:
                if remaining <= .1: break
                if d['duration']<=remaining+.1:
                    chosen.append(d); remaining-=d['duration']; continue
                if d['gold'].get('words') or not d['gold'].get('text'):
                    _,audio,ref=load_dataset(root,d['id'])
                    if ref.get('verified') and (('words' in ref and not ref['words']) or (not ref.get('turns') and not ref.get('text'))):
                        continue
                    p = d.get('provenance') or dict(title=d['title'],scenario=d['scenario'],corpus=d['corpus'],language=language,split='user',group=d['id'])
                    words=[w for w in ref.get('words',[]) if 'start' in w and 'end' in w]
                    # Choose a speech-bearing window; silent samples waste small quotas.
                    starts=sorted({max(0.,min(w['start']-.2,d['duration']-remaining)) for w in words})
                    start=max(starts,key=lambda a:sum(w['start']>=a and w['end']<=a+remaining for w in words)) if starts else 0.
                    chosen.append(canonical_clip(root,audio,ref,p,start,start+remaining)); remaining=0.
                # Untimed utterances stay whole. Pick smaller ones instead.
    if not chosen: raise ValueError('No whole utterance fits selection; use complete/custom timed gold')
    return chosen,suite


def snapshot_key(dataset, pipeline, parameters, resource, implementation):
    prep = {k:parameters[k] for k in ('ambiguity_margin','audio_window','min_audio')}
    value = {'audio':dataset['audio_sha256'],'gold':dataset['reference_sha256'],'pipeline':pipeline,
             'detector':prep,'threads':resource['threads'],'implementation':implementation}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def seal_snapshot(directory, result, key):
    path = directory/'snapshot.json'
    if path.exists(): raise ValueError('Snapshot immutable; refusing overwrite')
    atomic_json(path, {'key':key,'result':result})
    atomic_json(directory/'seal.json', {'sha256':digest_file(path)})
    return path


def read_snapshot(path, key=None):
    path=Path(path)
    seal=json.loads((path.parent/'seal.json').read_text())
    if digest_file(path)!=seal['sha256']: raise ValueError('Snapshot modified; new preparation required')
    value=json.loads(path.read_text())
    if key and value['key']!=key: raise ValueError('Snapshot configuration mismatch')
    return value['result']
