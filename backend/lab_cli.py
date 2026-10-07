"""Reproducible local subset imports and benchmark runs without UI."""
import argparse
import json
import os
from pathlib import Path
from backend import lab
from backend.lab_dataset import import_manifest


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    ingest = commands.add_parser('import')
    ingest.add_argument('manifest', type=Path)
    commands.add_parser('prepare-public')
    commands.add_parser('suites')
    elan=commands.add_parser('import-elan')
    elan.add_argument('--audio',type=Path,required=True)
    elan.add_argument('--annotation',type=Path,required=True)
    elan.add_argument('--tiers',nargs='+',required=True)
    elan.add_argument('--group',required=True)
    elan.add_argument('--split',choices=['calibration','evaluation'],default='evaluation')
    elan.add_argument('--exclude',type=Path,help='JSON muted/anonymized intervals [[start,end],...]')
    elan.add_argument('--reviewed-anonymization',action='store_true',help='Confirm muted names excluded from scoring')
    elan.add_argument('--replace-it',action='store_true',help='Replace equal duration of provisional Italian suite membership')
    elan.add_argument('--interval',type=float,nargs=2,help='Subset seconds, aligned to ELAN utterance boundaries')
    export=commands.add_parser('export')
    export.add_argument('run_id')
    export.add_argument('--output',type=Path,required=True)
    export.add_argument('--format',choices=['json','csv'])
    run = commands.add_parser('run')
    run.add_argument('dataset_id',nargs='?')
    run.add_argument('--suite',dest='suite_id')
    run.add_argument('--selection',choices=['small','medium','complete'])
    run.add_argument('--mode',choices=['frozen','complete'],default='frozen')
    run.add_argument('--budget-seconds',type=int,default=1800)
    run.add_argument('--resource-profile',choices=['light','balanced','heavy'],default='light')
    run.add_argument('--repetitions',type=int,default=1)
    run.add_argument('--split',choices=['calibration','evaluation'])
    run.add_argument('--interval',type=float,nargs=2)
    run.add_argument('--snapshot-key',help='Require compatible immutable snapshot for single clip')
    run.add_argument('--variants', nargs='+', choices=list(lab.VARIANTS), default=['baseline', 'acoustic', 'acoustic_laya'])
    run.add_argument('--settings', type=Path, help='JSON object with pipeline, parameters, notes')
    args = parser.parse_args()
    try:
        if args.command=='prepare-public':
            from backend.lab_sources import prepare_public
            suite=prepare_public(lab.LAB_DIR)
            print(json.dumps({'suite':suite['id'],'clips':len(suite['dataset_ids']),'duration':suite['duration']})); return 0
        if args.command=='suites':
            print(json.dumps([json.loads(p.read_text()) for p in (lab.LAB_DIR/'suites').glob('*.json')],indent=2)); return 0
        if args.command=='import-elan':
            from backend.lab_sources import elan_reference,canonical_clip
            from backend.lab_dataset import audio_duration
            gold=elan_reference(args.annotation,audio_duration(args.audio),args.tiers)
            if args.exclude:
                from backend.lab_sources import complement,intersect_uem
                from backend.lab_dataset import validate_reference
                excluded=json.loads(args.exclude.read_text())
                validate_reference({'uem':excluded},audio_duration(args.audio))
                gold['uem']=intersect_uem(gold['uem'],complement(excluded,audio_duration(args.audio)))
                usable=[s for s in gold['segments'] if all(not (s['start']<b and s['end']>a) for a,b in excluded)]
                removed=[[s['start'],s['end']] for s in gold['segments'] if s not in usable]
                gold['uem']=intersect_uem(gold['uem'],complement(removed,audio_duration(args.audio)))
                gold.update(segments=usable,text=' '.join(s['text'] for s in usable),turns=[[s['start'],s['end'],s['speaker']] for s in usable],
                            words=[{'word':w,'speaker':s['speaker']} for s in usable for w in s['text'].split()])
            gold['verified']=args.reviewed_anonymization
            duration=audio_duration(args.audio)
            a,b=args.interval if args.interval else (0.,duration)
            if not 0<=a<b<=duration or b-a>1800: raise ValueError('TIGR import requires interval within audio, at most 30 minutes')
            provenance=dict(title=args.audio.stem,corpus='TIGR',language='it',split=args.split,group=args.group,scenario='IT conversation',
                            source=str(args.annotation.resolve()),license='Restricted research contract; user-authorized local import',microphone='Sony ambient (confirm source)')
            record=canonical_clip(lab.LAB_DIR,args.audio,gold,provenance,a,b)
            if args.replace_it:
                from backend.storage import atomic_json
                from backend.lab_dataset import load_dataset
                path=lab.LAB_DIR/'suites'/'public-it-en.json'; suite=json.loads(path.read_text())
                kept=[]; removed=0.
                for id in suite['dataset_ids']:
                    ds=load_dataset(lab.LAB_DIR,id)[0]
                    if ds.get('language')=='it' and ds.get('corpus')=='VoxForge' and removed<record['duration']: removed+=ds['duration']
                    else: kept.append(id)
                if record['id'] not in kept: kept.append(record['id'])
                suite['dataset_ids']=kept; suite['duration']=sum(load_dataset(lab.LAB_DIR,id)[0]['duration'] for id in kept)
                atomic_json(path,suite)
            print(json.dumps(record,indent=2)); return 0
        if args.command=='export':
            from backend.storage import atomic_json
            record=lab.read_record(args.run_id)
            record['details']={r.get('case_key',r['variant']):json.loads((lab.path_for(args.run_id)/r.get('case_key',r['variant'])/'result.json').read_text()) for r in record['results']}
            if args.format=='csv' or (args.format is None and args.output.suffix.lower()=='.csv'):
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(lab.report_csv(record),encoding='utf-8')
            else: atomic_json(args.output,record)
            return 0
        if args.command == 'import':
            print(json.dumps(import_manifest(lab.LAB_DIR, args.manifest), indent=2)); return 0
        # No app history in standalone foreground benchmark.
        os.environ['LOCAL_WHISPER_LAB_WORKER'] = '1'
        from backend import main as context
        body = json.loads(args.settings.read_text()) if args.settings else {}
        body.update(dataset_id=args.dataset_id, variants=args.variants)
        body.update({k:getattr(args,k) for k in ('suite_id','mode','budget_seconds','resource_profile','repetitions','split','interval','snapshot_key') if getattr(args,k) is not None})
        if args.selection: body['selection']=args.selection
        record = lab.prepare_run(body, context)
        print(f"Run: {record['id']}", flush=True)
        lab.execute_run(record['id'], context)
        result = lab.read_record(record['id'])
        print(json.dumps({k:result.get(k) for k in ('id','status','error','budget_elapsed_seconds','aggregates')}, indent=2))
        return 0 if result['status'] == 'done' else 2
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc)); return 1


if __name__ == '__main__':
    raise SystemExit(main())
