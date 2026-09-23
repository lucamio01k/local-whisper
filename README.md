# Local Whisper

Applicazione web locale per la trascrizione audio/video con riconoscimento speaker.  
**Tutto gira in locale — nessun dato inviato a server esterni.**

---

## Stack

| Layer     | Tecnologia                          |
|-----------|-------------------------------------|
| Backend   | Python · FastAPI · uvicorn          |
| Trascrizione | whisper.cpp (Metal) / faster-whisper     |
| Diarizzazione | pyannote.audio 4.0.5 / community-1              |
| YouTube   | yt-dlp                              |
| Frontend  | React 18 · Vite · Tailwind CSS      |

---

## Requisiti

- **Python 3.12**
- **Node.js 18+** (con npm)
- **ffmpeg** installato nel PATH (usato da yt-dlp per convertire l'audio)

```bash
# macOS
brew install python@3.12 node ffmpeg

# Ubuntu/Debian
sudo apt install python3.12 python3.12-venv nodejs npm ffmpeg
```

---

## Avvio rapido

```bash
git clone <repo-url>
cd local-whisper
./setup.sh
./start.sh
```

`setup.sh` prepara Python 3.12, installa dipendenze bloccate in `backend/requirements.lock.txt` e usa `npm ci`.
`start.sh` avvia backend e frontend senza reinstallazioni. `restart.sh` arresta soltanto processi del progetto sulle porte 8000/5173 e riavvia.

Apri **http://localhost:5173** nel browser.

---

## CLI locale

La CLI elabora un solo file locale in foreground: non avvia frontend né server HTTP.
Per ora eseguila dalla root del progetto, dopo `./setup.sh`:

```bash
.venv/bin/python -m backend.cli transcribe ~/Downloads/riunione.mp3
```

L'output predefinito è `./local-whisper-output/riunione/transcript.txt`. La cartella
deve essere nuova, così una trascrizione esistente non viene mai sovrascritta.

```bash
# Export espliciti; --format è ripetibile
.venv/bin/python -m backend.cli transcribe ~/Downloads/riunione.mp3 \
  --output ~/Documents/trascrizioni/riunione-settembre \
  --format txt --format srt --format json --language it --speakers 4

# Override delle preferenze salvate dall'app
.venv/bin/python -m backend.cli transcribe audio.m4a --backend whisper_cpp --profile quality --no-diarize
```

Formati disponibili: `txt`, `srt`, `vtt`, `md`, `csv`, `json`. Senza `--format`
viene creato soltanto TXT. La CLI legge `config.json` ma non lo modifica; `HF_TOKEN`,
se presente nell'ambiente, ha precedenza sul token locale e non viene mostrato. Input,
artefatti temporanei e trascrizioni non vengono aggiunti a Git.

L'installazione del comando globale `local-whisper` in `~/bin` e l'eventuale skill per
agenti saranno aggiunte in una fase separata.

---

## Token HuggingFace (per la diarizzazione)

La diarizzazione speaker usa [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1).  
Alla prima esecuzione il modello viene scaricato da HuggingFace Hub.

### Passi

1. Crea un account su [huggingface.co](https://huggingface.co) (gratuito)
2. Genera un token: **Settings → Access Tokens → New token** (tipo *Read*)
3. Accetta i termini d'uso del modello:
   - [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
   - [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
4. Nell'app vai su **Impostazioni** e incolla il token

Il token viene salvato in `config.json` (solo in locale).

> **Nota:** Se non hai il token, la diarizzazione viene disabilitata automaticamente e tutti i segmenti vengono assegnati a "Speaker 1".

---

## Modelli Whisper

I modelli si scaricano dalla pagina principale dell'app.  
Vengono salvati in `models_cache/` (formato HuggingFace Hub).

| Modello         | Dimensione | Velocità | Qualità  |
|-----------------|-----------|----------|---------|
| tiny            | 75 MB     | ★★★★★   | ★★☆☆☆  |
| base            | 145 MB    | ★★★★☆   | ★★★☆☆  |
| small           | 465 MB    | ★★★☆☆   | ★★★★☆  |
| medium          | 1.5 GB    | ★★☆☆☆   | ★★★★☆  |
| large-v2        | 3.1 GB    | ★☆☆☆☆   | ★★★★★  |
| large-v3        | 3.1 GB    | ★☆☆☆☆   | ★★★★★  |
| large-v3-turbo  | 800 MB    | ★★★☆☆   | ★★★★★  |

---

## Apple Silicon (M-series)

Su Apple Silicon, whisper.cpp usa Metal. faster-whisper/CTranslate2 usa CPU con int8; non supporta MPS. Pyannote può usare MPS o CPU.

Profilo Qualità whisper.cpp abilita DTW e disabilita Flash Attention perché incompatibili in questa versione. Tempi DTW sperimentali: verificarli sul parlato.

---

## Formati supportati

**Input:** mp3, wav, m4a, mp4, mov, ogg, opus, webm  
**Export:** TXT, SRT, VTT, Markdown, CSV

---

## Struttura del progetto

```
local-whisper/
├── backend/
│   ├── main.py          # FastAPI app (tutti gli endpoint)
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── pages/
│   │   │   ├── TranscribePage.jsx
│   │   │   └── SettingsPage.jsx
│   │   └── components/
│   │       ├── ModelSelector.jsx
│   │       ├── FileUpload.jsx
│   │       ├── AudioPlayer.jsx
│   │       ├── TranscriptView.jsx
│   │       └── ExportPanel.jsx
│   └── package.json
├── models_cache/        # modelli scaricati
├── uploads/             # file audio temporanei (per job)
├── config.json          # token HF + preferenze (creato automaticamente)
└── start.sh
```

---

## API Backend

| Metodo | Path                              | Descrizione                        |
|--------|-----------------------------------|------------------------------------|
| GET    | `/api/health`                     | Stato del server                   |
| GET    | `/api/models`                     | Lista modelli + stato download     |
| GET    | `/api/models/{name}/download`     | Download modello (SSE progress)    |
| GET    | `/api/config`                     | Leggi configurazione               |
| POST   | `/api/config`                     | Salva configurazione               |
| POST   | `/api/youtube/info`               | Info video YouTube                 |
| POST   | `/api/transcribe`                 | Avvia job di trascrizione          |
| GET    | `/api/jobs/{id}`                  | Stato job                          |
| GET    | `/api/jobs/{id}/events`           | SSE progress stream                |
| POST   | `/api/jobs/{id}/speakers`         | Rinomina speaker                   |
| GET    | `/api/audio/{id}`                 | Serve file audio                   |
| GET    | `/api/jobs/{id}/export/{fmt}`     | Esporta trascrizione               |

Documentazione interattiva: http://localhost:8000/docs

## Revisione qualità e valutazione

Nuova pipeline **Per parola (sperimentale)** selezionabile nelle opzioni, insieme al glossario. In profilo Qualità usa DTW; il modello predefinito non cambia automaticamente.

Aprire una trascrizione → **Revisione qualità** per correggere testo/speaker, dividere alle parole, creare proposte di ritrascrizione e ripristinare versioni. Modifiche manuali al testo o ai tempi invalidano il vecchio allineamento; raw originale resta disponibile.

- `./scripts/check_quality.sh`: test backend e build frontend.
- `./scripts/download_quality_models.sh`: large-v3 e Silero VAD locali.
- `./scripts/benchmark_quality.sh`: campione fisso di 600 secondi, tre ripetizioni, VAD acceso/spento.
- [Guida tecnica e limiti](docs/quality-v1.md): versioni, API, cache e riferimento umano.
