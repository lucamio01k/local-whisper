"""Small local dataset imports. No downloads; frozen audio/reference hashes."""
import json
import math
import shutil
import subprocess
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from backend.storage import atomic_json, digest_file

SCENARIOS = ['IT read speech', 'IT controlled noise', 'IT controlled reverb', 'IT controlled compression', 'EN clean meeting', 'EN far-field meeting', 'EN overlap 10%', 'EN overlap 20%',
             'EN overlap 40%', 'EN noisy room', 'IT conversation', 'IT smartphone conversation']


def parse_rttm(text):
    turns = []
    recordings = set()
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        fields = line.split()
        if len(fields) < 8 or fields[0] != 'SPEAKER': raise ValueError('Invalid RTTM SPEAKER row')
        recordings.add(fields[1])
        start, length = float(fields[3]), float(fields[4])
        turns.append([start, start+length, fields[7]])
    if len(recordings) > 1: raise ValueError('RTTM must contain one recording per imported clip')
    return turns


def validate_reference(reference, duration):
    if not isinstance(reference, dict): raise ValueError('Reference must be a JSON object')
    if type(reference.get('verified', False)) is not bool: raise ValueError('verified must be boolean')
    if 'text' in reference and not isinstance(reference['text'], str): raise ValueError('Gold text must be string')
    def interval(a, b):
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (a, b)) or not 0 <= a < b <= duration+.05:
            raise ValueError('Gold interval outside audio duration or invalid')
    if 'turns' in reference:
        if not isinstance(reference['turns'], list): raise ValueError('Gold turns must be list')
        for turn in reference['turns']:
            if not isinstance(turn, list) or len(turn) != 3: raise ValueError('Gold turn must be [start, end, speaker]')
            interval(*turn[:2])
            if not isinstance(turn[2], str) or not turn[2] or turn[2] == 'UNKNOWN': raise ValueError('Gold speaker missing')
    if 'segments' in reference:
        if not isinstance(reference['segments'],list): raise ValueError('Gold segments must be list')
        for segment in reference['segments']:
            if not isinstance(segment,dict) or not isinstance(segment.get('text'),str) or not isinstance(segment.get('speaker'),str):
                raise ValueError('Gold segment must contain text and speaker')
            interval(segment.get('start'),segment.get('end'))
    if 'words' in reference:
        if not isinstance(reference['words'], list): raise ValueError('Gold words must be list')
        previous = -1
        for word in reference['words']:
            if not isinstance(word, dict) or not isinstance(word.get('word'), str): raise ValueError('Gold word must contain word string')
            if word.get('speaker') is not None and not isinstance(word['speaker'], str): raise ValueError('Gold word speaker must be string')
            if 'start' in word or 'end' in word:
                interval(word.get('start'), word.get('end'))
                if word['start'] < previous: raise ValueError('Gold words must be ordered by start')
                previous = word['start']
    if 'uem' in reference:
        if not isinstance(reference['uem'], list): raise ValueError('UEM must be list')
        previous = 0
        for a, b in reference['uem']:
            interval(a, b)
            if a < previous: raise ValueError('UEM must be ordered and non-overlapping')
            previous = b
    return reference


def audio_duration(audio):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(audio)],
                            capture_output=True, text=True, check=True, timeout=30)
    duration = float(result.stdout)
    if not math.isfinite(duration) or duration <= 0: raise ValueError('Invalid audio duration')
    return duration


def finalize(directory, audio, reference, title='', scenario='', corpus='', channel=''):
    duration = audio_duration(audio)
    validate_reference(reference, duration)
    atomic_json(directory/'reference.json', reference)
    record = {'id': directory.name, 'title': title or audio.stem, 'scenario': scenario, 'corpus': corpus, 'channel': channel,
              'created_at': datetime.now(timezone.utc).isoformat(), 'audio': audio.name, 'duration': duration,
              'audio_sha256': digest_file(audio), 'reference_sha256': digest_file(directory/'reference.json'),
              'verified': reference.get('verified', False),
              'gold': {k: k in reference for k in ('text', 'turns', 'words')}}
    atomic_json(directory/'dataset.json', record)
    return record


def load_dataset(root, dataset_id):
    if not isinstance(dataset_id, str) or len(dataset_id) != 32 or any(c not in '0123456789abcdef' for c in dataset_id):
        raise ValueError('Invalid dataset ID')
    directory = root/'datasets'/dataset_id
    record = json.loads((directory/'dataset.json').read_text())
    audio = directory/record['audio']
    if audio.parent != directory: raise ValueError('Invalid audio path')
    reference_file = directory/'reference.json'
    if digest_file(audio) != record['audio_sha256'] or digest_file(reference_file) != record['reference_sha256']:
        raise ValueError('Frozen dataset modified; reimport as new dataset')
    reference = json.loads(reference_file.read_text())
    validate_reference(reference, record['duration'])
    return record, audio, reference


def import_manifest(root, manifest_path):
    """Prepared AMI/LibriCSS/CHiME/DiPCo/IT subsets use this common manifest."""
    path = Path(manifest_path).resolve()
    manifest = json.loads(path.read_text())
    records = []
    for sample in manifest['samples']:
        audio = (path.parent/sample['audio']).resolve()
        reference_path = (path.parent/sample['reference']).resolve() if sample.get('reference') else None
        reference = json.loads(reference_path.read_text()) if reference_path else {'verified': False}
        identity={'audio':digest_file(audio),'reference':reference,'sample':sample,'corpus':manifest.get('corpus','')}
        directory = root/'datasets'/hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:32]
        if (directory/'dataset.json').exists():
            records.append(load_dataset(root,directory.name)[0]); continue
        directory.mkdir(parents=True)
        try:
            destination = directory/('audio'+audio.suffix.lower())
            from backend.lab_download import DiskBudget
            import os
            matching=next((p.parent/json.loads(p.read_text())['audio'] for p in (root/'datasets').glob('*/dataset.json')
                           if json.loads(p.read_text())['audio_sha256']==identity['audio']),None)
            if matching: os.link(matching,destination)
            else:
                DiskBudget(root).reserve(audio.stat().st_size+100_000)
                shutil.copyfile(audio, destination)
            records.append(finalize(directory, destination, reference, sample.get('id', ''),
                                    sample.get('scenario', ''), manifest.get('corpus', ''), sample.get('channel', '')))
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
    return records
