"""Pure alignment, attribution and presentation helpers. No model or app imports."""
import copy
import math
import re
import textwrap
from collections import defaultdict

PIPELINE_VERSION = 'words-v1'


def cpp_time(segment, key):
    value = (segment.get('timestamps') or {}).get(key)
    if isinstance(value, str):
        match = re.fullmatch(r'(\d+):(\d+):(\d+)[,.](\d+)', value)
        if match:
            h, m, s, frac = match.groups()
            return int(h) * 3600 + int(m) * 60 + int(s) + float('0.' + frac)
    value = (segment.get('offsets') or {}).get(key)
    if value is not None:
        return float(value) / 1000
    value = segment.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def valid_interval(item):
    a, b = item.get('start'), item.get('end')
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isfinite(a) and math.isfinite(b) and 0 <= a < b


def cpp_words(segment, use_dtw=False):
    """Preserve exact ASR text; token offsets associate timing with word spans."""
    text = segment.get('text', '')
    tokens = [t for t in segment.get('tokens', []) if not re.match(r'^(\[_|<\|)', t.get('text', ''))]
    if not tokens or ''.join(t.get('text', '') for t in tokens).strip() != text.strip():
        return []
    start, end = cpp_time(segment, 'from'), cpp_time(segment, 'to')
    if start is None or end is None:
        return []
    token_text = ''.join(t.get('text', '') for t in tokens)
    spans, cursor = [], 0
    for token in tokens:
        size = len(token.get('text', ''))
        spans.append((cursor, cursor + size, token))
        cursor += size
    result = []
    # Leading whitespace is carried by each word, so concatenation preserves text.
    for match in re.finditer(r'\s*\S+', token_text):
        selected = [t for a, b, t in spans if a < match.end() and b > match.start() and token_text[max(a, match.start()):min(b, match.end())].strip()]
        if not selected:
            continue
        times = [(cpp_time(t, 'from'), cpp_time(t, 'to')) for t in selected]
        anchors = [float(t['t_dtw']) / 100 for t in selected if isinstance(t.get('t_dtw'), (int, float)) and t['t_dtw'] >= 0]
        word = {'word': match.group(), 'prob': min((t.get('p', 1.) for t in selected)),
                'alignment': 'dtw' if use_dtw else 'token', 'speaker': None}
        if use_dtw:
            word['_anchor'] = sum(anchors) / len(anchors) if len(anchors) == len(selected) else None
        else:
            word['start'] = max(start, times[0][0]) if times[0][0] is not None else None
            word['end'] = min(end, times[-1][1]) if times[-1][1] is not None else None
        result.append(word)
    if use_dtw:
        anchors = [w['_anchor'] for w in result]
        valid = all(a is not None and start <= a <= end for a in anchors) and all(a <= b for a, b in zip(anchors, anchors[1:]))
        bounds = [start] + [(a + b) / 2 for a, b in zip(anchors, anchors[1:])] + [end] if valid else []
        for i, word in enumerate(result):
            word.pop('_anchor')
            word['start'] = bounds[i] if valid else None
            word['end'] = bounds[i + 1] if valid else None
    previous = start
    for word in result:
        if not valid_interval(word) or word['start'] < previous:
            word['timing_issue'] = True
        if valid_interval(word):
            previous = word['end']
    return result


def speaker_for(start, end, turns):
    if start is None or end is None or end <= start:
        return None
    scores = defaultdict(float)
    for a, b, speaker in turns:
        overlap = max(0., min(end, b) - max(start, a))
        if overlap:
            scores[speaker] += overlap
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if not ranked or (len(ranked) > 1 and math.isclose(ranked[0][1], ranked[1][1], abs_tol=1e-6)):
        return None
    return ranked[0][0]


def candidates(start, end, turns):
    if start is None or end is None or end <= start:
        return []
    return sorted({speaker for a, b, speaker in turns if min(end, b) > max(start, a)})


def has_overlap(start, end, turns):
    relevant = [(max(start, a), min(end, b), sp) for a, b, sp in turns if a < end and b > start]
    return any(sp != other and min(b, d) > max(a, c) for i, (a, b, sp) in enumerate(relevant) for c, d, other in relevant[i+1:])


def assign_speakers(segments, standard, exclusive=None, precision='words'):
    turns = exclusive if exclusive is not None else standard
    labels = {}
    for _, _, speaker in sorted(turns):
        labels.setdefault(speaker, f'Speaker {len(labels) + 1}')
    result = []
    for source in segments:
        seg = copy.deepcopy(source)
        words = seg.get('words') or []
        aligned = precision == 'words' and words and all(valid_interval(w) and not w.get('timing_issue') for w in words)
        if aligned:
            groups = []
            for word in words:
                raw = speaker_for(word['start'], word['end'], turns)
                word['speaker'] = labels.get(raw)
                word['speaker_candidates'] = [labels[sp] for sp in candidates(word['start'], word['end'], turns)]
                word['overlap'] = has_overlap(word['start'], word['end'], standard)
                if groups and groups[-1]['speaker'] == word['speaker']:
                    groups[-1]['words'].append(word)
                    groups[-1]['end'] = word['end']
                else:
                    groups.append({'start': word['start'], 'end': word['end'], 'speaker': word['speaker'], 'words': [word]})
            for group in groups:
                group['text'] = ''.join(w['word'] for w in group['words'])
                group['source_segment_id'] = source.get('id')
                result.append(group)
        else:
            seg['speaker'] = labels.get(speaker_for(seg['start'], seg['end'], turns))
            seg['speaker_candidates'] = [labels[sp] for sp in candidates(seg['start'], seg['end'], turns)]
            seg['alignment_issue'] = precision == 'words'
            seg['overlap'] = has_overlap(seg['start'], seg['end'], standard)
            for word in words:
                word['speaker'] = seg['speaker']
            result.append(seg)
    for i, seg in enumerate(result, 1):
        seg['id'] = i
    return result


def diagnostics(segments):
    issues = []
    def add(kind, seg, **extra):
        issues.append({'kind': kind, 'segment_id': seg.get('id'), 'start': seg.get('start'), 'end': seg.get('end'), **extra})
    for seg in segments:
        if not valid_interval(seg):
            add('invalid_timing', seg)
        if seg.get('alignment_issue'):
            add('alignment_missing', seg)
        if seg.get('speaker') is None:
            add('unknown_speaker', seg)
        if len(seg.get('speaker_candidates', [])) > 1 or any(len(w.get('speaker_candidates', [])) > 1 for w in seg.get('words', [])):
            add('speaker_ambiguity', seg)
        if seg.get('overlap') or any(w.get('overlap') for w in seg.get('words', [])):
            add('overlap', seg)
        if any(w.get('timing_issue') or not valid_interval(w) for w in seg.get('words', [])):
            add('invalid_word_timing', seg)
        if any(w.get('prob') is not None and w['prob'] < .5 for w in seg.get('words', [])):
            add('low_probability', seg)
    # Detect repeated phrases across ASR boundaries too.
    tokens = [(re.sub(r'[^\w]', '', word).lower(), seg) for seg in segments for word in seg.get('text', '').split()]
    reported = set()
    for i in range(len(tokens)):
        for size in range(3, min(30, (len(tokens) - i) // 3) + 1):
            phrase = [t[0] for t in tokens[i:i+size]]
            if phrase == [t[0] for t in tokens[i+size:i+2*size]] == [t[0] for t in tokens[i+2*size:i+3*size]]:
                seg = tokens[i][1]
                if seg.get('id') not in reported:
                    add('repetition', seg, end=tokens[i+3*size-1][1]['end'])
                    reported.add(seg.get('id'))
                break
    return issues


def reading_segments(segments, max_seconds=45):
    result = []
    for seg in segments:
        if result and seg.get('speaker') == result[-1].get('speaker') and seg['start'] - result[-1]['end'] <= 2 and seg['end'] - result[-1]['start'] <= max_seconds:
            result[-1]['text'] = result[-1]['text'].rstrip() + ' ' + seg['text'].lstrip()
            result[-1]['end'] = seg['end']
        else:
            result.append(copy.deepcopy(seg))
    return result


def subtitle_segments(segments, include_speaker=False):
    result = []
    pending = []
    def flush():
        if pending:
            prefix = (str(pending[0].get('speaker') or 'Non determinato') + ': ') if include_speaker else ''
            text = prefix + ' '.join(w['word'].strip() for w in pending)
            result.append({'start': pending[0]['start'], 'end': pending[-1]['end'], 'speaker': pending[0].get('speaker'),
                           'text': '\n'.join(textwrap.wrap(text, 42))})
            pending.clear()
    for seg in segments:
        words = seg.get('words') or []
        if not words or not all(valid_interval(w) and not w.get('timing_issue') for w in words):
            flush()
            # Legacy boundaries stay untouched; no fabricated word times.
            prefix = (str(seg.get('speaker') or 'Non determinato') + ': ') if include_speaker else ''
            result.append({**seg, 'text': '\n'.join(textwrap.wrap(prefix + seg['text'], 42))})
            continue
        for source in words:
            word = {**source, 'speaker': seg.get('speaker')}
            prefix = (str(word.get('speaker') or 'Non determinato') + ': ') if include_speaker else ''
            text = prefix + ' '.join(w['word'].strip() for w in pending + [word])
            if pending and (word['end'] - pending[0]['start'] > 7 or word['speaker'] != pending[0]['speaker'] or len(textwrap.wrap(text, 42)) > 2):
                flush()
            pending.append(word)
        flush()
    return result
