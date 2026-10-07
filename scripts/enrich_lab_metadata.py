#!/usr/bin/env python3
"""Recover documented VoxForge recording hardware without retaining archives."""
import json
import tarfile
from pathlib import Path
from backend.lab_download import DiskBudget, download
from backend.storage import atomic_json, digest_file
from backend.lab_sources import vox_metadata


def enrich(root):
    root=Path(root); groups={}
    for path in (root/'datasets').glob('*/dataset.json'):
        ds=json.loads(path.read_text())
        if ds.get('corpus')=='VoxForge': groups.setdefault(ds['provenance']['source'],[]).append((path,ds))
    for url,items in groups.items():
        archive=download(url,root/'sources'/url.rsplit('/',1)[1],DiskBudget(root),30_000_000)
        try:
            if digest_file(archive)!=items[0][1]['provenance']['archive_sha256']: raise ValueError('Source archive changed')
            with tarfile.open(archive) as tar:
                fields,microphone=vox_metadata(tar)
            for path,ds in items:
                ds['provenance'].update(recording_metadata=fields,microphone=microphone,metadata_source='Archive etc/README')
                ds['channel']=microphone; atomic_json(path,ds)
        finally:
            archive.unlink(missing_ok=True); archive.with_suffix('.tgz.json').unlink(missing_ok=True)
    return len(groups)


if __name__=='__main__': print('Archives inspected:',enrich(Path(__file__).resolve().parents[1]/'lab-data'))
