import os,time,json
from pathlib import Path
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD']='1'
root=Path(__file__).resolve().parents[2]; out=root/'reports/quality-2026-09-09'
start=time.perf_counter()
from pyannote.audio import Pipeline
import torch,subprocess
print('MPS',torch.backends.mps.is_available(),flush=True)
token=json.loads((root/'config.json').read_text()).get('hf_token')
pipeline=Pipeline.from_pretrained('pyannote/speaker-diarization-community-1',token=token)
pipeline.to(torch.device('mps' if torch.backends.mps.is_available() else 'cpu'))
loaded=time.perf_counter()
raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(root/'uploads/b12cfe14-e01a-41a6-afac-d18c31eb7046/diarization.wav'),'-f','f32le','-ac','1','-ar','16000','pipe:1'])
wave=torch.frombuffer(bytearray(raw),dtype=torch.float32).unsqueeze(0)
result=pipeline({'waveform':wave,'sample_rate':16000},num_speakers=2)
record={'load_seconds':round(loaded-start,3),'inference_and_decode_seconds':round(time.perf_counter()-loaded,3),'device':str(pipeline.device)}
for key in ['speaker_diarization','exclusive_speaker_diarization']:
 record[key]=[[float(t.start),float(t.end),sp] for t,_,sp in getattr(result,key).itertracks(yield_label=True)]
(out/'diarization_turns.json').write_text(json.dumps(record,indent=2)); print({k:(len(v) if isinstance(v,list) else v) for k,v in record.items()},flush=True)
