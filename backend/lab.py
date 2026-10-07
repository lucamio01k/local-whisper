"""Persistent, isolated Benchmark/Lab API and sequential process supervisor."""
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from backend.storage import atomic_json, digest_file
from backend.refinement import RefinementConfig, REFINEMENT_VERSION
from backend.lab_dataset import finalize, load_dataset, parse_rttm, SCENARIOS
from backend.lab_models import availability

ROOT = Path(__file__).resolve().parents[1]
LAB_DIR = ROOT/'lab-data'
VARIANTS = {'baseline': 'Baseline', 'acoustic': 'Pyannote + Acoustic', 'acoustic_laya': 'Pyannote + Acoustic + Laya',
            'acoustic_gliner': 'Pyannote + Acoustic + GLiNER'}


def path_for(run_id):
    if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
        raise ValueError('Invalid run ID')
    return LAB_DIR/'runs'/run_id


def read_record(run_id):
    return json.loads((path_for(run_id)/'record.json').read_text())


def version_info():
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        diff = subprocess.run(['git', 'diff', 'HEAD'], cwd=ROOT, capture_output=True, timeout=5, check=True).stdout
        import hashlib
        # Include untracked implementation files, excluding runtime/ignored data.
        files = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard'], cwd=ROOT, capture_output=True, text=True, timeout=5, check=True).stdout.splitlines()
        hasher = hashlib.sha256(diff)
        for name in sorted(files):
            hasher.update(name.encode()); hasher.update((ROOT/name).read_bytes())
        dirty = bool(diff or files)
        fingerprint = hasher.hexdigest()
    except (OSError, subprocess.SubprocessError):
        commit, dirty, fingerprint = None, None, None
    versions = {}
    for name in ('torch', 'pyannote.audio', 'faster-whisper', 'laya', 'gliner2'):
        try: versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: versions[name] = None
    return {'commit': commit, 'dirty': dirty, 'working_tree_sha256': fingerprint,
            'refinement_version': REFINEMENT_VERSION, 'packages': versions}


def pipeline_config(body, context):
    cfg = context.load_config()
    settings = body.get('pipeline') or {}
    allowed = {'model', 'language', 'expected_speakers', 'diarization_precision', 'performance_profile', 'diarization_device', 'transcription_backend'}
    if set(settings)-allowed: raise ValueError('Unknown pipeline setting')
    model = settings.get('model', cfg.get('default_model', 'large-v3-turbo'))
    if model not in context.WHISPER_MODELS: raise ValueError('Lab baseline requires Whisper model')
    backend = settings.get('transcription_backend') or context._backend_for_model(model, cfg.get('whisper_backend_preference', 'auto'))
    if backend not in ('whisper_cpp', 'faster_whisper'): raise ValueError('Lab requires Whisper backend')
    expected = settings.get('expected_speakers')
    if expected is not None and (type(expected) is not int or not 1 <= expected <= 20): raise ValueError('Expected speakers: integer 1–20')
    precision = settings.get('diarization_precision', cfg.get('diarization_precision', 'segments'))
    profile = settings.get('performance_profile', cfg.get('performance_profile', 'balanced'))
    device = settings.get('diarization_device', cfg.get('diarization_device', 'auto'))
    if precision not in ('segments', 'words') or profile not in context.PERFORMANCE_PROFILES or device not in context.DIARIZATION_DEVICES:
        raise ValueError('Invalid pipeline precision/profile/device')
    language = settings.get('language') or None
    if language and (not isinstance(language, str) or len(language) > 10): raise ValueError('Invalid language')
    return {'model': model, 'transcription_backend': backend, 'language': language, 'diarize': True,
            'expected_speakers': expected, 'diarization_model': 'community-1', 'diarization_mode': 'conservative',
            'diarization_start': 'auto', 'diarization_device': device, 'performance_profile': profile,
            'diarization_precision': precision, 'glossary': cfg.get('glossary', '')}


def prepare_run(body, context):
    from backend.lab_suite import select, resources
    datasets, suite = select(LAB_DIR, body)
    dataset = datasets[0]
    mode = body.get('mode', 'frozen')
    if mode not in ('frozen', 'complete'): raise ValueError('Mode must be frozen/complete')
    repetitions = body.get('repetitions', 1)
    if type(repetitions) is not int or not 1 <= repetitions <= 3: raise ValueError('Repetitions: integer 1–3')
    if mode == 'frozen' and repetitions != 1: raise ValueError('Frozen mode uses one preparation; repetitions require complete mode')
    resource = resources(body.get('resource_profile', 'light'))
    requested_snapshot=body.get('snapshot_key')
    if requested_snapshot and mode!='frozen': raise ValueError('Snapshot only available in frozen mode')
    if requested_snapshot and (not isinstance(requested_snapshot,str) or len(requested_snapshot)!=64 or any(c not in '0123456789abcdef' for c in requested_snapshot) or len(datasets)!=1):
        raise ValueError('Explicit snapshot: 64 hex characters, single clip only')
    budget = body.get('budget_seconds',1800)
    if not isinstance(budget,(int,float)) or not 1 <= budget <= 21600: raise ValueError('Execution budget: 1–21600 seconds')
    params = body.get('parameters') or {}
    from dataclasses import asdict
    config = asdict(RefinementConfig(**params))
    variants = body.get('variants', ['baseline'])
    if not isinstance(variants, list) or not variants or any(v not in VARIANTS for v in variants): raise ValueError('Unknown/empty pipeline configuration')
    # Baseline always first, before any experimental inference or modification.
    variants = ['baseline'] + [v for v in VARIANTS if v != 'baseline' and v in variants]
    if mode=='complete' and len(variants)>2: raise ValueError('Complete verification: choose baseline and one finalist')
    notes = body.get('notes', '')
    if not isinstance(notes, str) or len(notes) > 2000: raise ValueError('Notes: maximum 2000 characters')
    pipeline = pipeline_config(body, context)
    record = {'id': uuid.uuid4().hex, 'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'queued',
              'dataset': dataset, 'datasets': datasets, 'suite': suite, 'selection':body.get('selection','small' if suite else 'complete'),
              'selection_seed':body.get('seed',42),'selection_split':body.get('split'),'selection_interval':body.get('interval'),
              'mode':mode, 'repetitions':repetitions, 'resources':resource, 'budget_seconds':budget, 'snapshot_key':requested_snapshot, 'variants': variants, 'parameters': config, 'pipeline': pipeline,
              'version': version_info(), 'models': {name: availability(name) for name in ('laya', 'gliner', 'diarizationlm')},
              'notes': notes, 'results': [], 'error': None, 'owner_pid':os.getpid()}
    atomic_json(path_for(record['id'])/'record.json', record)
    return record


def sample_tree(pid):
    """Sample summed RSS of process tree (not total unified/GPU memory)."""
    try:
        result = subprocess.run(['ps', '-axo', 'pid=,ppid=,rss='], capture_output=True, text=True, timeout=2, check=True)
        rows = [tuple(map(int, line.split())) for line in result.stdout.splitlines() if len(line.split()) == 3]
        children = {pid}
        changed = True
        while changed:
            old = len(children)
            children.update(p for p, parent, _ in rows if parent in children)
            changed = old != len(children)
        memory = sum(rss*1024 for p, _, rss in rows if p in children)
        return memory, children
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, {pid}


def supervise(spec, directory, context, job):
    output = directory/'result.json'
    atomic_json(directory/'request.json', spec)
    token = os.environ.get('HF_TOKEN') or context.load_config().get('hf_token', '')
    env = {**os.environ, 'LOCAL_WHISPER_LAB_WORKER': '1', 'HF_TOKEN': token,
           'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'OMP_NUM_THREADS': str(spec.get('resources',{}).get('threads',2)), 'MKL_NUM_THREADS': str(spec.get('resources',{}).get('threads',2))}
    proc = None
    started, peak, sampled = time.monotonic(), 0, False
    children = set()
    try:
        with (directory/'worker.log').open('w') as log:
            proc = subprocess.Popen([sys.executable, '-m', 'backend.lab_worker', '--input', str(directory/'request.json'), '--output', str(output)],
                                    cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=(os.name == 'posix'))
            context.job_processes[job['id']] = proc
            while proc.poll() is None:
                memory, descendants = sample_tree(proc.pid)
                children = descendants
                if memory is not None:
                    sampled = True
                    peak = max(peak, memory)
                if spec.get('run_id') and read_record(spec['run_id']).get('cancel_requested'): job['cancel_requested']=True
                if job.get('cancel_requested'): raise RuntimeError('Benchmark canceled')
                if time.monotonic() >= spec.get('deadline', started+1800): raise TimeoutError('Total benchmark budget reached; completed cases preserved')
                # RSS budget leaves room for OS/user apps; GPU/driver allocations not fully covered.
                if memory is not None and memory > spec.get('resources',{}).get('rss_gib',8)*1024**3: raise RuntimeError('Benchmark RSS memory budget exceeded')
                from backend.lab_download import DiskBudget
                DiskBudget(LAB_DIR).reserve(0)
                time.sleep(.25)
            if job.get('cancel_requested'): raise RuntimeError('Benchmark canceled')
            if not output.exists(): raise RuntimeError('Lab worker failed; see worker.log')
            result = json.loads(output.read_text())
            if proc.returncode or result['status'] == 'error': raise RuntimeError(result.get('error') or 'Lab worker failed')
            result['performance']['wall_seconds_including_scoring'] = time.monotonic()-started
            if sampled:
                result['performance'].update(peak_ram_bytes=max(peak,result['performance']['peak_ram_bytes']) if spec.get('snapshot') else peak,
                                             ram_method='max_preparation_and_sampled_replay_tree_rss' if spec.get('snapshot') else 'sampled_process_tree_rss_250ms')
            atomic_json(output, result)
            return result
    finally:
        if proc is not None and proc.poll() is None:
            context._terminate_process(proc)
            try: proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                context._terminate_process(proc, force=True)
                proc.wait(timeout=3)
        context.job_processes.pop(job['id'], None)
        # Normally the worker's SIGTERM handler cleans its separate ASR sessions.
        # Kill only still-existing descendants captured while worker was alive.
        if proc is not None and children:
            import signal
            for pid in children-{proc.pid if proc else None}:
                try:
                    if os.getpgid(pid) == pid: os.killpg(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError): pass


def execute_run(run_id, context):
    directory = path_for(run_id)
    if read_record(run_id)['status'] != 'queued': return
    job_id = 'lab-'+run_id
    context.jobs[job_id] = {'id': job_id, 'segments': [], 'status': 'queued', 'cancel_requested': False}
    job = context.jobs[job_id]
    @context.heavy_job
    def execute(_job_id):
        record = read_record(run_id)
        record['status'] = 'running'
        atomic_json(directory/'record.json', record)
        deadline = time.monotonic()+record.get('budget_seconds',1800)
        record['preparations'] = []
        from backend.lab_suite import snapshot_key, read_snapshot, seal_snapshot
        from backend.lab_metrics import aggregate, divergences
        from backend.lab_download import DiskBudget
        implementation = {name:digest_file(ROOT/name) for name in ('backend/engine.py','backend/asr_worker.py','backend/main.py','backend/quality.py','backend/refinement.py')}
        implementation['packages'] = record['version']['packages']
        from huggingface_hub.constants import HF_HUB_CACHE
        cache=Path(HF_HUB_CACHE)
        implementation['model_revisions']={str(p.relative_to(cache)):p.read_text().strip() for pattern in ('models--pyannote--*/refs/*','models--*whisper*/refs/*') for p in cache.glob(pattern) if p.is_file()}
        if record['pipeline']['transcription_backend'] == 'whisper_cpp':
            model_path = context._whisper_cpp_model_path(record['pipeline']['model'])
            if model_path: implementation['asr_model_sha256'] = digest_file(model_path)
            binary=context._whisper_cpp_binary()
            if binary: implementation['asr_binary_sha256']=digest_file(binary)
        try:
            implementation['ffmpeg']=subprocess.run(['ffmpeg','-version'],capture_output=True,text=True,timeout=5,check=True).stdout.splitlines()[0]
        except (OSError,subprocess.SubprocessError,IndexError): implementation['ffmpeg']=None
        try:
            for dataset in record.get('datasets',[record['dataset']]):
                for repetition in range(record.get('repetitions',1)):
                    pipeline = {**record['pipeline']}
                    if dataset.get('language') in ('it','en') and not pipeline.get('language'): pipeline['language']=dataset['language']
                    spec = {'root':str(LAB_DIR),'dataset_id':dataset['id'],'parameters':record['parameters'],
                            'pipeline':pipeline,'resources':record.get('resources',{'threads':2,'rss_gib':8}),'deadline':deadline,'run_id':run_id}
                    if time.monotonic()>=deadline: raise TimeoutError('Total benchmark budget reached')
                    DiskBudget(LAB_DIR).reserve(int(dataset['duration']*32000*6)+2_000_000)
                    prepared = None
                    if record.get('mode','complete')=='frozen':
                        key=snapshot_key(dataset,pipeline,record['parameters'],spec['resources'],implementation)
                        if record.get('snapshot_key') and record['snapshot_key']!=key: raise ValueError('Snapshot config changed; new preparation required')
                        folder=LAB_DIR/'snapshots'/key
                        snapshot=folder/'snapshot.json'
                        if snapshot.exists():
                            read_snapshot(snapshot,key)
                            record['preparations'].append({'dataset_id':dataset['id'],'key':key,'reused':True,'seconds':0})
                        else:
                            folder.mkdir(parents=True,exist_ok=True)
                            job['message']='Preparation · '+dataset['title']
                            prepared=supervise({**spec,'variant':'baseline','prepare_snapshot':True},folder,context,job)
                            seal_snapshot(folder,prepared,key)
                            record['preparations'].append({'dataset_id':dataset['id'],'key':key,'reused':False,'seconds':prepared['performance']['total_seconds']})
                        spec.update(snapshot=str(snapshot),snapshot_key=key)
                    baseline_metrics=None
                    for variant in record['variants']:
                        if time.monotonic()>=deadline: raise TimeoutError('Total benchmark budget reached')
                        job['message']=dataset['title']+' · '+VARIANTS[variant]
                        case_key = variant if len(record.get('datasets',[]))<=1 and record.get('repetitions',1)==1 else dataset['id']+'-'+str(repetition)+'-'+variant
                        case=directory/case_key; case.mkdir(exist_ok=True)
                        if prepared is not None and variant=='baseline':
                            result=prepared; atomic_json(case/'result.json',result)
                        else: result=supervise({**spec,'variant':variant},case,context,job)
                        summary={k:result[k] for k in ('status','metrics','performance','warnings')}
                        if baseline_metrics is None: baseline_metrics=result['metrics']
                        _,_,reference=load_dataset(LAB_DIR,dataset['id'])
                        mapping=result.get('baseline_speaker_mapping',baseline_metrics.get('speaker_mapping'))
                        outcomes=divergences(reference,result['baseline_segments'],result['segments'],mapping,result['metrics'].get('speaker_mapping'))
                        summary.update(variant=variant,label=VARIANTS[variant],case_key=case_key,dataset_id=dataset['id'],repetition=repetition+1,
                            language=dataset.get('language','unknown'),scenario=dataset['scenario'],split=dataset.get('split','user'),original=dataset.get('original',True),
                            changes=sum(d['changed'] for d in result['decisions']),analyzed=len({d['segment_id'] for d in result['decisions']}),
                            outcomes={name:sum(r['outcome']==name for r in outcomes) for name in ('correct_correction','wrong_correction','new_error','remaining_error','unscored')},
                            abstentions=len({d['segment_id'] for d in result['decisions']} - {d['segment_id'] for d in result['decisions'] if d.get('accepted',False)}),
                            delta={name:result['metrics'][name]-baseline_metrics[name] if result['metrics'][name] is not None and baseline_metrics[name] is not None else None for name in ('der','wer','wder')})
                        record['results'].append(summary)
                        record['aggregates']=group_results(record['results'])
                        atomic_json(directory/'record.json',record)
            record['status']='degraded' if any(r['status']!='done' for r in record['results']) else 'done'
        except BaseException as exc:
            if isinstance(exc,(KeyboardInterrupt,SystemExit)): job['cancel_requested']=True
            record.update(status='canceled' if job.get('cancel_requested') else 'partial' if isinstance(exc,TimeoutError) else 'error',error=str(exc)[:1000])
        finally:
            record['budget_elapsed_seconds']=record.get('budget_seconds',1800)-(deadline-time.monotonic())
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
            atomic_json(directory/'record.json', record)
    try:
        execute(job_id)
        if job.get('stage') == 'canceled' and read_record(run_id)['status'] == 'queued':
            record = read_record(run_id); record['status'] = 'canceled'; atomic_json(directory/'record.json', record)
    finally:
        context.jobs.pop(job_id, None)


def group_results(rows):
    from backend.lab_metrics import aggregate
    groups={}
    for row in rows:
        key=(row['variant'],row['language'],row['scenario'],row['split'],row['original'])
        groups.setdefault(key,[]).append(row)
    return [{'variant':k[0],'language':k[1],'scenario':k[2],'split':k[3],'original':k[4],**aggregate(v)} for k,v in groups.items()]


def report_csv(record):
    import csv
    import io
    buffer=io.StringIO(); fields=['run_id','mode','selection','config_json','provenance_json','dataset_id','variant','repetition','language','scenario','split','original','status','wer','der','wder','pyannote_der','reference_words','word_errors','aligned_correct_words','speaker_word_errors','total_seconds','refiner_seconds','rtf','peak_ram_bytes']
    writer=csv.DictWriter(buffer,fieldnames=fields); writer.writeheader()
    for row in record['results']:
        dataset=next((d for d in record.get('datasets',[record['dataset']]) if d['id']==row.get('dataset_id',record['dataset']['id'])),record['dataset'])
        values={**row,**row['metrics'],**row['performance'],'run_id':record['id'],'mode':record.get('mode','complete'),'selection':record.get('selection','complete'),
                'config_json':json.dumps({k:record.get(k) for k in ('pipeline','parameters','resources','version','budget_seconds','selection_seed','selection_split','selection_interval','scoring_revision','evaluation_version')}),
                'provenance_json':json.dumps(dataset)}
        writer.writerow({k:values.get(k) for k in fields})
    return buffer.getvalue()


def register_lab_routes(app, context):
    state=LAB_DIR/'preparation.json'
    if state.exists():
        try:
            preparation=json.loads(state.read_text())
            if preparation.get('status')=='running' and preparation.get('owner_pid'):
                try: os.kill(preparation['owner_pid'],0)
                except ProcessLookupError:
                    preparation.update(status='interrupted',error='Preparation interrupted; resume retains completed clips/downloads')
                    atomic_json(state,preparation)
        except (OSError,ValueError,TypeError): pass
    # Restart recovery: prior worker processes have exited; never present stale success.
    for path in (LAB_DIR/'runs').glob('*/record.json'):
        try:
            record = json.loads(path.read_text())
            if record['status'] in ('queued', 'running'):
                if record.get('owner_pid'):
                    try:
                        os.kill(record['owner_pid'],0)
                        continue
                    except ProcessLookupError: pass
                    except PermissionError: continue
                record.update(status='interrupted', error='App restarted during benchmark; completed variants preserved')
                atomic_json(path, record)
        except (OSError, ValueError, KeyError): pass

    @app.get('/api/lab')
    def catalog():
        from dataclasses import asdict
        return {'variants': VARIANTS, 'models': {k: availability(k) for k in ('laya', 'gliner', 'diarizationlm')},
                'parameters': asdict(RefinementConfig()), 'scenarios': SCENARIOS,
                'pipeline': pipeline_config({}, context), 'privacy': 'Local datasets; explicit bounded public preparation',
                'resource_profiles': __import__('backend.lab_suite',fromlist=['RESOURCE_PROFILES']).RESOURCE_PROFILES}

    @app.get('/api/lab/suites')
    def suites():
        from backend.lab_sources import SOURCES
        from backend.lab_download import DiskBudget
        items=[json.loads(p.read_text()) for p in (LAB_DIR/'suites').glob('*.json')]
        state=LAB_DIR/'preparation.json'
        return {'suites':items,'sources':SOURCES,'preparation':json.loads(state.read_text()) if state.exists() else {'status':'not_prepared'},
                'used_bytes':DiskBudget(LAB_DIR).used(),'budget_bytes':2_000_000_000}

    @app.get('/api/lab/snapshots')
    def snapshots():
        from backend.lab_suite import read_snapshot
        items=[]
        for path in (LAB_DIR/'snapshots').glob('*/snapshot.json'):
            try:
                read_snapshot(path,path.parent.name)
                request=json.loads((path.parent/'request.json').read_text())
                items.append({'key':path.parent.name,'dataset_id':request['dataset_id'],'pipeline':request['pipeline'],
                              'parameters':request['parameters'],'resources':request.get('resources')})
            except (ValueError,OSError,KeyError): pass
        return items

    @app.post('/api/lab/prepare')
    def prepare(background_tasks: BackgroundTasks):
        from backend.lab_sources import prepare_public
        state=LAB_DIR/'preparation.json'
        if state.exists() and json.loads(state.read_text()).get('status')=='running': raise HTTPException(409,'Preparation already running')
        atomic_json(state,{'status':'running','owner_pid':os.getpid()})
        def work():
            try: prepare_public(LAB_DIR)
            except Exception as exc: atomic_json(state,{'status':'error','error':str(exc)[:1000]})
        background_tasks.add_task(work)
        return {'status':'running'}

    @app.post('/api/lab/selection')
    def preview(body: dict):
        from backend.lab_suite import select
        try:
            selected,suite=select(LAB_DIR,body)
            duration=sum(d['duration'] for d in selected)
            return {'datasets':selected,'duration':duration,'estimated_seconds':duration*4+len(selected)*20,
                    'estimate_note':'Rough estimate; model load and hardware affect cost; total budget enforced'}
        except (ValueError,TypeError,KeyError,OSError) as exc: raise HTTPException(400,str(exc)) from exc

    @app.get('/api/lab/datasets/{dataset_id}/audio')
    def audio(dataset_id: str):
        try:
            _,path,_=load_dataset(LAB_DIR,dataset_id)
            return FileResponse(path,headers={'Accept-Ranges':'bytes'})
        except (ValueError,OSError) as exc: raise HTTPException(404,str(exc)) from exc

    @app.get('/api/lab/datasets/{dataset_id}/reference')
    def reference(dataset_id: str):
        try: return load_dataset(LAB_DIR,dataset_id)[2]
        except (ValueError,OSError) as exc: raise HTTPException(404,str(exc)) from exc

    @app.get('/api/lab/runs/{run_id}/export')
    def export(run_id: str, format: str='json'):
        try: record=read_record(run_id)
        except (ValueError,OSError) as exc: raise HTTPException(404,str(exc)) from exc
        if format=='json': return Response(json.dumps(detail(run_id),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename={run_id}.json'})
        if format!='csv': raise HTTPException(400,'Export format json/csv')
        return Response(report_csv(record),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename={run_id}.csv'})

    @app.get('/api/lab/datasets')
    def datasets():
        return sorted([json.loads(p.read_text()) for p in (LAB_DIR/'datasets').glob('*/dataset.json')], key=lambda r: r['created_at'], reverse=True)

    @app.post('/api/lab/datasets')
    async def upload_dataset(audio: UploadFile = File(...), gold: UploadFile = File(None), diarization: UploadFile = File(None),
                             title: str = Form(''), scenario: str = Form(''), verified: bool = Form(False)):
        suffix = Path(audio.filename or '').suffix.lower()
        if suffix not in context.ALLOWED_AUDIO_SUFFIXES: raise HTTPException(400, 'Unsupported audio format')
        if len(title) > 200 or len(scenario) > 200: raise HTTPException(400, 'Title/scenario too long')
        directory = LAB_DIR/'datasets'/uuid.uuid4().hex
        directory.mkdir(parents=True)
        try:
            destination = directory/('audio'+suffix)
            size = 0
            with destination.open('wb') as handle:
                while chunk := await audio.read(context.UPLOAD_CHUNK_BYTES):
                    size += len(chunk)
                    from backend.lab_download import DiskBudget
                    DiskBudget(LAB_DIR).reserve(len(chunk))
                    if size > 2_000_000_000: raise ValueError('Lab audio exceeds disk budget')
                    handle.write(chunk)
            reference = {'verified': verified}
            for upload in (gold, diarization):
                if upload is None: continue
                data = await upload.read(10*1024*1024+1)
                if len(data) > 10*1024*1024: raise ValueError('Gold annotation exceeds 10 MB; split dataset')
                text = data.decode('utf-8-sig')
                ext = Path(upload.filename or '').suffix.lower()
                if ext == '.json':
                    incoming = json.loads(text)
                    if not isinstance(incoming, dict): raise ValueError('Gold JSON must be object')
                    reference.update(incoming)
                elif ext == '.rttm': reference['turns'] = parse_rttm(text)
                elif ext == '.txt' and upload is gold: reference['text'] = text
                else: raise ValueError('Gold formats: JSON, TXT transcription, RTTM diarization')
            # Explicit user checkbox is required for uploaded human gold.
            reference['verified'] = verified
            return finalize(directory, destination, reference, title, scenario)
        except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise HTTPException(400, str(exc)) from exc
        finally:
            await audio.close()
            for upload in (gold, diarization):
                if upload is not None: await upload.close()

    @app.get('/api/lab/runs')
    def runs():
        return sorted([json.loads(p.read_text()) for p in (LAB_DIR/'runs').glob('*/record.json')], key=lambda r: r['created_at'], reverse=True)

    @app.post('/api/lab/runs')
    def start(body: dict, background_tasks: BackgroundTasks):
        try: record = prepare_run(body, context)
        except (ValueError, TypeError, KeyError, OSError) as exc: raise HTTPException(400, str(exc)) from exc
        background_tasks.add_task(execute_run, record['id'], context)
        return record

    @app.get('/api/lab/runs/{run_id}')
    def detail(run_id: str):
        from backend.lab_metrics import divergences
        try:
            record = read_record(run_id)
            details = {}
            for summary in record['results']:
                variant = summary.get('case_key',summary['variant'])
                result = json.loads((path_for(run_id)/variant/'result.json').read_text())
                reference = json.loads((LAB_DIR/'datasets'/summary.get('dataset_id',record['dataset']['id'])/'reference.json').read_text())
                result['dataset_id'] = summary.get('dataset_id',record['dataset']['id'])
                result['reference'] = reference
                # Each variant reruns ASR/diarization; its original labels may differ.
                base_mapping = result.get('baseline_speaker_mapping', record['results'][0]['metrics'].get('speaker_mapping'))
                result['divergences'] = divergences(reference, result['baseline_segments'], result['segments'], base_mapping, result['metrics'].get('speaker_mapping'))
                details[variant] = result
            return {**record, 'details': details}
        except (ValueError, OSError) as exc: raise HTTPException(404, str(exc)) from exc

    @app.post('/api/lab/runs/{run_id}/cancel')
    def cancel(run_id: str):
        try: record = read_record(run_id)
        except (ValueError, OSError) as exc: raise HTTPException(404, str(exc)) from exc
        job = context.jobs.get('lab-'+run_id)
        if job: job['cancel_requested'] = True
        elif record['status'] in ('queued','running'):
            record['cancel_requested']=True
            atomic_json(path_for(run_id)/'record.json',record)
        return {'status': 'cancel_requested' if job else record['status']}
