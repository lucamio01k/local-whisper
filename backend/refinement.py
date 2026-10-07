"""Lab-only conservative speaker refinement. No model imports at module load."""
import copy
import math
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from collections import defaultdict
from backend.quality import diagnostics, valid_interval

UNKNOWN = 'UNKNOWN'
REFINEMENT_VERSION = 'lab-v1'


@dataclass(frozen=True)
class RefinementConfig:
    ambiguity_margin: float = .15
    acoustic_confidence: float = .75
    semantic_confidence: float = .9
    change_confidence: float = .85
    decision_margin: float = .15
    context_turns: int = 2
    audio_window: float = 3.
    min_audio: float = 1.5

    def __post_init__(self):
        for name in ('ambiguity_margin', 'acoustic_confidence', 'semantic_confidence', 'change_confidence', 'decision_margin'):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f'{name}: expected 0–1')
        if type(self.context_turns) is not int or not 0 <= self.context_turns <= 5:
            raise ValueError('context_turns: expected integer 0–5')
        if not 1.5 <= self.min_audio <= self.audio_window <= 5:
            raise ValueError('audio_window: expected min_audio ≤ window ≤ 5, min_audio ≥ 1.5')


@dataclass
class Evidence:
    segment: dict
    candidates: list
    previous: list
    following: list
    temporal: dict
    reasons: list
    acoustic: dict = field(default_factory=dict)


@dataclass
class Resolution:
    speaker: str = UNKNOWN
    confidence: float = 0.
    scores: dict = field(default_factory=dict)
    refiner: str = ''
    reason: str = ''
    metadata: dict = field(default_factory=dict)


class SpeakerRefiner(ABC):
    name = 'abstract'

    @abstractmethod
    def resolve(self, evidence: Evidence) -> Resolution:
        pass

    def close(self):
        pass


def labeled_turns(turns):
    """Use exactly the label order used by quality.assign_speakers."""
    chosen = turns.get('exclusive') if turns.get('exclusive') is not None else turns['standard']
    labels = {}
    for _, _, speaker in sorted(chosen):
        labels.setdefault(speaker, f'Speaker {len(labels) + 1}')
    return {**turns, 'standard': [[a, b, labels.get(s, s)] for a, b, s in turns['standard']],
            'exclusive': None if turns.get('exclusive') is None else [[a, b, labels.get(s, s)] for a, b, s in chosen]}, labels


def temporal_scores(segment, turns):
    scores = defaultdict(float)
    if not valid_interval(segment):
        return {}
    selected = turns.get('exclusive') if turns.get('exclusive') is not None else turns['standard']
    for a, b, speaker in selected:
        scores[speaker] += max(0., min(segment['end'], b) - max(segment['start'], a))
    duration = segment['end'] - segment['start']
    return {s: min(1., v / duration) for s, v in scores.items() if v > 0}


def detect(segments, turns, config):
    indexed = defaultdict(list)
    for issue in diagnostics(segments):
        indexed[issue['segment_id']].append(issue['kind'])
    all_speakers = sorted({s for _, _, s in turns['standard']})
    for i, seg in enumerate(segments):
        temporal = temporal_scores(seg, turns)
        ranked = sorted(temporal.values(), reverse=True)
        best = ranked[0] if ranked else 0.
        margin = best - (ranked[1] if len(ranked) > 1 else 0.)
        word_conflict = any(w.get('speaker') != seg.get('speaker') for w in seg.get('words', []) if w.get('speaker'))
        reasons = [r for r in indexed[seg.get('id')] if r in ('unknown_speaker', 'speaker_ambiguity', 'overlap')]
        if seg.get('speaker') in (None, UNKNOWN): reasons.append('unknown_speaker')
        if best < .9: reasons.append('low_temporal_coverage')
        if margin < config.ambiguity_margin: reasons.append('close_temporal_candidates')
        if word_conflict: reasons.append('word_segment_disagreement')
        if valid_interval(seg) and seg['end'] - seg['start'] < .7 and best < .9:
            reasons.append('short_segment')
        # High temporal coverage and a single consistent speaker are never sent to AI.
        winner = max(temporal, key=temporal.get) if temporal else None
        if (best >= .9 and margin >= config.ambiguity_margin and not word_conflict
                and 'overlap' not in reasons and seg.get('speaker') == winner and winner is not None):
            continue
        if not reasons: continue
        if not valid_interval(seg): reasons.append('invalid_timing')
        candidates = sorted(set(temporal) | set(seg.get('speaker_candidates') or []))
        if best < .9: candidates = sorted(set(candidates) | set(all_speakers))
        if seg.get('speaker') not in (None, UNKNOWN): candidates = sorted(set(candidates) | {seg['speaker']})
        if not candidates: candidates = all_speakers
        n = config.context_turns
        yield Evidence(copy.deepcopy(seg), candidates, copy.deepcopy(segments[max(0, i-n):i]),
                       copy.deepcopy(segments[i+1:i+1+n]), temporal, sorted(set(reasons)))


def ranked_resolution(scores, refiner, reason, metadata=None):
    if not scores or any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in scores.values()):
        return Resolution(refiner=refiner, reason='invalid_or_missing_scores')
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return Resolution(ranked[0][0], ranked[0][1], scores, refiner, reason, metadata or {})


def accepted(result, evidence, config, semantic=False):
    if result.speaker not in evidence.candidates or result.speaker == UNKNOWN:
        return False, 'abstain'
    threshold = max(config.change_confidence, config.semantic_confidence if semantic else config.acoustic_confidence)
    others = [v for s, v in result.scores.items() if s != result.speaker]
    if result.confidence < threshold or result.confidence - max(others, default=0.) < config.decision_margin:
        return False, 'below_threshold_or_margin'
    if semantic:
        # Text alone cannot establish voice identity; require independent support.
        acoustic = evidence.acoustic.get('scores', {})
        strongest = max(acoustic, key=acoustic.get) if acoustic else None
        if strongest and acoustic[strongest] >= config.acoustic_confidence and strongest != result.speaker:
            return False, 'acoustic_semantic_conflict'
        if evidence.temporal.get(result.speaker, 0.) <= 0 and acoustic.get(result.speaker, 0.) < config.acoustic_confidence:
            return False, 'no_independent_speaker_support'
    return True, 'accepted'


def refine(segments, turns, refiner, config, prior=None, semantic=False):
    """Frozen baseline context; conservative acceptance, complete bounded decision log."""
    output = copy.deepcopy(segments)
    previous = {d['segment_id']: d for d in (prior or [])}
    decisions = []
    by_id = {s['id']: s for s in output}
    try:
        for evidence in detect(segments, turns, config):
            old = previous.get(evidence.segment['id'])
            if old and old.get('accepted'):
                continue
            if old: evidence.acoustic = old.get('evidence', {}).get('acoustic', {})
            try:
                result = refiner.resolve(evidence)
                if not isinstance(result, Resolution): raise ValueError('Refiner must return Resolution')
                if not math.isfinite(result.confidence) or not 0 <= result.confidence <= 1: raise ValueError('Invalid confidence')
                if result.scores and any(not math.isfinite(v) or not 0 <= v <= 1 for v in result.scores.values()): raise ValueError('Invalid candidate score')
                if set(result.scores) - (set(evidence.candidates) | {UNKNOWN}): raise ValueError('Unexpected score candidate')
                accept, policy = accepted(result, evidence, config, semantic)
            except Exception as exc:
                result = Resolution(refiner=refiner.name, reason='refiner_failed', metadata={'error': str(exc)[:500]})
                accept, policy = False, 'fallback_original'
            original = evidence.segment.get('speaker')
            final = result.speaker if accept else original
            changed = final != original
            if changed:
                target = by_id[evidence.segment['id']]
                target['speaker'] = final
                for word in target.get('words', []): word['speaker'] = final
            provenance = {'temporal': evidence.temporal, 'acoustic': evidence.acoustic,
                          'semantic': asdict(result) if semantic else {}}
            if not semantic: provenance['acoustic'] = asdict(result)
            decisions.append({'segment_id': evidence.segment['id'], 'start': evidence.segment.get('start'),
                'end': evidence.segment.get('end'), 'text': evidence.segment.get('text'),
                'original': original, 'final': final, 'changed': changed, 'accepted': accept,
                'refiner': result.refiner, 'confidence': result.confidence, 'scores': result.scores,
                'proposal': result.speaker, 'reason': result.reason, 'policy': policy,
                'ambiguity': evidence.reasons, 'threshold': max(config.change_confidence, config.semantic_confidence if semantic else config.acoustic_confidence),
                'margin_threshold': config.decision_margin, 'metadata': result.metadata, 'evidence': provenance})
    finally:
        refiner.close()
    return output, decisions


class AcousticSpeakerResolver(SpeakerRefiner):
    name = 'pyannote_acoustic'

    def __init__(self, embed, centroids, waveform, sample_rate, config):
        self.embed, self.centroids, self.waveform = embed, centroids, waveform
        self.sample_rate, self.config = sample_rate, config

    def resolve(self, evidence):
        import numpy as np
        seg = evidence.segment
        if not valid_interval(seg) or 'invalid_timing' in evidence.reasons:
            return Resolution(refiner=self.name, reason='invalid_timing')
        if 'overlap' in evidence.reasons or len(evidence.temporal) > 1:
            return Resolution(refiner=self.name, reason='mixed_speaker_window')
        duration = seg['end'] - seg['start']
        if duration < self.config.min_audio:
            return Resolution(refiner=self.name, reason='audio_too_short')
        center = (seg['start'] + seg['end']) / 2
        a, b = max(seg['start'], center-self.config.audio_window/2), min(seg['end'], center+self.config.audio_window/2)
        crop = self.waveform[:, int(a*self.sample_rate):int(b*self.sample_rate)]
        if crop.shape[-1] < self.sample_rate*self.config.min_audio:
            return Resolution(refiner=self.name, reason='audio_too_short')
        vector = np.asarray(self.embed(crop.unsqueeze(0)))[0]
        norm = np.linalg.norm(vector)
        if not np.isfinite(vector).all() or norm <= 0:
            return Resolution(refiner=self.name, reason='invalid_embedding')
        scores = {}
        for speaker in evidence.candidates:
            centroid = self.centroids.get(speaker)
            if centroid is None: continue
            centroid = np.asarray(centroid)
            den = norm*np.linalg.norm(centroid)
            if den > 0 and np.isfinite(centroid).all():
                scores[speaker] = float(np.clip(np.dot(vector, centroid)/den, 0., 1.))
        return ranked_resolution(scores, self.name, 'acoustic_embedding_similarity',
                                 {'window': [a, b], 'score_kind': 'cosine_similarity_not_probability'})


class FrozenAcousticResolver(SpeakerRefiner):
    name = 'pyannote_acoustic'

    def __init__(self, decisions):
        self.observations = {d['segment_id']: d['evidence']['acoustic'] for d in decisions}

    def resolve(self, evidence):
        saved = self.observations.get(evidence.segment['id'])
        if saved is None: raise ValueError('Acoustic observation missing; new preparation required')
        return Resolution(**saved)
