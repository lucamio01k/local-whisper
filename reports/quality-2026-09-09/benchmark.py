import json, subprocess, time, os
from pathlib import Path
root=Path(__file__).resolve().parents[2]
out=root/'reports/quality-2026-09-09'
base=[str(root/'whisper.cpp/build/bin/whisper-cli'),'-m',str(root/'models_cache/whisper.cpp/ggml-large-v3-turbo.bin'),'-f',str(root/'uploads/b12cfe14-e01a-41a6-afac-d18c31eb7046/whisper_cpp.wav'),'-t','8','-l','it','-oj','-np']
runs=[]
for name,opts in [('balanced',['-bs','3']),('fast',['-bs','1']),('quality',['-bs','5']),('glossary',['-bs','3','--prompt','Terminologia: EMV, PSP, acquirer, acquiring, issuer, POS, tap, Nexi, OpenMove, Arriva, GTFS, Sadem, AEP, schema transit, check-out, multipasseggero.'])]:
 start=time.perf_counter()
 with (out/f'{name}.log').open('w') as log:
  proc=subprocess.run(base+opts+['-of',str(out/name)],stdout=log,stderr=log)
 elapsed=round(time.perf_counter()-start,3)
 row={'name':name,'seconds':elapsed,'returncode':proc.returncode,'options':opts}
 if proc.returncode==0:
  data=json.loads((out/f'{name}.json').read_text()); segs=data['transcription']; texts=[s['text'].strip() for s in segs]
  row.update(segments=len(segs),words=len(' '.join(texts).split()),adjacent_duplicate_segments=sum(a==b for a,b in zip(texts,texts[1:])),gestire_repetitions=' '.join(texts).lower().count('ah, è più da gestire'))
 runs.append(row); print(json.dumps(row),flush=True)
 if proc.returncode: break
(out/'benchmark.json').write_text(json.dumps(runs,indent=2))
