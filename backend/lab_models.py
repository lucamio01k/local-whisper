"""Optional semantic refiners. Explicit local paths, lazy CPU loading, no downloads."""
import importlib.util
import os
import subprocess
import sys
import importlib.metadata
from pathlib import Path
from backend.refinement import SpeakerRefiner, Resolution, UNKNOWN, ranked_resolution


MODEL_SPECS = {
    'laya': {'package': 'laya', 'env': 'LOCAL_WHISPER_LAYA_PATH', 'repo': 'convaiinnovations/laya-multilingual'},
    'gliner': {'package': 'gliner2', 'env': 'LOCAL_WHISPER_GLINER_PATH', 'repo': 'fastino/GLiNER2.5-multi-Decide'},
}


def semantic_python():
    return os.environ.get('LOCAL_WHISPER_SEMANTIC_PYTHON', sys.executable)


def package_version(name):
    try: return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError: return None


def package_available(package):
    interpreter = semantic_python()
    if interpreter == sys.executable: return importlib.util.find_spec(package) is not None
    try:
        result = subprocess.run([interpreter, '-c', 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)', package],
                                capture_output=True, timeout=5)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError): return False


def availability(name):
    if name == 'diarizationlm':
        return {'available': False, 'status': 'adapter_pending', 'reason': 'Quality postprocessor contract ready; GGUF runtime not validated'}
    if name == 'gliner':
        return {'available': False, 'status': 'adapter_pending', 'repo': MODEL_SPECS[name]['repo'],
                'reason': 'GLiNER adapter pending: per-candidate score API requires validation'}
    spec = MODEL_SPECS[name]
    raw = os.environ.get(spec['env'])
    path = Path(raw).expanduser() if raw else None
    installed = package_available(spec['package'])
    complete = path and all((path/f).is_file() for f in ('rl_agent_config.json', 'model.safetensors', 'tokenizer/tokenizer.json', 'encoder/config.json'))
    return {'available': bool(installed and complete), 'repo': spec['repo'],
            'status': 'ready_unvalidated' if installed and complete else 'not_installed',
            'reason': f"{name.title()} not installed / incomplete local checkpoint" if not installed or not complete else 'Local weights found; speaker attribution requires gold benchmark',
            'environment_variable': spec['env']}


def semantic_state(evidence):
    def turn(s): return {'speaker': s.get('speaker') or UNKNOWN, 'text': s.get('text', '')[:500]}
    # Numeric acoustic/temporal evidence deliberately excluded from semantic input.
    return {'previous': [turn(s) for s in evidence.previous], 'current': {'text': evidence.segment.get('text', '')[:1000]},
            'next': [turn(s) for s in evidence.following]}


class LayaSpeakerRefiner(SpeakerRefiner):
    name = 'laya_multilingual'

    def __init__(self):
        self.model = None
        self.failure = None
        self.model_info = {}

    def resolve(self, evidence):
        if self.failure: return Resolution(refiner=self.name, reason='model_unavailable', metadata={'error': self.failure})
        if self.model is None:
            state = availability('laya')
            if not state['available']:
                self.failure = state['reason']
                return Resolution(refiner=self.name, reason='model_unavailable', metadata={'error': self.failure})
            try:
                os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', USE_TF='0')
                import torch
                import laya
                torch.set_num_threads(int(os.environ.get("LOCAL_WHISPER_LAB_THREADS", 2)))
                self.model = laya.load(str(Path(os.environ['LOCAL_WHISPER_LAYA_PATH']).expanduser()), device='cpu', compile=False)
                from backend.storage import digest_file
                path = Path(os.environ['LOCAL_WHISPER_LAYA_PATH']).expanduser()
                self.model_info = {'sdk_version': getattr(laya, '__version__', None), 'device': 'cpu',
                    'packages': {name: package_version(name) for name in ('torch', 'transformers')},
                    'files_sha256': {f: digest_file(path/f) for f in ('model.safetensors', 'rl_agent_config.json', 'encoder/config.json') if (path/f).is_file()}}
            except Exception as exc:
                self.failure = str(exc)
                raise
        criteria = {s: f'The person labeled {s} in the surrounding conversation, based on their statements and conversational role.' for s in evidence.candidates}
        criteria[UNKNOWN] = 'Speaker identity cannot be established from this text; insufficient or conflicting evidence.'
        result = self.model.predict(semantic_state(evidence), {'speaker': {'type': 'choice',
            'instructions': 'Which participant spoke CURRENT? Use meaning, references and conversational continuity. Do not assume turn alternation. Choose UNKNOWN when uncertain.',
            'criteria': criteria}}, max_len=1024)
        answer = result['answers']['speaker']
        scores = answer['probabilities']
        if set(scores) != set(criteria): raise ValueError('Laya returned unexpected candidate set')
        resolution = ranked_resolution(scores, self.name, 'semantic_context', {'score_kind': 'uncalibrated_model_probability', 'device': 'cpu'})
        if resolution.speaker != answer['choice']: raise ValueError('Laya choice/probabilities disagree')
        return resolution

    def close(self):
        self.model = None


class UnavailableSpeakerRefiner(SpeakerRefiner):
    def __init__(self, name, error):
        self.name, self.error = name, error

    def resolve(self, evidence):
        return Resolution(refiner=self.name, reason='model_unavailable', metadata={'error': self.error})


class GLiNERSpeakerRefiner(SpeakerRefiner):
    name = 'gliner_multilingual_decide'

    def resolve(self, evidence):
        # Model exists, but raw per-candidate score API must be validated before applying changes.
        return Resolution(refiner=self.name, reason='adapter_pending', metadata={
            'error': 'GLiNER adapter contract ready; per-candidate probabilities not validated'})


class QualityPostprocessor:
    """Separate full-transcript contract, run only after ASR/pyannote workers exit."""
    name = 'diarizationlm'

    def process(self, segments):
        raise RuntimeError('DiarizationLM adapter pending: validate GGUF prompt, speaker transfer and llama.cpp runtime first')
