"""One-job Qwen3-ASR MLX process; writes the existing segment format."""
import json
import gc
import math
import os
import sys
import time
import traceback
import wave
import statistics
import hashlib
from pathlib import Path

from backend.storage import atomic_json, digest_file


LANGUAGES = {
    'it': 'Italian', 'en': 'English', 'fr': 'French', 'de': 'German',
    'es': 'Spanish', 'pt': 'Portuguese', 'zh': 'Chinese',
    'ja': 'Japanese', 'ru': 'Russian', 'ko': 'Korean',
}
LANGUAGE_CODES = {name.lower(): code for code, name in LANGUAGES.items()}
CHUNK_SECONDS = 30.0
MAX_CHUNK_TOKENS = 1024
MEMORY_LIMIT_BYTES = 4 * 1024**3
STEP_TIMEOUT_SECONDS = 300


def process_memory_bytes(pid: int):
    """macOS physical footprint includes Metal allocations, unlike RSS."""
    if sys.platform != 'darwin':
        return None
    import ctypes
    class Usage(ctypes.Structure):
        _fields_ = [('uuid', ctypes.c_ubyte * 16)] + [
            (name, ctypes.c_uint64) for name in (
                'user_time', 'system_time', 'idle_wakeups', 'interrupt_wakeups',
                'pageins', 'wired_size', 'resident_size', 'phys_footprint',
                'start_time', 'exit_time')]
    usage = Usage()
    lib = ctypes.CDLL('/usr/lib/libproc.dylib')
    if lib.proc_pid_rusage(pid, 0, ctypes.byref(usage)) != 0:
        return None
    return usage.phys_footprint


def _report(output, phase, end, duration, message, **details):
    atomic_json(str(output) + '.progress', dict(
        phase=phase, end=end, duration=duration, message=message, **details))
    print(f'[Qwen {phase}] {message} {json.dumps(details) if details else ""}', flush=True)


def _segment_audio(source: Path, target: Path, start: float, end: float) -> None:
    with wave.open(str(source), 'rb') as reader:
        rate = reader.getframerate()
        reader.setpos(min(reader.getnframes(), max(0, int(start * rate))))
        frames = reader.readframes(max(0, int((end - start) * rate)))
        with wave.open(str(target), 'wb') as writer:
            writer.setparams(reader.getparams())
            writer.writeframes(frames)


def checkpoint_signature(request, duration):
    return dict(audio_sha256=digest_file(request['audio']), duration=duration,
                asr_model=request['asr_model'], language=request.get('language'),
                chunk_seconds=CHUNK_SECONDS)


def load_checkpoint(request, output, duration, signature):
    path = output.parent / 'qwen_partial.json'
    if not request.get('resume') or not path.exists():
        return [], 0.0
    saved = json.loads(path.read_text())
    if saved.get('signature') != signature:
        legacy = request.get('legacy_request', {})
        marker = output.parent / 'normalized.json'
        legacy_ok = ('signature' not in saved and marker.exists()
                     and json.loads(marker.read_text()).get('sha256') == request.get('source_sha256')
                     and all(legacy.get(k) == request.get(k) for k in ('audio', 'asr_model', 'language')))
        if not legacy_ok:
            raise RuntimeError('Checkpoint Qwen non compatibile con audio, modello o lingua attuali')
    covered = float(saved.get('covered_seconds', 0))
    segments = saved.get('segments', [])
    if (saved.get('duration') != duration or not 0 <= covered <= duration
            or (covered < duration and abs(covered / CHUNK_SECONDS - round(covered / CHUNK_SECONDS)) > .001)
            or any(not 0 <= s['start'] < s['end'] <= covered for s in segments)):
        raise RuntimeError('Checkpoint Qwen non valido: copertura o timestamp incoerenti')
    return segments, covered


def _transcribe_range(asr, mx, request, output, language, start, end, duration):
    clip = output.parent / 'qwen_chunk.wav'
    _segment_audio(Path(request['audio']), clip, start, end)
    try:
        result = asr.generate(str(clip), language=language, chunk_duration=CHUNK_SECONDS,
                              max_tokens=MAX_CHUNK_TOKENS)
        if getattr(result, 'generation_tokens', 0) >= MAX_CHUNK_TOKENS:
            del result
            gc.collect()
            mx.clear_cache()
            if end - start <= 7.5:
                raise RuntimeError(f'Qwen: generazione eccessiva nel tratto {start:.1f}–{end:.1f}s '
                                   'anche dopo suddivisione. Blocchi precedenti salvati: ripresa disponibile.')
            middle = (start + end) / 2
            _report(output, 'asr', start, duration,
                    f'Limite token: riprovo {start:.1f}–{end:.1f}s in parti più corte')
            first = _transcribe_range(asr, mx, request, output, language, start, middle, duration)
            second = _transcribe_range(asr, mx, request, output, language, middle, end, duration)
            return first + second
        raw = getattr(result, 'segments', None) or [dict(
            start=0, end=end - start, text=getattr(result, 'text', ''), language=None)]
        if float(raw[-1]['end']) < end - start - .1:
            raise RuntimeError(f'Qwen: risultato incompleto nel tratto {start:.1f}–{end:.1f}s')
        segments = []
        for item in raw:
            a, b = start + float(item['start']), min(end, start + float(item['end']))
            if b > a and item.get('text', '').strip():
                segments.append(dict(start=a, end=b, text=item['text'].strip(),
                                     speaker=None, words=[], detected_language=item.get('language')))
        del result, raw
        gc.collect()
        mx.clear_cache()
        return segments
    finally:
        clip.unlink(missing_ok=True)


def run(request: dict, output: Path) -> dict:
    import mlx.core as mx
    from mlx_audio.stt import load

    started = time.monotonic()
    try:
        os.nice(10)
    except OSError:
        pass
    mx.set_memory_limit(MEMORY_LIMIT_BYTES)
    mx.set_cache_limit(128 * 1024**2)
    _report(output, 'loading', 0, 0, 'Caricamento Qwen3-ASR; limite processo 4 GiB')
    asr = load(request['asr_model'])
    loaded = time.monotonic()
    language = LANGUAGES.get(request.get('language'))
    with wave.open(request['audio'], 'rb') as reader:
        duration = reader.getnframes() / reader.getframerate()
    signature = checkpoint_signature(request, duration)
    segments, covered = load_checkpoint(request, output, duration, signature)
    resumed_seconds = covered
    detected = next((s.get('detected_language') for s in segments if s.get('detected_language')), None)
    if language is None and detected:
        language = detected
    rates = []
    count = math.ceil(duration / CHUNK_SECONDS)
    for index in range(math.ceil(covered / CHUNK_SECONDS), count):
        offset = index * CHUNK_SECONDS
        end = min(duration, offset + CHUNK_SECONDS)
        _report(output, 'asr', offset, duration,
                f'Trascrizione blocco {index + 1}/{count} ({offset:.0f}–{end:.0f}s)',
                eta_seconds=(duration - offset) * statistics.median(rates[-8:]) if len(rates) >= 3 else None)
        step_started = time.monotonic()
        raw = _transcribe_range(asr, mx, request, output, language, offset, end, duration)
        for item in raw:
            item['id'] = len(segments) + 1
            segments.append(item)
        detected = next((s.get('detected_language') for s in segments if s.get('detected_language')), None)
        if language is None and detected:
            language = detected
        block_seconds = time.monotonic() - step_started
        rates.append(block_seconds / (end - offset))
        atomic_json(output.parent / 'qwen_partial.json', dict(
            segments=segments, covered_seconds=end, duration=duration, complete=False, signature=signature))
        _report(output, 'asr', end, duration,
                f'Trascritto {end:.0f}s / {duration:.0f}s',
                block_seconds=round(block_seconds, 2),
                eta_seconds=(duration - end) * statistics.median(rates[-8:]) if len(rates) >= 3 else None,
                mlx_peak_bytes=mx.get_peak_memory())
    asr_seconds = time.monotonic() - loaded
    del asr
    gc.collect()
    mx.clear_cache()

    alignment_seconds = 0
    if request['words'] and segments:
        align_started = time.monotonic()
        _report(output, 'alignment', 0, duration, 'Caricamento allineatore parole Qwen')
        aligner = load(request['aligner_model'])
        alignment_path = output.parent / 'qwen_alignment.json'
        alignment_signature = hashlib.sha256(json.dumps(dict(
            segments=segments, model=request['aligner_model'], language=language), sort_keys=True).encode()).hexdigest()
        aligned_count = 0
        if request.get('resume') and alignment_path.exists():
            saved = json.loads(alignment_path.read_text())
            if saved.get('signature') == alignment_signature and len(saved['segments']) == len(segments):
                aligned_count = int(saved['completed_segments'])
                if not 0 <= aligned_count <= len(segments):
                    raise RuntimeError('Checkpoint allineamento Qwen non valido')
                segments = saved['segments']
        alignment_rates = []
        for index in range(aligned_count, len(segments)):
            seg = segments[index]
            _report(output, 'alignment', seg['start'], duration,
                    f'Allineamento parole {index + 1}/{len(segments)}',
                    fraction=index / len(segments),
                    eta_seconds=(len(segments) - index) * statistics.median(alignment_rates[-8:]) if len(alignment_rates) >= 3 else None)
            step_started = time.monotonic()
            clip = output.parent / f"qwen_align_{seg['id']}.wav"
            try:
                _segment_audio(Path(request['audio']), clip, seg['start'], seg['end'])
                aligned = aligner.generate(audio=str(clip), text=seg['text'],
                                           language=language or seg.get('detected_language') or 'Italian')
                items = getattr(aligned, 'items', aligned)
                seg['words'] = [dict(word=(' ' if index else '') + word.text, start=seg['start'] + float(word.start_time),
                                     end=seg['start'] + float(word.end_time), prob=None,
                                     alignment='qwen_forced') for index, word in enumerate(items)]
                del aligned, items
                mx.clear_cache()
                alignment_rates.append(time.monotonic() - step_started)
                atomic_json(alignment_path, dict(signature=alignment_signature, segments=segments,
                                                 completed_segments=index + 1))
            finally:
                clip.unlink(missing_ok=True)
        alignment_seconds = time.monotonic() - align_started
        del aligner
        gc.collect()
        mx.clear_cache()
        _report(output, 'alignment', duration, duration, 'Allineamento parole completato', fraction=1)

    detected = segments[0].get('detected_language') if segments else None
    for seg in segments:
        seg.pop('detected_language', None)
    return dict(ok=True, segments=segments,
                language=request.get('language') or LANGUAGE_CODES.get(str(detected).lower(), 'it'),
                metrics=dict(model_load_seconds=loaded - started, asr_seconds=asr_seconds,
                             alignment_seconds=alignment_seconds,
                             mlx_peak_bytes=mx.get_peak_memory(),
                             qwen_chunk_seconds=CHUNK_SECONDS,
                             qwen_max_chunk_tokens=MAX_CHUNK_TOKENS,
                             qwen_resumed_seconds=resumed_seconds,
                             alignment_included_in_asr=False))


if __name__ == '__main__':
    output = Path(sys.argv[2])
    try:
        atomic_json(output, run(json.loads(Path(sys.argv[1]).read_text()), output))
    except Exception as exc:
        traceback.print_exc()
        atomic_json(output, {'ok': False, 'error': str(exc)})
        raise SystemExit(1)
