"""Optional semantic environment; no FastAPI, ASR or diarization model imports."""
import json
import os
import sys
from pathlib import Path
from backend.storage import atomic_json
from backend.refinement import RefinementConfig, labeled_turns, refine
from backend.lab_models import LayaSpeakerRefiner, GLiNERSpeakerRefiner

if __name__ == '__main__':
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', USE_TF='0', HF_HUB_DISABLE_TELEMETRY='1')
    request = json.loads(Path(sys.argv[1]).read_text())
    config = RefinementConfig(**request['parameters'])
    turns, _ = labeled_turns(request['turns'])
    refiner = LayaSpeakerRefiner() if request['model'] == 'laya' else GLiNERSpeakerRefiner()
    segments, decisions = refine(request['segments'], turns, refiner, config, prior=request['prior'], semantic=True)
    atomic_json(sys.argv[2], {'segments': segments, 'decisions': decisions, 'model_info': getattr(refiner, 'model_info', {})})
