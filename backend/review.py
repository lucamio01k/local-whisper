"""Version-aware review and explicit ASR proposals; no writes to source audio."""
import copy
import json
import math
import shutil
import subprocess
import uuid
from pathlib import Path
from fastapi import BackgroundTasks, HTTPException
from backend.storage import LOCK, active, atomic_json, legacy_version, read_revision
from backend.quality import diagnostics, valid_interval, speaker_for


def units(segments):
    result = []
    for seg in segments:
        words = seg.get('words') or []
        if words and all(valid_interval(w) and not w.get('timing_issue') for w in words):
            result.extend({'start': w['start'], 'end': w['end'], 'text': w['word'], 'speaker': seg.get('speaker'), 'words': [copy.deepcopy(w)]} for w in words)
        else:
            result.append(copy.deepcopy(seg))
    return result


def numbered(segments):
    for i, seg in enumerate(segments, 1):
        seg['id'] = i
    return segments


def register_review_routes(app, ctx):
    def get(job_id):
        if job_id not in ctx.jobs:
            raise HTTPException(404, 'Job non trovato')
        return ctx.jobs[job_id]

    def proposal_path(job_id, proposal_id):
        get(job_id)
        if not proposal_id.isalnum():
            raise HTTPException(400, 'Proposta non valida')
        path = ctx._job_dir(job_id) / 'proposals' / (proposal_id + '.json')
        if not path.exists():
            raise HTTPException(404, 'Proposta non trovata')
        return path

    def publish(job_id, segments, reason, metadata=None):
        old = copy.deepcopy(ctx.jobs[job_id])
        try:
            job = ctx.jobs[job_id]
            if metadata:
                # Versioned processing data only; do not rename the recording.
                for k in (*ctx.QUALITY_FIELDS, 'model', 'transcription_backend', 'language', 'transcription_options', 'metrics', 'diarization_ran'):
                    if k in metadata: job[k] = copy.deepcopy(metadata[k])
            job['segments'] = numbered(copy.deepcopy(segments))
            job['_revision_reason'] = reason
            ctx.save_job_to_disk(job_id)
            return {'version': job['version'], 'segments': job['segments'], 'diagnostics': job['diagnostics']}
        except Exception:
            ctx.jobs[job_id] = old
            raise

    @app.get('/api/jobs/{job_id}/revisions')
    def revisions(job_id: str):
        job = get(job_id)
        rows = []
        for path in (ctx._job_dir(job_id) / 'revisions').glob('*.json'):
            data = json.loads(path.read_text())
            rows.append({k: data.get(k) for k in ('version', 'parent', 'created_at', 'reason')})
        return {'active': job.get('version'), 'revisions': sorted(rows, key=lambda x: x.get('created_at', ''), reverse=True)}

    @app.post('/api/jobs/{job_id}/restore')
    def restore(job_id: str, body: dict):
        with LOCK:
            ctx._check_edit_version(job_id, body.get('version'))
            try:
                record = read_revision(ctx._job_dir(job_id), body.get('target_version'))
            except (ValueError, FileNotFoundError):
                raise HTTPException(404, 'Versione non trovata')
            return publish(job_id, record['segments'], 'restore:' + record['version'], record.get('metadata'))

    @app.patch('/api/jobs/{job_id}/segments/{segment_id}')
    def edit_segment(job_id: str, segment_id: int, body: dict):
        with LOCK:
            job = ctx._check_edit_version(job_id, body.get('version'))
            segments = copy.deepcopy(job['segments'])
            index = next((i for i, s in enumerate(segments) if s.get('id') == segment_id), None)
            if index is None:
                raise HTTPException(404, 'Segmento non trovato')
            seg = segments[index]
            if 'text' in body and body['text'] != seg['text']:
                if not isinstance(body['text'], str) or not body['text'].strip() or len(body['text']) > 20000:
                    raise HTTPException(400, 'Testo non valido')
                seg['text'] = body['text']
                seg['words'] = []
                seg['alignment_issue'] = True
            if 'speaker' in body:
                speaker = body['speaker']
                if speaker is not None and (not isinstance(speaker, str) or not speaker.strip() or len(speaker) > 120):
                    raise HTTPException(400, 'Speaker non valido')
                seg['speaker'] = speaker
                seg['speaker_candidates'] = [speaker] if speaker else []
                for word in seg.get('words', []):
                    word['speaker'] = speaker
                    word['speaker_candidates'] = [speaker] if speaker else []
            if 'start' in body or 'end' in body:
                try:
                    start, end = float(body.get('start', seg['start'])), float(body.get('end', seg['end']))
                except (TypeError, ValueError):
                    raise HTTPException(400, 'Tempi non validi')
                if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or end > job['duration']:
                    raise HTTPException(400, 'Tempi fuori dalla durata audio')
                if start != seg['start'] or end != seg['end']:
                    seg.update(start=start, end=end, words=[], alignment_issue=True)
            if 'split_word' in body:
                split = body['split_word']
                words = seg.get('words') or []
                if not isinstance(split, int) or not 0 < split < len(words) or not all(valid_interval(w) and not w.get('timing_issue') for w in words) or any(a['end'] > b['start'] for a,b in zip(words,words[1:])):
                    raise HTTPException(400, 'Divisione disponibile solo su confini parola validi')
                halves = []
                for group in (words[:split], words[split:]):
                    halves.append({**seg, 'words': group, 'start': group[0]['start'], 'end': group[-1]['end'], 'text': ''.join(w['word'] for w in group)})
                segments[index:index+1] = halves
            return publish(job_id, segments, 'manual_edit')

    @app.get('/api/jobs/{job_id}/reference')
    def export_reference(job_id: str):
        with LOCK:
            job = get(job_id)
            return {'verified': False, 'version': job.get('version'),
                    'text': ' '.join(s['text'].strip() for s in job['segments']),
                    'turns': [[s['start'], s['end'], s.get('speaker')] for s in job['segments']],
                    'words': [dict(w, speaker=s.get('speaker')) for s in job['segments'] for w in s.get('words', [])],
                    'terms': [], 'instructions': 'Verificare audio e annotazioni prima di impostare verified=true.'}

    def compute_proposal(job_id, proposal_id, work_id, source, body):
        path = proposal_path(job_id, proposal_id)
        proposal = json.loads(path.read_text())
        try:
            audio = ctx._find_audio_file(job_id)
            if not audio:
                raise RuntimeError('Audio originale non trovato')
            work = ctx._job_dir(work_id)
            work.mkdir(parents=True, exist_ok=True)
            full = proposal['full']
            duration = ctx._audio_duration(audio)
            clip_start = 0 if full else max(0, proposal['start'] - 5)
            clip_end = duration if full else min(duration, proposal['end'] + 5)
            normalized = work / 'audio.wav'
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', str(clip_start), '-i', str(audio), '-t', str(clip_end - clip_start), '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(normalized)], check=True, capture_output=True)
            cfg = ctx.load_config()
            ctx._run_transcription(work_id, str(normalized), body.get('model_name') or source.get('model') or 'large-v3-turbo',
                                   source.get('language'), full and bool(cfg.get('hf_token')), cfg.get('hf_token', ''),
                                   source.get('expected_speakers'), source.get('diarization_model') or 'community-1', 'balanced',
                                   'auto' if full else 'off', source.get('diarization_device') or 'auto', 'quality',
                                   source.get('transcription_backend') or 'whisper_cpp')
            result = ctx.jobs[work_id]
            if result.get('cancel_requested'):
                raise RuntimeError('Elaborazione annullata')
            if result['status'] != 'done' or result.get('diarization_error'):
                raise RuntimeError(result.get('error') or result.get('diarization_error') or 'Elaborazione annullata')
            replacement = copy.deepcopy(result['segments'])
            if not full:
                replacement = units(replacement)
                for seg in replacement:
                    seg['start'] += clip_start
                    seg['end'] += clip_start
                    for word in seg.get('words', []):
                        if word.get('start') is not None: word['start'] += clip_start
                        if word.get('end') is not None: word['end'] += clip_start
                # Crop only at actual word boundaries. Unaligned chunks are not guessed.
                overlapping = [s for s in replacement if s['end'] > proposal['start'] and s['start'] < proposal['end']]
                if any(not s.get('words') and (s['start'] < proposal['start'] or s['end'] > proposal['end']) for s in overlapping):
                    raise RuntimeError('Allineamento insufficiente al confine: usa rielaborazione completa')
                replacement = [s for s in overlapping if proposal['start'] <= (s['start'] + s['end']) / 2 <= proposal['end']]
                current_turns = [(s['start'], s['end'], s['speaker']) for s in source['segments'] if s.get('speaker')]
                for seg in replacement:
                    seg['start'] = max(seg['start'], proposal['start'])
                    seg['end'] = min(seg['end'], proposal['end'])
                    seg['speaker'] = speaker_for(seg['start'], seg['end'], current_turns)
                    for word in seg.get('words', []):
                        word.update(start=seg['start'], end=seg['end'], speaker=seg['speaker'])
            if not replacement:
                raise RuntimeError('Nessun testo riconosciuto: originale conservato')
            proposal.update(status='ready', segments=numbered(replacement), diagnostics=diagnostics(replacement),
                            metadata={k: result.get(k) for k in (*ctx.QUALITY_FIELDS, 'model', 'transcription_options', 'metrics', 'diarization_ran')})
        except Exception as exc:
            proposal.update(status='error', error=str(exc))
        finally:
            atomic_json(path, proposal)
            ctx.jobs.pop(work_id, None)
            shutil.rmtree(ctx._job_dir(work_id), ignore_errors=True)

    @app.post('/api/jobs/{job_id}/proposals')
    def create_proposal(job_id: str, body: dict, background_tasks: BackgroundTasks):
        with LOCK:
            source = copy.deepcopy(ctx._check_edit_version(job_id, body.get('version')))
            model = body.get('model_name') or source.get('model') or 'large-v3-turbo'
            ctx._validate_model_name(model)
            if source.get('transcription_backend') == 'whisper_cpp' and not ctx._whisper_cpp_model_path(model):
                raise HTTPException(400, 'Modello non scaricato: installalo dalla sezione Modelli')
            full = body.get('full', False)
            if not isinstance(full, bool): raise HTTPException(400, 'Valore full non valido')
            if full:
                start, end = 0., source.get('duration') or 0.
            else:
                try: start, end = float(body['start']), float(body['end'])
                except (KeyError, TypeError, ValueError): raise HTTPException(400, 'Intervallo non valido')
                if not math.isfinite(start) or not math.isfinite(end) or start < 0 or not 0 < end - start <= 60 or end > source['duration']:
                    raise HTTPException(400, 'Intervallo massimo 60 secondi entro durata audio')
                selected = [s for s in units(source['segments']) if s['end'] > start and s['start'] < end]
                if not selected: raise HTTPException(400, 'Nessun segmento selezionato')
                start, end = min(s['start'] for s in selected), max(s['end'] for s in selected)
                if end - start > 60:
                    raise HTTPException(400, 'Segmento legacy senza confini parola: usa rielaborazione completa')
            glossary = body.get('glossary', source.get('glossary') or '')
            ctx._validate_quality_options('words', glossary)
            proposal_id, work_id = uuid.uuid4().hex, '_work' + uuid.uuid4().hex
            record = {'id': proposal_id, 'base_version': source['version'], 'status': 'queued',
                      'start': start, 'end': end, 'full': full, 'work_id': work_id,
                      'original': [s for s in units(source['segments']) if s['end'] > start and s['start'] < end]}
            atomic_json(ctx._job_dir(job_id) / 'proposals' / (proposal_id + '.json'), record)
            ctx.jobs[work_id] = {**copy.deepcopy(source), 'segments': [], 'status': 'queued', 'version': None,
                                 'metrics': {}, 'turns': None, 'progress': 0, 'message': 'In coda…', 'cancelable': True, 'glossary': glossary, 'diarization_precision': 'words',
                                 'cancel_requested': False, 'pause_requested': False, 'diarization_ran': False}
            background_tasks.add_task(compute_proposal, job_id, proposal_id, work_id, source, {**body, 'model_name': model})
            return record

    @app.get('/api/jobs/{job_id}/proposals/{proposal_id}')
    def get_proposal(job_id: str, proposal_id: str):
        path = proposal_path(job_id, proposal_id)
        data = json.loads(path.read_text())
        if data['status'] == 'queued':
            worker = ctx.jobs.get(data.get('work_id'))
            if worker:
                data.update(status='running' if worker['status'] != 'queued' else 'queued', message=worker.get('message'), progress=worker.get('progress'))
            else:
                data.update(status='error', error='Elaborazione interrotta dal riavvio; originale conservato')
        return data

    @app.post('/api/jobs/{job_id}/proposals/{proposal_id}/cancel')
    def cancel_proposal(job_id: str, proposal_id: str):
        data = json.loads(proposal_path(job_id, proposal_id).read_text())
        worker = ctx.jobs.get(data.get('work_id'))
        if worker: worker.update(cancel_requested=True, pause_requested=False)
        return {'ok': True}

    @app.post('/api/jobs/{job_id}/proposals/{proposal_id}/apply')
    def apply_proposal(job_id: str, proposal_id: str, body: dict):
        with LOCK:
            job = ctx._check_edit_version(job_id, body.get('version'))
            proposal = json.loads(proposal_path(job_id, proposal_id).read_text())
            if proposal['status'] != 'ready': raise HTTPException(409, 'Proposta non pronta')
            if job['version'] != proposal['base_version']: raise HTTPException(409, 'Proposta basata su versione precedente')
            if proposal['full']:
                segments = proposal['segments']
                metadata = proposal.get('metadata')
            else:
                existing = units(job['segments'])
                segments = [s for s in existing if s['end'] <= proposal['start']] + proposal['segments'] + [s for s in existing if s['start'] >= proposal['end']]
                metadata = None
            return publish(job_id, segments, 'proposal:' + proposal_id, metadata)
