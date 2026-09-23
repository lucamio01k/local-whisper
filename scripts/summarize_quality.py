#!/usr/bin/env python3
"""Summarize observed timings/alignment, without treating them as accuracy."""
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/evaluation-v1'
groups = defaultdict(list)
for path in sorted((OUT / 'runs').glob('*/metrics.json')):
    row = json.loads(path.read_text())
    segments = json.loads((path.parent / 'segments.json').read_text())
    words = [w for s in segments for w in s.get('words', [])]
    row['words'] = len(words)
    row['invalid_words'] = sum(bool(w.get('timing_issue')) or w.get('start') is None or w.get('end') is None for w in words)
    log = (path.parent / 'cli.log').read_text()
    for key in ('load', 'total'):
        match = re.search(rf'{key} time\s*=\s*([\d.]+) ms', log)
        row[key + '_seconds'] = float(match[1]) / 1000 if match else None
    groups[(row['variant'], row['vad'])].append(row)

summary = []
for (variant, vad), rows in groups.items():
    words = sum(r['words'] for r in rows)
    invalid = sum(r['invalid_words'] for r in rows)
    summary.append(dict(variant=variant, vad=vad, runs=len(rows),
        median_wall_seconds=statistics.median(r['seconds'] for r in rows),
        median_rtf=statistics.median(r['rtf'] for r in rows),
        first_read_seconds=statistics.median(r['seconds'] for r in rows if r['repeat']==1),
        warm_repeat_seconds=statistics.median(r['seconds'] for r in rows if r['repeat']>1),
        median_load_seconds=statistics.median(r['load_seconds'] for r in rows if r['load_seconds'] is not None),
        words=words, invalid_words=invalid, invalid_word_fraction=invalid/words if words else None))
(OUT / 'timing_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2))
