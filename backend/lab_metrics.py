"""Explicit metric contracts. Missing gold => null, never an invented zero."""
import re
from backend.quality import valid_interval


def normalized(text):
    return re.findall(r'\w+', text.lower())


def edit_distance(a, b):
    row = list(range(len(b)+1))
    for i, x in enumerate(a, 1):
        nxt = [i]
        for j, y in enumerate(b, 1):
            nxt.append(min(nxt[-1]+1, row[j]+1, row[j-1]+(x != y)))
        row = nxt
    return row[-1]


def correct_pairs(a, b):
    """Levenshtein alignment, diagonal-first ties; return exact lexical matches."""
    if len(a)*len(b) > 20_000_000:
        raise ValueError('WDER alignment too large: split dataset into shorter clips')
    ops = [bytearray(len(b)+1) for _ in range(len(a)+1)]
    row = list(range(len(b)+1))
    for j in range(1, len(b)+1): ops[0][j] = 2
    for i, x in enumerate(a, 1):
        ops[i][0] = 1
        nxt = [i]
        for j, y in enumerate(b, 1):
            options = [row[j-1]+(x != y), row[j]+1, nxt[j-1]+1]
            op = min(range(3), key=options.__getitem__)
            ops[i][j] = op
            nxt.append(options[op])
        row = nxt
    pairs, i, j = [], len(a), len(b)
    while i or j:
        op = ops[i][j]
        if op == 0:
            if a[i-1] == b[j-1]: pairs.append((i-1, j-1))
            i, j = i-1, j-1
        elif op == 1: i -= 1
        else: j -= 1
    return list(reversed(pairs))


def token_speakers(segments):
    result = []
    for segment in segments:
        words = segment.get('words') or []
        detailed = [(token, w.get('speaker', segment.get('speaker'))) for w in words for token in normalized(w.get('word', ''))]
        text_tokens = normalized(segment.get('text', ''))
        if [t for t, _ in detailed] == text_tokens:
            result.extend(detailed)
        else:
            result.extend((t, segment.get('speaker')) for t in text_tokens)
    return result


def annotation(turns):
    from pyannote.core import Annotation, Segment
    ann = Annotation()
    for i, (a, b, speaker) in enumerate(turns):
        if speaker not in (None, 'UNKNOWN') and b > a:
            ann[Segment(a, b), i] = speaker
    return ann


def evaluate(reference, segments, duration, raw_turns=None, fixed_mapping=None):
    from pyannote.core import Segment, Timeline
    from pyannote.metrics.diarization import DiarizationErrorRate
    scores = {k: None for k in ('wer', 'der', 'speaker_confusion', 'missed_speech', 'false_alarm',
                                'wder', 'word_speaker_accuracy', 'pyannote_der')}
    scores.update(reference_words=0, word_errors=0, aligned_correct_words=0, speaker_word_errors=0, warnings=[],audio_seconds=duration,gold_seconds=0.)
    if not reference.get('verified'):
        scores['warnings'].append('Gold not human-verified; quality metrics unavailable')
        return scores
    regions = reference.get('uem', [[0,duration]])
    scores['gold_seconds'] = sum(b-a for a,b in regions)
    scores['audio_seconds'] = duration
    # Do not score anonymized/boundary regions. Prefer genuine predicted word times.
    import copy
    scoring_segments = []
    for s in segments:
        detailed = s.get('words') or []
        if detailed and all(valid_interval(w) for w in detailed):
            words = [w for w in detailed if any(a <= (w['start']+w['end'])/2 < b for a,b in regions)]
            if words: scoring_segments.append({**s,'words':words,'text':' '.join(w['word'] for w in words)})
        elif valid_interval(s) and any(a <= s['start'] and s['end'] <= b+.001 for a,b in regions):
            scoring_segments.append(copy.deepcopy(s))
        elif regions == [[0,duration]]: scoring_segments.append(copy.deepcopy(s))
    hypothesis = token_speakers(scoring_segments)
    ref_words = reference.get('words') or []
    ref_text = reference.get('text')
    if regions != [[0,duration]] and ref_words and all(valid_interval(w) for w in ref_words):
        if ref_text is not None and normalized(ref_text) != [t for w in ref_words for t in normalized(w['word'])]:
            scores['warnings'].append('Partial UEM with inconsistent gold text/words; lexical metrics unavailable')
            ref_text,ref_words=None,[]
        else:
            ref_words=[w for w in ref_words if any(a <= (w['start']+w['end'])/2 < b for a,b in regions)]
            if ref_text is not None: ref_text=' '.join(w['word'] for w in ref_words)
    if ref_text is not None:
        a, b = normalized(ref_text), [t for t, _ in hypothesis]
        scores['reference_words'] = len(a)
        scores['word_errors'] = edit_distance(a, b)
        scores['wer'] = scores['word_errors'] / len(a) if a else None
    ref_turns = reference.get('turns')
    mapping = None
    uem = Timeline([Segment(a, b) for a, b in reference.get('uem', [[0, duration]])])
    if ref_turns is not None:
        ref_ann = annotation(ref_turns)
        hyp_ann = annotation([(s['start'], s['end'], s.get('speaker')) for s in segments if valid_interval(s)])
        der = DiarizationErrorRate(collar=.25, skip_overlap=False)
        detail = der(ref_ann, hyp_ann, uem=uem, detailed=True)
        total = detail['total']
        if total:
            scores.update(der=(detail['confusion']+detail['missed detection']+detail['false alarm'])/total,
                          speaker_confusion=detail['confusion']/total, missed_speech=detail['missed detection']/total,
                          false_alarm=detail['false alarm']/total)
        scores['der_detail_seconds'] = detail
        # Attribution mapping includes brief turns; DER's collar remains separate.
        mapping = DiarizationErrorRate(collar=0, skip_overlap=False).optimal_mapping(ref_ann, hyp_ann, uem=uem)
        scores['speaker_mapping'] = mapping
        if raw_turns is not None:
            raw_detail = DiarizationErrorRate(collar=.25, skip_overlap=False)(ref_ann, annotation(raw_turns), uem=uem, detailed=True)
            scores['pyannote_detail_seconds'] = raw_detail
            scores['pyannote_der'] = ((raw_detail['confusion']+raw_detail['missed detection']+raw_detail['false alarm'])/raw_detail['total']) if raw_detail['total'] else None
    if fixed_mapping is not None:
        mapping=fixed_mapping
    scores['attribution_speaker_mapping']=mapping
    if mapping is not None and ref_words:
        ref_tokens = [(t, w.get('speaker')) for w in ref_words for t in normalized(w.get('word', ''))]
        if ref_text is not None and [t for t, _ in ref_tokens] != normalized(ref_text):
            scores['warnings'].append('Gold text and words disagree; WDER unavailable')
        elif any(s in (None, 'UNKNOWN') for _, s in ref_tokens):
            scores['warnings'].append('Missing gold word speaker; WDER unavailable')
        else:
            try:
                pairs = correct_pairs([t for t, _ in ref_tokens], [t for t, _ in hypothesis])
                errors = sum(mapping.get(hypothesis[j][1]) != ref_tokens[i][1] for i, j in pairs)
                scores.update(aligned_correct_words=len(pairs), speaker_word_errors=errors,
                              wder=errors/len(pairs) if pairs else None)
            except ValueError as exc:
                scores['warnings'].append(str(exc))
        timed = [w for w in ref_words if valid_interval(w) and w.get('speaker')]
        errors = 0
        for word in timed:
            mid = (word['start']+word['end'])/2
            labels = {mapping.get(s.get('speaker')) for s in segments if valid_interval(s) and s['start'] <= mid < s['end']}
            errors += labels != {word['speaker']}
        scores['word_speaker_accuracy'] = 1-errors/len(timed) if timed else None
        scores['word_timeline_errors'], scores['word_timeline_count'] = errors, len(timed)
    return scores


def divergences(reference, baseline, refined, base_mapping, final_mapping):
    rows = []
    for original, final in zip(baseline, refined):
        changed = original.get('speaker') != final.get('speaker')
        mid = (original['start']+original['end'])/2
        gold = {s for a, b, s in reference.get('turns', []) if a <= mid < b}
        valid_gold = (reference.get('verified') and len(gold) == 1 and base_mapping is not None and final_mapping is not None
                      and any(a <= mid < b for a,b in reference.get('uem', [[0,float('inf')]])))
        expected = next(iter(gold)) if valid_gold else None
        before = base_mapping.get(original.get('speaker')) if base_mapping else None
        after = base_mapping.get(final.get('speaker')) if base_mapping else None
        if not changed and (not valid_gold or after == expected): continue
        outcome = ('new_error' if before == expected else 'wrong_correction') if valid_gold else 'unscored'
        if valid_gold and after == expected: outcome = 'correct_correction'
        if valid_gold and not changed: outcome = 'remaining_error'
        rows.append({'segment_id': original.get('id'), 'start': original['start'], 'end': original['end'],
            'text': original.get('text'), 'reference': expected, 'baseline': original.get('speaker'),
            'refined': final.get('speaker'), 'outcome': outcome})
    return rows


def aggregate(rows):
    """Micro-average counts; denominator-specific gold coverage, absent values stay null."""
    metrics=[r['metrics'] for r in rows]
    words=sum(m.get('reference_words',0) for m in metrics if m.get('wer') is not None)
    errors=sum(m.get('word_errors',m['wer']*m.get('reference_words',0)) for m in metrics if m.get('wer') is not None)
    aligned=sum(m.get('aligned_correct_words',0) for m in metrics if m.get('wder') is not None)
    speaker_errors=sum(m.get('speaker_word_errors',0) for m in metrics if m.get('wder') is not None)
    totals={}
    for key in ('der_detail_seconds','pyannote_detail_seconds'):
        detail=[m[key] for m in metrics if key in m]
        den=sum(d.get('total',0) for d in detail)
        num=sum(d.get('confusion',0)+d.get('missed detection',0)+d.get('false alarm',0) for d in detail)
        totals['der' if key=='der_detail_seconds' else 'pyannote_der']=num/den if den else None
    return {**totals,'wer':errors/words if words else None,'wder':speaker_errors/aligned if aligned else None,
            'reference_words':words,'word_errors':errors,'aligned_correct_words':aligned,'speaker_word_errors':speaker_errors,
            'clips':len(rows),'coverage':{k:sum(m.get(k) is not None for m in metrics) for k in ('wer','der','wder')},
            'gold_seconds':sum(m.get('gold_seconds',0) for m in metrics),'audio_seconds':sum(m.get('audio_seconds',0) for m in metrics),
            'total_seconds':sum(r['performance']['total_seconds'] for r in rows),
            'refiner_seconds':sum(r['performance']['refiner_seconds'] for r in rows),
            'peak_ram_bytes':max((r['performance']['peak_ram_bytes'] for r in rows),default=0)}
