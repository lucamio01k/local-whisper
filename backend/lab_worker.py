"""One Lab variant per process; all model memory released on process exit."""
import argparse
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from backend.storage import atomic_json


def semantic_pass(segments, turns, parameters, prior, model, context):
    from backend.lab_models import semantic_python, UnavailableSpeakerRefiner
    from backend.refinement import RefinementConfig, labeled_turns, refine
    process = None
    with tempfile.TemporaryDirectory(prefix='local-whisper-semantic-') as folder:
        incoming, outgoing = Path(folder)/'input.json', Path(folder)/'output.json'
        atomic_json(incoming, {'segments': segments, 'turns': turns, 'parameters': parameters,
                              'prior': prior, 'model': model})
        env = {k: v for k, v in os.environ.items() if k != 'HF_TOKEN'}
        try:
            process = subprocess.Popen([semantic_python(), '-m', 'backend.lab_semantic_worker', str(incoming), str(outgoing)],
                                       env=env, start_new_session=(os.name == 'posix'))
            context.job_processes['lab-semantic'] = process
            process.wait(timeout=600)
            if process.returncode or not outgoing.exists(): raise RuntimeError('Semantic worker failed')
            return json.loads(outgoing.read_text()), None
        except Exception as exc:
            error = f'Semantic worker fallback: {exc}'
            labeled, _ = labeled_turns(turns)
            name = 'laya_multilingual' if model == 'laya' else 'gliner_multilingual_decide'
            final, decisions = refine(segments, labeled, UnavailableSpeakerRefiner(name, error),
                                      RefinementConfig(**parameters), prior=prior, semantic=True)
            return {'segments': final, 'decisions': decisions, 'model_info': {}}, error
        finally:
            if process is not None and process.poll() is None:
                context._terminate_process(process)
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    context._terminate_process(process, force=True)
                    process.wait(timeout=3)
            context.job_processes.pop('lab-semantic', None)


def run(spec):
    threads = str(spec.get('resources', {}).get('threads', 2))
    os.environ.update(LOCAL_WHISPER_LAB_WORKER='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', OMP_NUM_THREADS=threads, MKL_NUM_THREADS=threads,
                      LOCAL_WHISPER_LAB_THREADS=threads, VECLIB_MAXIMUM_THREADS=threads)
    from backend import main
    from backend.runner import run_file
    from backend.lab_metrics import evaluate
    from backend.lab_dataset import load_dataset
    def stop(_sig, _frame):
        for process in list(main.job_processes.values()):
            main._terminate_process(process)
            try: process.wait(timeout=5)
            except Exception: main._terminate_process(process, force=True)
        raise SystemExit(130)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    dataset, audio, reference = load_dataset(Path(spec['root']), spec['dataset_id'])
    variant = spec['variant']
    if spec.get('snapshot'):
        return replay(spec, dataset, reference, main)
    request = {**spec['pipeline'], 'audio_path': str(audio), 'hf_token': os.environ.get('HF_TOKEN', ''), 'lab': True}
    if not request['hf_token']: raise RuntimeError('Baseline requires locally configured Pyannote token/weights')
    if variant != 'baseline' or spec.get('prepare_snapshot'):
        request['lab_refinement'] = {'parameters': spec['parameters'], 'precision': request['diarization_precision']}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='local-whisper-lab-') as work:
        result = run_file(request, Path(work), lambda *args: None, main)
    if not result['diarization_ran']:
        raise RuntimeError(result.get('diarization_error') or 'Baseline diarization did not run')
    baseline = result['segments']
    turns = result['turns']
    final, decisions, status, warnings, semantic_model_info = baseline, [], 'done', [], {}
    refinement = result.get('lab_refinement')
    added = 0.
    if variant != 'baseline':
        if not refinement:
            raise RuntimeError('Acoustic stage did not return evidence')
        final = refinement['segments']
        decisions = refinement['decisions']
        added = refinement.get('seconds', 0.)
        status = refinement['status']
        if refinement.get('error'): warnings.append(refinement['error'])
    if variant in ('acoustic_laya', 'acoustic_gliner'):
        acoustic_decisions = decisions
        semantic_started = time.monotonic()
        semantic, failure = semantic_pass(baseline, turns, spec['parameters'], acoustic_decisions,
                                          'laya' if variant == 'acoustic_laya' else 'gliner', main)
        final = merge_semantic(final, semantic['segments'], semantic['decisions'])
        semantic_decisions = semantic['decisions']
        semantic_model_info = semantic.get('model_info', {})
        if failure:
            warnings.append(failure)
            status = 'degraded'
        added += time.monotonic()-semantic_started
        decisions = acoustic_decisions+semantic_decisions
        from backend.lab_models import availability
        if variant == 'acoustic_gliner' or not availability('laya')['available'] or any(d['reason'] in ('refiner_failed', 'model_unavailable', 'adapter_pending') for d in semantic_decisions):
            status = 'degraded'
            warnings.append('Requested semantic model unavailable/failed; acoustic fallback, not a full semantic benchmark')
    elapsed = time.monotonic()-started
    changed = any(a.get('speaker') != b.get('speaker') for a, b in zip(baseline, final))
    baseline_metrics = evaluate(reference, baseline, dataset['duration'], turns['standard'])
    metrics = evaluate(reference, final, dataset['duration'], turns['standard'], fixed_mapping=baseline_metrics.get('speaker_mapping')) if changed else baseline_metrics
    fallback_peak = max(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    # Darwin reports bytes, Linux reports KiB. This fallback is a per-process high water mark.
    peak_bytes = fallback_peak if sys.platform == 'darwin' else fallback_peak*1024
    return {'status': status, 'segments': final, 'baseline_segments': baseline, 'decisions': decisions, 'warnings': warnings,
            'semantic_model_info': semantic_model_info,
            'acoustic_observations': refinement,
            'metrics': metrics, 'baseline_speaker_mapping': baseline_metrics.get('speaker_mapping'),
            'pipeline_metrics': result['metrics'], 'turns': turns,
            'performance': {'total_seconds': elapsed, 'rtf': elapsed/dataset['duration'], 'refiner_seconds': added,
                            'peak_ram_bytes': peak_bytes, 'ram_method': 'max_process_high_water_rss',
                            'gpu_memory_included': False, 'cache': 'cold_model_processes; OS filesystem cache uncontrolled'}}


def merge_semantic(acoustic, semantic, decisions):
    import copy
    resolved = {d['segment_id'] for d in decisions if d['accepted']}
    proposals = {s['id']:s for s in semantic}
    return [copy.deepcopy(proposals[s['id']] if s['id'] in resolved else s) for s in acoustic]


def replay(spec, dataset, reference, context):
    """No ASR/Pyannote model in this process. All context originates in snapshot."""
    import copy
    from backend.lab_suite import read_snapshot
    from backend.lab_metrics import evaluate
    from backend.refinement import FrozenAcousticResolver, RefinementConfig, labeled_turns, refine
    source = read_snapshot(spec['snapshot'], spec['snapshot_key'])
    baseline, turns = source['baseline_segments'], source['turns']
    final, decisions = copy.deepcopy(baseline), []
    variant = spec['variant']; status = 'done'; warnings=[]; model_info={}
    started=time.monotonic()
    if variant != 'baseline':
        observation = source.get('acoustic_observations') or {}
        if observation.get('status') != 'done':
            status='degraded'; warnings.append(observation.get('error') or 'Acoustic observations incomplete')
        labeled,_=labeled_turns(turns)
        final,decisions=refine(baseline,labeled,FrozenAcousticResolver(observation.get('decisions',[])),RefinementConfig(**spec['parameters']))
        if any(d['reason']=='refiner_failed' for d in decisions): status='degraded'
        if variant in ('acoustic_laya','acoustic_gliner'):
            semantic,failure=semantic_pass(baseline,turns,spec['parameters'],decisions,'laya' if variant=='acoustic_laya' else 'gliner',context)
            final=merge_semantic(final,semantic['segments'],semantic['decisions'])
            decisions += semantic['decisions']; model_info=semantic.get('model_info',{})
            if failure or any(d['reason'] in ('refiner_failed','model_unavailable','adapter_pending') for d in semantic['decisions']):
                status='degraded'; warnings.append(failure or 'Semantic model unavailable/failed')
    added=time.monotonic()-started
    baseline_metrics=evaluate(reference,baseline,dataset['duration'],turns['standard'])
    metrics=evaluate(reference,final,dataset['duration'],turns['standard'],fixed_mapping=baseline_metrics.get('speaker_mapping'))
    # Lexical output and raw turns cannot change in frozen attribution comparisons.
    for k in ('wer','reference_words','word_errors','pyannote_der','pyannote_detail_seconds'):
        if k in baseline_metrics: metrics[k]=baseline_metrics[k]
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'status':status,'segments':final,'baseline_segments':baseline,'turns':turns,'decisions':decisions,
            'warnings':warnings,'semantic_model_info':model_info,'metrics':metrics,
            'baseline_speaker_mapping':baseline_metrics.get('speaker_mapping'),'pipeline_metrics':source['pipeline_metrics'],
            'snapshot_key':spec['snapshot_key'],
            'performance':{'total_seconds':source['performance']['total_seconds']+added,'execution_seconds':added,
                           'refiner_seconds':added if variant!='baseline' else 0.,'rtf':(source['performance']['total_seconds']+added)/dataset['duration'],
                           'preparation_seconds':source['performance']['total_seconds'], 'peak_ram_bytes':max(source['performance']['peak_ram_bytes'],peak if sys.platform=='darwin' else peak*1024),
                           'ram_method':'process_high_water_rss','cache':'frozen_snapshot','gpu_memory_included':False}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        result = run(json.loads(Path(args.input).read_text()))
        atomic_json(args.output, result)
    except Exception as exc:
        atomic_json(args.output, {'status': 'error', 'error': str(exc)})
        raise SystemExit(1)
