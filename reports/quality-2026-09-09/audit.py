"""Read-only audit of supplied examples and isolated backend helper behavior."""
import ast
import collections
import json
import re
import typing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
JOBS = ['b12cfe14-e01a-41a6-afac-d18c31eb7046', 'dc48742f-baa9-44d6-b0cb-0ddb737ea9ba']
report = {'jobs': []}
for jid in JOBS:
    path = ROOT / 'uploads' / jid
    raw = json.loads((path / 'transcript.json').read_text())
    diarized = json.loads((path / 'diarized.json').read_text())
    meta = json.loads((path / 'meta.json').read_text())
    texts = [s['text'].strip() for s in raw]
    report['jobs'].append({
        'job_id': jid, 'filename': meta['filename'], 'recorded_metrics': meta['metrics'],
        'raw_segments': len(raw), 'diarized_segments': len(diarized),
        'word_timestamps': sum(len(s.get('words', [])) for s in raw),
        'word_count': len(' '.join(texts).split()),
        'text_preserved': ' '.join(texts) == ' '.join(s['text'].strip() for s in diarized),
        'zero_duration': sum(s['end'] <= s['start'] for s in raw),
        'display_zero_duration': sum(int(s['end']) == int(s['start']) for s in raw),
        'adjacent_duplicate_segments': sum(a == b for a, b in zip(texts, texts[1:])),
        'longest_turn_seconds': max(s['end'] - s['start'] for s in diarized),
        'speaker_turns': dict(collections.Counter(s['speaker'] for s in diarized)),
    })

names = {'_speaker_stats', '_speaker_neighbor_scores', '_merge_speaker_into',
         '_conservative_merge_speakers', '_parse_timestamp_seconds', '_whisper_cpp_segment_time'}
ns = {name: getattr(typing, name) for name in ['List', 'Dict', 'Any', 'Optional']}
ns['re'] = re
nodes = [n for n in ast.parse((ROOT / 'backend/main.py').read_text()).body
         if isinstance(n, ast.FunctionDef) and n.name in names]
exec(compile(ast.Module(body=nodes, type_ignores=[]), '<isolated backend helpers>', 'exec'), ns)
segments = [{'start': i * 10., 'end': i * 10. + (5 if i % 20 == 10 else 10),
             'text': 'test', 'speaker': 'B' if i % 20 == 10 else 'A'} for i in range(100)]
report['synthetic_checks'] = {
    'minority_before': ns['_speaker_stats'](segments),
    'minority_after': ns['_speaker_stats'](ns['_conservative_merge_speakers'](segments)),
    'offset_500ms_parsed_seconds': ns['_whisper_cpp_segment_time']({'offsets': {'from': 500}}, 'from'),
    'zero_timestamp_parsed': ns['_whisper_cpp_segment_time'](
        {'timestamps': {'from': '00:00:00,000'}, 'offsets': {'from': 0}}, 'from'),
}
turns_file = OUT / 'diarization_turns.json'
if turns_file.exists():
    turns_data = json.loads(turns_file.read_text())
    raw = json.loads((ROOT / 'uploads' / JOBS[0] / 'transcript.json').read_text())
    diagnostics = {}
    for name in ['speaker_diarization', 'exclusive_speaker_diarization']:
        mixed = []; changed = []; no_overlap = []
        for seg in raw:
            scores = collections.defaultdict(float)
            best = 'SPEAKER_00'; largest = 0.
            for start, end, speaker in turns_data[name]:
                overlap = max(0., min(seg['end'], end) - max(seg['start'], start))
                scores[speaker] += overlap
                if overlap > largest:
                    largest, best = overlap, speaker
            if sum(v >= .25 for v in scores.values()) > 1:
                mixed.append({'start': seg['start'], 'end': seg['end'], 'overlap_seconds': dict(scores)})
            if largest == 0:
                no_overlap.append(seg['id'])
            elif best != max(scores, key=scores.get):
                changed.append(seg['id'])
        diagnostics[name] = {'turns': len(turns_data[name]), 'mixed_segments_at_least_250ms_per_speaker': mixed,
                             'largest_vs_sum_changed_segment_ids': changed, 'no_overlap_segment_ids': no_overlap}
    report['diarization_diagnostics'] = diagnostics
(OUT / 'audit.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))
print(json.dumps(report, indent=2, ensure_ascii=False))
