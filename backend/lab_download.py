"""Sequential resumable transfers, bounded by total Lab disk occupancy."""
import json
import os
import re
import urllib.request
from pathlib import Path
from backend.storage import atomic_json, digest_file

DEFAULT_BUDGET = 2_000_000_000


class DiskBudget:
    def __init__(self, root, limit=DEFAULT_BUDGET):
        self.root, self.limit = Path(root), int(limit)
        if self.limit <= 0: raise ValueError('Positive disk budget required')

    def used(self):
        # Count hardlinked canonical audio once, as filesystem does.
        seen, size = set(), 0
        for path in self.root.rglob('*'):
            if path.is_file():
                stat = path.stat(); key = (stat.st_dev, stat.st_ino)
                if key not in seen: size += stat.st_size; seen.add(key)
        return size

    def reserve(self, amount):
        if amount < 0 or self.used()+amount > self.limit:
            raise ValueError('Lab disk budget exceeded; completed files preserved')
        if self.root.exists() and os.statvfs(self.root).f_bavail*os.statvfs(self.root).f_frsize < amount:
            raise ValueError('Insufficient free disk space')


def download(url, destination, budget, max_bytes=200_000_000):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    receipt = destination.with_suffix(destination.suffix+'.json')
    if destination.exists() and receipt.exists():
        metadata = json.loads(receipt.read_text())
        if metadata['url'] == url and metadata['sha256'] == digest_file(destination): return destination
        raise ValueError('Existing download provenance/hash mismatch')
    partial = destination.with_suffix(destination.suffix+'.part')
    state = partial.with_suffix(partial.suffix+'.json')
    previous = json.loads(state.read_text()) if state.exists() else {}
    offset = partial.stat().st_size if partial.exists() else 0
    if offset and previous.get('url') != url: raise ValueError('Partial download belongs to another URL')
    if offset and previous.get('expected_bytes')==offset:
        budget.reserve(len(url.encode())+512)
        partial.replace(destination)
        atomic_json(receipt,{'url':url,'bytes':offset,'sha256':digest_file(destination)})
        state.unlink(missing_ok=True)
        return destination
    headers = {'User-Agent': 'local-whisper-lab/1.0'}
    if offset:
        headers['Range'] = f'bytes={offset}-'
        if previous.get('etag'): headers['If-Range'] = previous['etag']
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        resumed = response.status == 206 and offset > 0
        if resumed and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
            raise ValueError('Invalid server resume range')
        if resumed:
            match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
            if not match or int(match[2])+1!=int(match[3]): raise ValueError('Incomplete server resume range')
        if not resumed: offset = 0
        length = response.headers.get('Content-Length')
        if length and int(length)+offset > max_bytes: raise ValueError('Source exceeds per-file download limit')
        if length: budget.reserve(max(0, int(length)-(partial.stat().st_size if not resumed and partial.exists() else 0)))
        budget.reserve(len(url.encode())+512)
        atomic_json(state, {'url': url, 'etag': response.headers.get('ETag'), 'expected_bytes':offset+int(length) if length else None})
        with partial.open('ab' if resumed else 'wb') as handle:
            total = offset
            while chunk := response.read(256*1024):
                if total+len(chunk) > max_bytes: raise ValueError('Transfer stopped at per-file limit')
                budget.reserve(len(chunk))
                handle.write(chunk); handle.flush(); total += len(chunk)
        if length and total != offset+int(length): raise ValueError('Incomplete transfer; partial preserved')
    partial.replace(destination)
    budget.reserve(len(url.encode())+512)
    atomic_json(receipt, {'url': url, 'bytes': total, 'sha256': digest_file(destination)})
    state.unlink(missing_ok=True)
    return destination
