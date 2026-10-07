#!/usr/bin/env python3
"""Convert a selected local annotation/audio window to reusable Lab gold + manifest.

RTTM, AMI word XML and CHiME/DiPCo JSON. No downloads or forced word timing.
"""
import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.lab_dataset import parse_rttm, validate_reference, audio_duration
from backend.storage import atomic_json


def seconds(value):
    if isinstance(value, (int, float)): return float(value)
    parts = str(value).split(':')
    if len(parts) == 3: return int(parts[0])*3600+int(parts[1])*60+float(parts[2])
    return float(value)


def convert(annotation, fmt):
    if fmt == 'rttm': return {'turns': parse_rttm(annotation.read_text()), 'verified': False}
    if fmt == 'json': return json.loads(annotation.read_text())
    if fmt == 'chime':
        incoming = json.loads(annotation.read_text())
        if not isinstance(incoming, list): raise ValueError('CHiME JSON must contain utterance list')
        segments = []
        for row in incoming:
            start, end = row.get('start_time', row.get('start')), row.get('end_time', row.get('end'))
            if isinstance(start, dict):
                # Clock depends on chosen microphone; never pick an arbitrary clock.
                raise ValueError('CHiME multi-clock times: select/synchronize microphone clock before conversion')
            segments.append({'start': seconds(start), 'end': seconds(end), 'speaker': row['speaker'], 'text': row.get('words', row.get('text', ''))})
        segments.sort(key=lambda s: (s['start'], s['speaker']))
        return {'verified': False, 'turns': [[s['start'], s['end'], s['speaker']] for s in segments],
                'segments': segments, 'text': ' '.join(s['text'] for s in segments)}
    words = []
    paths = sorted(annotation.glob('*.words.xml')) if annotation.is_dir() else [annotation]
    meetings = {path.name.rsplit('.', 3)[0] for path in paths}
    if len(meetings) > 1: raise ValueError('AMI directory must contain words from one meeting only')
    for path in paths:
        parts = path.name.split('.')
        if len(parts) < 4: raise ValueError('AMI word XML filename must contain meeting.speaker.words.xml')
        speaker = parts[-3]
        for elem in ET.parse(path).getroot().iter():
            if elem.tag.split('}')[-1] != 'w' or 'starttime' not in elem.attrib or 'endtime' not in elem.attrib: continue
            a, b = float(elem.attrib['starttime']), float(elem.attrib['endtime'])
            if b > a and elem.text:
                words.append({'start': a, 'end': b, 'word': elem.text, 'speaker': speaker})
    if not words: raise ValueError('No timed AMI words found')
    words.sort(key=lambda w: (w['start'], w['speaker']))
    # Word intervals are an explicit derived speech reference, not official AMI RTTM.
    return {'verified': False, 'words': words, 'text': ' '.join(w['word'] for w in words),
            'turns': [[w['start'], w['end'], w['speaker']] for w in words],
            'turn_reference': 'AMI timed word intervals; DER protocol differs from official speech-activity RTTM'}


def crop_reference(reference, start, end):
    result = {k: v for k, v in reference.items() if k not in ('turns', 'words', 'segments', 'text', 'uem')}
    if 'turns' in reference:
        result['turns'] = [[max(a, start)-start, min(b, end)-start, sp] for a, b, sp in reference['turns'] if b > start and a < end]
    if reference.get('words') and all('start' in w and 'end' in w for w in reference['words']):
        words = [w for w in reference['words'] if w.get('start', -1) >= start and w.get('end', end+1) <= end]
        result['words'] = [{**w, 'start': w['start']-start, 'end': w['end']-start} for w in words]
        result['text'] = ' '.join(w['word'] for w in words)
    elif reference.get('segments'):
        segments = reference['segments']
        # Whole utterances required; text cannot be cropped accurately without word times.
        if any(s['start'] < start < s['end'] or s['start'] < end < s['end'] for s in segments):
            raise ValueError('Window cuts CHiME utterance; choose utterance boundaries or use timed gold words')
        selected = [s for s in segments if s['start'] >= start and s['end'] <= end]
        result['text'] = ' '.join(s['text'] for s in selected)
        result['words'] = [{'word': token, 'speaker': s['speaker']} for s in selected for token in s['text'].split()]
        result['segments'] = [{**s, 'start': s['start']-start, 'end': s['end']-start} for s in selected]
    elif 'text' in reference:
        if start or (reference.get('duration') is not None and end != reference['duration']):
            raise ValueError('Untimed text cannot be cropped; supply gold for selected audio window')
        result['text'] = reference['text']
    result['uem'] = [[max(a,start)-start, min(b,end)-start] for a,b in reference.get('uem', [[start,end]]) if b>start and a<end]
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--audio', type=Path, required=True)
    parser.add_argument('--annotation', type=Path, required=True)
    parser.add_argument('--format', choices=['json', 'rttm', 'ami', 'chime'], required=True)
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--end', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--corpus', default='')
    parser.add_argument('--scenario', default='')
    parser.add_argument('--channel', default='mono', help='Source channel description; audio downmixed to mono')
    args = parser.parse_args()
    if args.output.exists(): parser.error('Output directory already exists; choose new destination')
    duration = audio_duration(args.audio)
    if not 0 <= args.start < args.end <= duration+.01: parser.error('Window outside audio duration')
    ref = convert(args.annotation, args.format)
    if args.format == 'json' and 'text' in ref and not ref.get('words') and not ref.get('segments'):
        ref['duration'] = duration
    ref = crop_reference(ref, args.start, args.end)
    ref['verified'] = False
    ref['source'] = {'annotation': str(args.annotation.resolve()), 'start': args.start, 'end': args.end}
    validate_reference(ref, args.end-args.start)
    args.output.mkdir(parents=True)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(args.start), '-i', str(args.audio), '-t', str(args.end-args.start),
                    '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(args.output/'audio.wav')], check=True)
    atomic_json(args.output/'gold.json', ref)
    atomic_json(args.output/'manifest.json', {'corpus': args.corpus, 'samples': [{'id': args.output.name, 'audio': 'audio.wav',
                'reference': 'gold.json', 'scenario': args.scenario, 'channel': args.channel}]})
    print(f'{args.output}/manifest.json — verify gold on selected audio, then set verified=true before importing')
