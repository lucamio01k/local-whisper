#!/usr/bin/env python3
"""Explicit download utility; never called by normal app or Lab inference."""
import json
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download, hf_hub_download
from fnmatch import fnmatch

ROOT = Path(__file__).resolve().parents[1]
REPO = 'convaiinnovations/laya-multilingual'
PATTERNS = ['rl_agent_config.json', 'model.safetensors', 'tokenizer/*', 'encoder/*']

if __name__ == '__main__':
    info = HfApi().model_info(REPO, files_metadata=True)
    files = [f for f in info.siblings if any(fnmatch(f.rfilename, pattern) for pattern in PATTERNS)]
    size = sum(f.size or 0 for f in files)
    if not files or size > 1_500_000_000:
        raise RuntimeError(f'Unexpected checkpoint size ({size} bytes); inspect model before downloading')
    destination = ROOT/'models_cache/lab/laya-multilingual'
    print(f'Laya checkpoint {info.sha}: {size/1e6:.1f} MB', flush=True)
    snapshot_download(REPO, revision=info.sha, allow_patterns=PATTERNS, local_dir=destination)
    config = json.loads((destination/'rl_agent_config.json').read_text())
    encoder = HfApi().model_info(config['encoder'])
    hf_hub_download(config['encoder'], 'config.json', revision=encoder.sha, local_dir=destination/'encoder')
    (destination/'local-whisper-download.json').write_text(json.dumps({'repo': REPO, 'revision': info.sha,
        'encoder_config_revision': encoder.sha, 'bytes': size}, indent=2)+'\n')
    print(destination, flush=True)
