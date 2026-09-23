# Riferimento CLI Local Whisper

Controlla sempre prima versione effettiva:

```bash
local-whisper transcribe --help
```

Se launcher globale non è configurato, esegui dalla root progetto:

```bash
.venv/bin/python -m backend.cli transcribe INPUT
```

## Claude Cowork e bridge locale

Una skill contiene istruzioni: non rende automaticamente disponibile `local-whisper` nel sandbox cloud o nel `PATH` del bridge locale. Per questo Mac, launcher e progetto sono:

```text
/Users/lucamiotti/scripts/bin/local-whisper
/Users/lucamiotti/scripts/_luca/local-whisper
```

Concedi a Claude accesso in lettura/esecuzione a `/Users/lucamiotti/scripts` e accesso in scrittura alla directory che conterrà input/output, per esempio `/Users/lucamiotti/Downloads/test-cluade`. Poi esegui percorso assoluto, senza dipendere da `which`:

```bash
/Users/lucamiotti/scripts/bin/local-whisper transcribe \
  "/Users/lucamiotti/Downloads/test-cluade/audio.mp3" \
  --output "/Users/lucamiotti/Downloads/test-cluade/output-audio"
```

Se accesso a `scripts` non è autorizzato, chiedilo esplicitamente. Non cercare di reinstallare, usare `pip`, creare altro launcher o sostituire Local Whisper con WhisperX/API cloud. Se installazione cambia posizione, chiedi percorso al proprietario oppure controlla launcher documentato; non dedurlo da ricerche incomplete con `find` o da `PATH` del bridge.

## Uso comune

Trascrizione locale con impostazioni salvate e TXT:

```bash
local-whisper transcribe "/percorso/riunione.m4a"
```

Trascrizione italiana con output esplicito:

```bash
local-whisper transcribe "/percorso/riunione.m4a" --language it --output "/percorso/output-riunione"
```

Diarizzazione con numero speaker noto:

```bash
local-whisper transcribe "/percorso/intervista.mp3" --diarize --speakers 2 --output "/percorso/output-intervista"
```

Sottotitoli e testo:

```bash
local-whisper transcribe "/percorso/video.mp4" --format srt --format vtt --format txt --output "/percorso/sottotitoli-video"
```

Risultato strutturato per automazione:

```bash
local-whisper transcribe "/percorso/chiamata.wav" --format json --output "/percorso/dati-chiamata"
```

## Flag

| Flag | Uso |
| --- | --- |
| `--output DIRECTORY` | directory finale; deve essere nuova o libera |
| `--format FORMAT` | ripetibile: `txt`, `srt`, `vtt`, `md`, `csv`, `json`; default `txt` |
| `--language CODICE` | lingua nota, per esempio `it`, `en`, `fr` |
| `--model MODEL` | override modello: `tiny`, `base`, `small`, `medium`, `large-v2`, `large-v3`, `large-v3-turbo` |
| `--backend BACKEND` | override: `whisper_cpp` o `faster_whisper` |
| `--profile PROFILO` | override: `fast`, `balanced`, `quality` |
| `--diarize` / `--no-diarize` | forza o disabilita speaker labels |
| `--speakers N` | speaker attesi, da 1 a 20; usa con `--diarize` |

## Esito e limiti

- Input è un solo file audio/video locale per comando.
- Errore input o ASR: codice uscita `1`, nessun export finale.
- Errore sola diarizzazione: export trascrizione, avviso su stderr, codice uscita `0`.
- Output finale non viene sovrascritto: nuova esecuzione richiede directory output diversa.
- `HF_TOKEN` ambientale ha precedenza sul token salvato; non mostrare mai valore.
