"""Bounded public corpus preparation and explicit annotation adapters."""
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from pathlib import Path
from backend.lab_dataset import audio_duration, finalize, load_dataset
from backend.lab_download import DiskBudget, download
from backend.storage import atomic_json, digest_file

AMI = 'https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/'
VOX = 'https://repository.voxforge1.org/downloads/it/Trunk/Audio/Main/16kHz_16bit/'
SOURCES = [
    {'id': 'ami', 'language': 'en', 'status': 'public', 'license': 'CC BY 4.0', 'url': AMI},
    {'id': 'voxforge', 'language': 'it', 'status': 'public', 'license': 'GPL corpus', 'url': VOX},
    {'id': 'tigr', 'language': 'it', 'status': 'authentication_required', 'license': 'Restricted research contract',
     'url': 'https://www.swissubase.ch/en/catalogue/studies/20902/21540/datasets/2741/4492/files'}]


@contextmanager
def exclusive(root, name, blocking=True):
    import fcntl
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    with (root/('.'+name+'.lock')).open('a') as handle:
        try: fcntl.flock(handle,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc: raise RuntimeError(name+' already running') from exc
        try: yield
        finally: fcntl.flock(handle,fcntl.LOCK_UN)


@contextmanager
def heavy_gate(root, job, job_id):
    """Shared with ordinary application FIFO; isolated child workers inherit ownership."""
    import fcntl
    import time
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    with (root/'.heavy.lock').open('a') as handle:
        while True:
            if job_id.startswith('lab-'):
                record=root/'runs'/job_id[4:]/'record.json'
                if record.exists() and json.loads(record.read_text()).get('cancel_requested'): job['cancel_requested']=True
            if job.get('cancel_requested'):
                yield False
                return
            try:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError: time.sleep(.2)
        try: yield True
        finally: fcntl.flock(handle,fcntl.LOCK_UN)


def complement(excluded, duration):
    regions, cursor = [], 0.
    for a, b in sorted(excluded):
        a, b = max(0., a), min(duration, b)
        if a > cursor: regions.append([cursor, a])
        cursor = max(cursor, b)
    if cursor < duration: regions.append([cursor, duration])
    return regions


def ami_reference(archive, meeting):
    words, turns = [], []
    with zipfile.ZipFile(archive) as z:
        paths = sorted(n for n in z.namelist() if n.startswith('words/'+meeting+'.') and n.endswith('.words.xml'))
        for name in paths:
            if z.getinfo(name).file_size>5_000_000: raise ValueError('AMI annotation too large')
            speaker = meeting+':'+name.split('.')[-3]
            for e in ET.fromstring(z.read(name)).iter():
                if e.tag.split('}')[-1] != 'w' or e.get('punc') == 'true': continue
                a, b = float(e.get('starttime', 0)), float(e.get('endtime', 0))
                if e.text and b > a: words.append({'word': e.text, 'start': a, 'end': b, 'speaker': speaker})
            name = name.replace('words/', 'segments/').replace('.words.xml', '.segments.xml')
            if z.getinfo(name).file_size>5_000_000: raise ValueError('AMI annotation too large')
            for e in ET.fromstring(z.read(name)).iter('segment'):
                a, b = float(e.get('transcriber_start')), float(e.get('transcriber_end'))
                if b > a: turns.append([a, b, speaker])
    if not words or not turns: raise ValueError('AMI manual words/segments missing')
    words.sort(key=lambda w: (w['start'], w['speaker']))
    return {'verified': True, 'words': words, 'turns': sorted(turns), 'text': ' '.join(w['word'] for w in words),
            'timing_provenance': 'AMI manual v1.6.2 word times and transcriber segment intervals',
            'turn_reference': 'Manual transcription segments; includes intra-segment pauses, not official SAD scoring'}


def elan_reference(path, duration, tiers):
    """Explicit transcript tiers only; REF annotations inherit parent intervals."""
    root = ET.parse(path).getroot()
    slots = {s.get('TIME_SLOT_ID'): float(s.get('TIME_VALUE'))/1000 for s in root.findall('.//TIME_SLOT') if s.get('TIME_VALUE')}
    annotations = {}
    for e in root.findall('.//ALIGNABLE_ANNOTATION')+root.findall('.//REF_ANNOTATION'):
        annotations[e.get('ANNOTATION_ID')] = e
    def interval(e, seen=None):
        seen = set() if seen is None else seen
        key = e.get('ANNOTATION_ID')
        if key in seen: raise ValueError('ELAN cyclic reference')
        seen.add(key)
        if e.tag == 'REF_ANNOTATION': return interval(annotations[e.get('ANNOTATION_REF')], seen)
        return slots[e.get('TIME_SLOT_REF1')], slots[e.get('TIME_SLOT_REF2')]
    selected, excluded, found = [], [], set()
    for tier in root.findall('TIER'):
        if tier.get('TIER_ID') not in tiers: continue
        found.add(tier.get('TIER_ID'))
        speaker = tier.get('PARTICIPANT') or tier.get('TIER_ID')
        for node in tier.findall('ANNOTATION'):
            e = list(node)[0]; a, b = interval(e)
            text = ''.join(e.find('ANNOTATION_VALUE').itertext()).strip()
            if not 0 <= a < b <= duration+.05: raise ValueError('ELAN interval outside selected audio')
            if not text or re.search(r'anonim|anonym|inaudib|incomprens|\bxxx\b|\*{2,}|\[nome\]|\[name\]', text, re.I):
                excluded.append([a, b]); continue
            selected.append({'start': a, 'end': b, 'speaker': speaker, 'text': text})
    if found != set(tiers) or not selected: raise ValueError('ELAN transcription tiers missing/empty; select tier IDs explicitly')
    selected.sort(key=lambda s: (s['start'], s['speaker']))
    # Entire overlapping utterance excluded if reference obscured anywhere in it.
    usable = [s for s in selected if not any(s['start'] < b and s['end'] > a for a, b in excluded)]
    excluded += [[s['start'], s['end']] for s in selected if s not in usable]
    return {'verified': True, 'segments': usable, 'turns': [[s['start'], s['end'], s['speaker']] for s in usable],
            'text': ' '.join(s['text'] for s in usable), 'words': [{'word': w, 'speaker': s['speaker']} for s in usable for w in s['text'].split()],
            'uem': complement(excluded, duration), 'excluded': excluded,
            'timing_provenance': 'ELAN utterance intervals; no word timestamps', 'tiers': tiers}


def crop_gold(reference, start, end):
    from scripts.prepare_lab_subset import crop_reference
    result = crop_reference(reference, start, end)
    if 'words' in reference and any('start' in w for w in reference['words']):
        # Never score clipped boundary words: exclude their original intervals.
        crossing = [[max(start, w['start'])-start, min(end, w['end'])-start] for w in reference['words']
                    if w['end'] > start and w['start'] < end and not (w['start'] >= start and w['end'] <= end)]
        result['uem'] = intersect_uem(result.get('uem', [[0, end-start]]), complement(crossing, end-start))
    return result


def intersect_uem(left, right):
    return [[max(a,c), min(b,d)] for a,b in left for c,d in right if min(b,d) > max(a,c)]


def vox_metadata(tar):
    member=next((m for m in tar.getmembers() if m.isfile() and m.name.endswith('/etc/README')),None)
    if member is None: return {},'undocumented'
    if member.size>100_000: raise ValueError('VoxForge README too large')
    text=tar.extractfile(member).read().decode('utf-8',errors='replace')
    fields={key.strip():value.strip() for key,value in re.findall(r'^([^:\n]+):\s*([^\n]*)',text,re.M)
            if any(term in key.lower() for term in ('microphone','audio','hardware','environment','license'))}
    microphone=next((v for k,v in fields.items() if 'microphone' in k.lower() and v),'undocumented')
    return fields,microphone


def vox_utterances(tar):
    members=tar.getmembers()
    prompt=next((m for m in members if m.isfile() and m.name.endswith('/etc/PROMPTS')),None)
    if prompt is None or prompt.size>1_000_000: raise ValueError('VoxForge PROMPTS missing/too large')
    for line in tar.extractfile(prompt).read().decode('utf-8-sig').splitlines():
        fields=line.split(maxsplit=1)
        if len(fields)!=2: continue
        stem=Path(fields[0]).name
        member=next((m for m in members if m.isfile() and Path(m.name).stem==stem and m.name.lower().endswith(('.wav','.flac'))),None)
        if member is None: raise ValueError('VoxForge audio/text mismatch: '+stem)
        if member.size>20_000_000: raise ValueError('VoxForge extracted audio too large')
        yield member,fields[0],fields[1]


def canonical_clip(root, source, reference, provenance, start=0., end=None, recipe=None):
    end = audio_duration(source) if end is None else end
    identity = {'source_sha256': digest_file(source), 'start': start, 'end': end, 'recipe': recipe, 'provenance': provenance,
                'reference': reference}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]
    directory = root/'datasets'/key
    if (directory/'dataset.json').exists(): return load_dataset(root, key)[0]
    directory.mkdir(parents=True, exist_ok=True)
    budget = DiskBudget(root); budget.reserve(int((end-start)*32000)+100_000)
    dest = directory/'audio.wav'
    try:
        command = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', str(start), '-i', str(source), '-t', str(end-start)]
        if recipe:
            kind = recipe['kind']
            if kind == 'noise':
                command += ['-f', 'lavfi', '-i', f"anoisesrc=color=white:amplitude=0.015:seed={recipe['seed']}:r=16000:d={end-start}",
                            '-filter_complex', '[0:a][1:a]amix=inputs=2:duration=first:normalize=0']
            elif kind == 'reverb': command += ['-af', 'aecho=0.8:0.9:70|130:0.25|0.12']
            elif kind == 'compression': command += ['-af', 'aresample=8000,acrusher=bits=8:mode=lin:aa=1,aresample=16000']
            else: raise ValueError('Unknown degradation')
        command += ['-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(dest)]
        subprocess.run(command, check=True, timeout=120)
        gold = crop_gold(reference, start, end)
        record = finalize(directory, dest, gold, provenance['title'], provenance['scenario'], provenance['corpus'], provenance.get('microphone', ''))
        record.update(provenance=provenance, language=provenance['language'], split=provenance['split'],
                      group=provenance['group'], original=not bool(recipe), recipe=recipe, source_interval=[start,end])
        if gold.get('turns'):
            events=sorted((t,delta) for a,b,_ in gold['turns'] for t,delta in [(a,1),(b,-1)])
            overlap=0.; active=0; previous=0.
            for t,delta in events:
                if active>1: overlap+=t-previous
                active+=delta; previous=t
            record.update(gold_overlap_seconds=overlap,gold_overlap_ratio=overlap/record['duration'])
        atomic_json(directory/'dataset.json', record)
        return record
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True); raise


def prepare_public(root):
    root=Path(root)
    with exclusive(root,'prepare',blocking=False):
        ready=root/'suites'/'public-it-en.json'
        if ready.exists():
            suite=json.loads(ready.read_text())
            for id in suite['dataset_ids']: load_dataset(root,id)
            atomic_json(root/'preparation.json',{'status':'done','clips':len(suite['dataset_ids']),'duration':suite['duration'],'bytes':DiskBudget(root).used()})
            return suite
        try: return _prepare_public(root)
        except BaseException as exc:
            atomic_json(root/'preparation.json',{'status':'error','error':str(exc)[:1000],'bytes':DiskBudget(root).used()})
            raise


def _prepare_public(root):
    """~30 min per language; originals and degraded copies count toward duration."""
    root = Path(root); sources = root/'sources'; sources.mkdir(parents=True, exist_ok=True)
    budget = DiskBudget(root); records = []
    import os
    atomic_json(root/'preparation.json',{'status':'running','owner_pid':os.getpid()})
    annotation = download('https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip', sources/'ami_manual.zip', budget)
    for meeting, split in [('ES2002a', 'calibration'), ('ES2003a', 'evaluation')]:
        gold = ami_reference(annotation, meeting)
        for channel, scenario in [('Mix-Headset', 'EN clean meeting'), ('Array1-01', 'EN far-field meeting')]:
            url = AMI+f'{meeting}/audio/{meeting}.{channel}.wav'
            raw = download(url, sources/f'{meeting}.{channel}.wav', budget, 100_000_000)
            # Same windows across microphones, separate meeting groups across splits.
            for index in range(8):
                a, b = 60.+index*55.5, 60.+(index+1)*55.5
                provenance = dict(title=f'{meeting} {channel} {index+1}', corpus='AMI', language='en', split=split,
                                  group=meeting[:-1], scenario=scenario, microphone=channel, source=url, license='CC BY 4.0',
                                  annotation_sha256=digest_file(annotation), annotation_version='1.6.2')
                records.append(canonical_clip(root, raw, gold, provenance, a, b))
            raw.unlink(); raw.with_suffix('.wav.json').unlink(missing_ok=True)
    # Index tiny archives, take distinct contributors before additional sessions.
    with urllib.request.urlopen(VOX, timeout=60) as response: listing = response.read(2_000_000).decode()
    names = list(dict.fromkeys(re.findall(r'href="([^"/]+\.tgz)"', listing)))
    contributors = {}
    for name in names: contributors.setdefault(name.split('-')[0], []).append(name)
    ordered = [items[i] for i in range(max(map(len, contributors.values()))) for items in contributors.values() if len(items)>i]
    original_seconds = 0.
    for archive_index, name in enumerate(ordered):
        if original_seconds >= 900: break
        raw = download(VOX+name, sources/name, budget, 30_000_000)
        with tarfile.open(raw) as tar:
            recording_metadata,microphone=vox_metadata(tar)
            for member,prompt_id,text in vox_utterances(tar):
                stem=Path(prompt_id).name
                temp = sources/('utterance'+Path(member.name).suffix)
                budget.reserve(member.size)
                with tar.extractfile(member) as src, temp.open('wb') as dst: shutil.copyfileobj(src, dst)
                try:
                    duration = audio_duration(temp)
                    gold = {'verified': True, 'text': text, 'duration': duration, 'timing_provenance': 'VoxForge PROMPTS utterance text; no word times'}
                    contributor = name.split('-')[0]
                    split = 'calibration' if int(hashlib.sha256(contributor.encode()).hexdigest(),16)%3 == 0 else 'evaluation'
                    provenance = dict(title=f'{contributor} {stem}', corpus='VoxForge', language='it', split=split, group=contributor,
                                      scenario='IT read speech', source=VOX+name, license='GPL corpus', microphone=microphone,
                                      recording_metadata=recording_metadata,metadata_source='Archive etc/README',
                                      archive_sha256=digest_file(raw), prompt_id=prompt_id)
                    original = canonical_clip(root, temp, gold, provenance)
                    records.append(original); original_seconds += duration
                    kind = ['noise','reverb','compression'][len(records)%3]
                    recipe = {'kind': kind, 'seed': 42, 'version': 1, 'parameters': {'noise_amplitude': .015} if kind=='noise' else
                              {'delays_ms':[70,130],'decays':[.25,.12]} if kind=='reverb' else {'sample_rate':8000,'bits':8}}
                    records.append(canonical_clip(root, temp, gold, {**provenance, 'title':provenance['title']+' '+kind,
                                   'scenario':'IT controlled '+kind}, recipe=recipe))
                finally: temp.unlink(missing_ok=True)
                if original_seconds >= 900: break
        raw.unlink(); raw.with_suffix('.tgz.json').unlink(missing_ok=True)
        atomic_json(root/'preparation.json', {'status':'running','owner_pid':os.getpid(), 'italian_original_seconds':original_seconds,'clips':len(records),'bytes':budget.used()})
    suite = {'id':'public-it-en', 'title':'AMI + VoxForge · IT/EN', 'dataset_ids':[r['id'] for r in records],
             'duration':sum(r['duration'] for r in records), 'sources':SOURCES, 'budget_bytes':budget.limit,
             'protocol':'AMI natural meetings; VoxForge read speech and deterministic degradations; speaker-disjoint splits'}
    atomic_json(root/'suites'/'public-it-en.json',suite)
    annotation.unlink(); annotation.with_suffix('.zip.json').unlink(missing_ok=True)
    atomic_json(root/'preparation.json', {'status':'done','clips':len(records),'bytes':budget.used(),'duration':suite['duration']})
    return suite
