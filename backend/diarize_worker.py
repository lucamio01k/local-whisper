import argparse
import json
import os
import sys
from pathlib import Path
from backend.storage import atomic_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        from backend.engine import run
        result = run(json.loads(Path(args.input).read_text()), os.environ.get('HF_TOKEN', ''))
        atomic_json(args.output, result)
        return 0
    except Exception as exc:
        atomic_json(args.output, {'ok': False, 'error': str(exc)})
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
