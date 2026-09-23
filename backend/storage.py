"""Immutable revisions; one atomic HEAD selects the complete visible result."""
import copy
import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

LOCK = threading.RLock()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def active(directory):
    head = Path(directory) / 'HEAD.json'
    if not head.exists():
        return None
    revision = json.loads(head.read_text())['version']
    return read_revision(directory, revision)


def read_revision(directory, version):
    if not isinstance(version, str) or not version.isalnum():
        raise ValueError('Versione non valida')
    return json.loads((Path(directory) / 'revisions' / f'{version}.json').read_text())


def legacy_version(directory, segments):
    return 'legacy' + hashlib.sha256(json.dumps(segments, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def commit(directory, segments, metadata, reason, expected=None, previous=None):
    with LOCK:
        current = active(directory)
        actual = current['version'] if current else legacy_version(directory, previous or [])
        if expected is not None and expected != actual:
            raise ValueError('Versione modificata: ricarica prima di applicare')
        if current is None and previous:
            baseline = {'version': actual, 'parent': None, 'created_at': datetime.now(timezone.utc).isoformat(),
                        'reason': 'legacy', 'segments': copy.deepcopy(previous), 'metadata': copy.deepcopy(metadata)}
            baseline_path = Path(directory) / 'revisions' / f'{actual}.json'
            if not baseline_path.exists():
                atomic_json(baseline_path, baseline)
        version = uuid.uuid4().hex
        record = {'version': version, 'parent': actual if current or previous else None,
                  'created_at': datetime.now(timezone.utc).isoformat(), 'reason': reason,
                  'segments': copy.deepcopy(segments), 'metadata': copy.deepcopy(metadata)}
        atomic_json(Path(directory) / 'revisions' / f'{version}.json', record)
        atomic_json(Path(directory) / 'HEAD.json', {'version': version})
        return record
