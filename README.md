# Local Whisper

Applicazione web locale per la trascrizione audio/video con riconoscimento speaker.  
**Tutto gira in locale — nessun dato inviato a server esterni.**

---

## Stack

| Layer     | Tecnologia                          |
|-----------|-------------------------------------|
| Backend   | Python · FastAPI · uvicorn          |
| Trascrizione | faster-whisper (CTranslate2)     |
| Diarizzazione | pyannote.audio 3.x              |
| YouTube   | yt-dlp                              |
| Frontend  | React 18 · Vite · Tailwind CSS      |

---

## Requisiti

- **Python 3.10+**
- **Node.js 18+** (con npm)
- **ffmpeg** installato nel PATH (usato da yt-dlp per convertire l'audio)

```bash
# macOS
brew install python@3.11 node ffmpeg

# Ubuntu/Debian
sudo apt install python3.11 python3.11-venv nodejs npm ffmpeg
```

---

## Avvio rapido

```bash
git clone <repo-url>
cd local-whisper
./start.sh
```

Lo script:
1. Crea un virtualenv Python in `.venv/`
2. Installa tutte le dipendenze Python (`backend/requirements.txt`)
3. Installa le dipendenze Node (`frontend/`)
4. Avvia il backend su `http://localhost:8000`
5. Avvia il frontend su `http://localhost:5173`

Apri **http://localhost:5173** nel browser.

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

CTranslate2 (usato da faster-whisper) supporta MPS (Metal) a partire dalla versione 4.x.  
Lo script imposta automaticamente `device="auto"` che seleziona Metal se disponibile.

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
