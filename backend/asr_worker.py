"""One-job faster-whisper process; never imports FastAPI or history."""
import json
import sys
import time
from pathlib import Path
from backend.storage import atomic_json


def run(request, output):
    from faster_whisper import WhisperModel
    started = time.monotonic()
    model = WhisperModel(request['model'], device=request['device'],
                         compute_type=request['compute_type'], download_root=request['models_dir'])
    loaded = time.monotonic()
    iterator, info = model.transcribe(request['audio'], language=request.get('language'),
        initial_prompt=request.get('glossary') or None, beam_size=request['beam_size'],
        best_of=request['best_of'], word_timestamps=request['word_timestamps'])
    segments = []
    for seg in iterator:
        segments.append(dict(id=seg.id, start=seg.start, end=seg.end, text=seg.text.strip(), speaker=None,
            words=[dict(word=w.word, start=w.start, end=w.end, prob=w.probability, alignment='faster_whisper') for w in (seg.words or [])]))
        atomic_json(str(output) + '.progress', {'end': seg.end})
    return dict(ok=True, segments=segments, language=info.language,
        metrics={'model_load_seconds': loaded-started, 'asr_seconds': time.monotonic()-loaded,
                 'alignment_seconds': None if request['word_timestamps'] else 0,
                 'alignment_included_in_asr': request['word_timestamps']})


if __name__ == '__main__':
    try:
        atomic_json(sys.argv[2], run(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2]))
    except Exception as exc:
        atomic_json(sys.argv[2], {'ok': False, 'error': str(exc)})
        raise SystemExit(1)
