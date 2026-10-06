"""Report actual pyannote step counters without inventing overall percentages."""
import sys
from backend.storage import atomic_json


LABELS = {
    'loading': 'Caricamento modello diarizzazione',
    'preparing': 'Preparazione audio diarizzazione',
    'segmentation': 'Segmentazione audio',
    'embeddings': 'Analisi voci',
    'speaker_counting': 'Conteggio voci',
    'clustering': 'Raggruppamento speaker',
    'discrete_diarization': 'Ricostruzione turni speaker',
    'assignment': 'Assegnazione speaker al testo',
    'done': 'Diarizzazione completata',
}


class DiarizationProgress:
    def __init__(self, path=None):
        self.path = path
        self.last = None

    def report(self, step, total=None, completed=None):
        if not self.path:
            return
        total = int(total) if total is not None else None
        completed = int(completed) if completed is not None else None
        percent = None
        if total is not None and completed is not None and total > 0:
            percent = max(0, min(100, int(100 * completed / total)))
        label = LABELS.get(step, step)
        message = label if percent is None else f'{label}: {percent}% ({completed}/{total})'
        update = dict(step=step, label=label, percent=percent,
                      completed=completed, total=total, message=message)
        if update != self.last:
            try:
                atomic_json(self.path, update)
            except (OSError, TypeError, ValueError) as exc:
                print(f'[Diarizzazione] Progresso disabilitato: {exc}', file=sys.stderr, flush=True)
                self.path = None
                return
            self.last = update
            print(f'[Diarizzazione] {message}', flush=True)

    def __call__(self, step_name, step_artifact=None, file=None, total=None, completed=None):
        if total is not None and completed is not None:
            self.report(step_name, total, completed)
        elif step_name == 'embeddings':
            # Final embeddings artifact is emitted immediately before clustering,
            # which has no batch counter in pyannote.
            self.report('clustering')
        elif step_name == 'segmentation':
            self.report('speaker_counting')
        else:
            self.report(step_name)
