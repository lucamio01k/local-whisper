# Local Whisper

Applicazione web locale per la trascrizione audio/video con riconoscimento speaker.  
**Trascrizione e diarizzazione eseguite in locale.** Download modelli contatta HuggingFace e altri repository; sorgenti YouTube contattano YouTube. File audio locali non vengono caricati su servizi di trascrizione.

---

## Stack

| Layer     | Tecnologia                          |
|-----------|-------------------------------------|
| Backend   | Python · FastAPI · uvicorn          |
| Trascrizione | whisper.cpp (Metal) / faster-whisper / Qwen3-ASR (MLX, Apple Silicon) |
| Diarizzazione | pyannote.audio 4.0.5 / community-1              |
| YouTube   | yt-dlp                              |
| Frontend  | React 18 · Vite · Tailwind CSS      |

---

## Requisiti

- **Python 3.12**
- **Node.js 18+** (con npm)
- **ffmpeg** installato nel PATH (normalizzazione audio/video e conversione YouTube)
- **macOS Apple Silicon** per Qwen/MLX e percorso consigliato whisper.cpp/Metal. Backend faster-whisper disponibile su altre piattaforme; launcher `.app` e `restart.sh` richiedono macOS.

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

`setup.sh` prepara Python 3.12, installa dipendenze bloccate in `backend/requirements.lock.txt` e usa `npm ci`. Su Mac Apple Silicon crea anche `.venv-qwen` con MLX Audio.
`start.sh` avvia backend e frontend senza reinstallazioni. Su macOS `restart.sh` arresta processi del progetto sulle porte 8000/5173 e riavvia tramite launcher `.app`. Prima di riavviare, attendi o annulla elaborazioni attive; pausa mantiene worker e memoria.

`setup.sh` non compila whisper.cpp: per motore Metal serve `whisper-cli` nel PATH, `WHISPER_CPP_BIN` oppure build locale in `whisper.cpp/build/bin/`. Senza binario, seleziona faster-whisper nelle impostazioni. Modelli scaricati per un motore non sono automaticamente disponibili per altri motori.

Su altre piattaforme, avvia in due terminali dalla root: `.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000` e `cd frontend && npm run dev`. Porte 8000/5173 devono essere libere; frontend non cambia porta automaticamente.

Nella schermata Trascrivi scegli solo il modello: su Apple Silicon i modelli Whisper usano automaticamente whisper.cpp/Metal, Qwen usa MLX. Il motore Whisper si può forzare nelle impostazioni avanzate. Se whisper.cpp fallisce durante l'esecuzione, l'app propone una riprova esplicita con faster-whisper; il job originale resta nello storico. faster-whisper resta come alternativa da rivalutare in futuro, senza una data di rimozione.

Per provare Qwen, scegli **Qwen3-ASR (MLX)** nel selettore modelli e premi **Scarica**: app salva localmente modello 1.7B e allineatore. I modelli occupano circa 3,6 GB su disco. Per speaker multipli è consigliata assegnazione **Per parola**. Glossario non viene applicato a Qwen. La pausa mantiene modello in RAM; **Annulla** termina worker e libera memoria.

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

Modello predefinito: [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1). Modello 3.1 disponibile come alternativa nelle impostazioni.
Alla prima esecuzione il modello viene scaricato da HuggingFace Hub.

### Passi

1. Crea un account su [huggingface.co](https://huggingface.co) (gratuito)
2. Genera un token: **Settings → Access Tokens → New token** (tipo *Read*)
3. Accetta i termini d'uso del modello:
   - [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) per modello predefinito
   - Per alternativa 3.1: [speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) e [segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
4. Nell'app vai su **Impostazioni** e incolla il token

Il token viene salvato in `config.json` (solo in locale).

Senza token, disattiva diarizzazione prima dell'avvio. Richieste con diarizzazione attiva vengono rifiutate con messaggio esplicito. Errore nella diarizzazione successiva conserva trascrizione e permette nuova diarizzazione.

---

## Modelli Whisper

I modelli si scaricano dalla pagina principale dell'app.  
Vengono salvati in `models_cache/`: GGML per whisper.cpp, HuggingFace/CTranslate2 per faster-whisper. Dimensioni dipendono dal motore; selettore mostra dimensioni disponibili. Velocità e qualità dipendono da audio e hardware: tabella seguente indica orientamento, non benchmark comparabile.

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

Verifica `large-v3` del 6 ottobre 2026: modello GGML riscaricato dal repository ufficiale `ggerganov/whisper.cpp`, dimensione 3.095.033.483 byte e SHA-256 `64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2` verificati. File precedente aveva stesso checksum: ripetizioni osservate nello storico non dipendevano da download corrotto. Test su due estratti problematici da 5 e 2 minuti, profilo Bilanciato, non hanno riprodotto loop massivi; confronto Turbo completato. Su estratto da 5 minuti verificato anche rilevamento lingua automatico. Questo non garantisce assenza di ripetizioni su registrazioni intere: controlla risultato e segnalazioni di revisione; Turbo/Qwen restano alternative.

### Qwen su audio lunghi

Qwen elabora blocchi separati di 30 secondi, con massimo 1.024 token per blocco e cache MLX di 128 MiB. Supervisore controlla memoria effettiva del processo su macOS (incluse allocazioni Metal): oltre 4 GiB arresta worker con errore esplicito. Controllo ogni secondo; limite non comprende altre app o successiva diarizzazione Pyannote.

Se caricamento, trascrizione di un blocco o allineamento non avanzano entro 5 minuti, worker viene arrestato. Pausa manuale non conta nel timeout. Progresso distingue trascrizione e allineamento parole; log `uploads/<job_id>/qwen_worker.log` registra blocchi, tempi e memoria MLX. Se un blocco esaurisce token, Qwen lo divide in parti da 15 e, se necessario, 7,5 secondi; persistente generazione eccessiva produce errore senza accettare testo troncato.

`qwen_partial.json` conserva blocchi ASR completati; `qwen_alignment.json` conserva allineamento parole completato. Dopo errore o annullamento, pulsante **Riprendi dai blocchi salvati** riusa checkpoint verificando audio, modello e lingua. Modello allineatore diverso invalida solo allineamento. Durante Qwen, interfaccia mostra memoria processo/limite e tempo residuo stimato della fase corrente (trascrizione o allineamento), dopo almeno tre blocchi misurati. Stima esclude successiva diarizzazione.

Job caricati vengono salvati prima di accodare elaborazione. Dopo interruzione del server, job incompleto viene mostrato come errore; checkpoint Qwen resta riprendibile. Job annullati restano nell'archivio. Altri motori richiedono nuovo avvio dopo interruzione. Elaborazioni pesanti sono serializzate per limitare uso simultaneo delle risorse.

Per ridurre tempi, disattiva tempi parola Qwen e usa diarizzazione per segmento. Per priorità velocità su Apple Silicon, prova `large-v3-turbo` con whisper.cpp, profilo Bilanciato. Qwen e Whisper possono dare risultati diversi: confronta estratto prima di elaborare riunione intera. Riavvio sviluppo: `./restart.sh`.

### Progresso diarizzazione

Percentuale della fase corrente usa contatori reali pyannote per segmentazione audio e analisi voci. Raggruppamento speaker e altre fasi prive di contatori mostrano solo nome fase. Percentuale non rappresenta stima del tempo residuo dell'intera diarizzazione. Cache dei turni già disponibile salta inferenza e passa direttamente ad assegnazione speaker al testo.

## Miglioramenti completati

- [x] Ripresa Qwen dai blocchi salvati dopo errore o annullamento.
- [x] Tempo residuo stimato dalla velocità dei blocchi completati.
- [x] Memoria corrente e limite visibili nell'interfaccia.

---

## Formati supportati

**Input:** mp3, wav, m4a, mp4, mov, mpeg, mpg, ogg, opus, webm<br>
**Export web:** TXT, SRT, VTT, Markdown, CSV. CLI aggiunge JSON.

---

## Struttura del progetto

```
local-whisper/
├── backend/
│   ├── main.py          # API, coda e supervisione job
│   ├── engine.py        # inferenza/cache diarizzazione
│   ├── qwen_worker.py   # MLX isolato e checkpoint
│   ├── review.py        # revisioni/proposte qualità
│   ├── storage.py       # revisioni atomiche
│   ├── cli.py           # CLI locale
│   └── requirements.lock.txt
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
| POST   | `/api/jobs/{id}/pause`            | Pausa fase compatibile             |
| POST   | `/api/jobs/{id}/resume`           | Riprendi fase in pausa             |
| POST   | `/api/jobs/{id}/cancel`           | Annulla elaborazione               |
| POST   | `/api/jobs/{id}/resume-qwen`      | Ripresa checkpoint Qwen            |
| POST   | `/api/jobs/{id}/diarize`          | Nuova diarizzazione                |
| GET    | `/api/history`                   | Archivio job                       |
| POST   | `/api/jobs/{id}/speakers`         | Rinomina speaker                   |
| GET    | `/api/audio/{id}`                 | Serve file audio                   |
| GET    | `/api/jobs/{id}/export/{fmt}`     | Esporta trascrizione               |

Documentazione interattiva: http://localhost:8000/docs

## Revisione qualità e valutazione

Nuova pipeline **Per parola (sperimentale)** selezionabile nelle opzioni, insieme al glossario. In profilo Qualità usa DTW; il modello predefinito non cambia automaticamente.

Aprire una trascrizione → **Revisione qualità** per correggere testo/speaker, dividere alle parole, creare proposte di ritrascrizione e ripristinare versioni. Modifiche manuali al testo o ai tempi invalidano il vecchio allineamento; raw originale resta disponibile.

- `./scripts/check_quality.sh`: test backend, test opzioni frontend e build produzione.
- `./scripts/download_quality_models.sh`: large-v3 e Silero VAD locali.
- `./scripts/benchmark_quality.sh`: campione fisso di 600 secondi, tre ripetizioni, VAD acceso/spento.
- [Guida tecnica e limiti](docs/quality-v1.md): versioni, API, cache e riferimento umano.

## Prima del push e del branch esperimenti

1. Esegui `./scripts/check_quality.sh` e `git diff --check`.
2. Rivedi `git status --short`: includi nuovi worker, moduli e test; escludi config/token, audio, checkpoint, output CLI e report Qwen locali tramite `.gitignore`.
3. Commit e push di `main`; crea branch esperimenti dal commit verificato.

Controllo stabilità del 6 ottobre 2026: 61 test backend, 2 test frontend, build produzione, sintassi script e `git diff --check` passati. Copertura regressioni su ripresa concorrente, recupero dopo riavvio, pulizia worker, storico annullati e nomi export Unicode. Controllo browser su sorgenti/opzioni, archivio e risultato lungo con player e revisione; viewport compatto e desktop. Cinque formati export web verificati via API. Riavvio riuscito; smoke test reale di 15 secondi da MPEG con whisper.cpp/Metal e output CLI TXT/SRT/JSON completato. Resta necessario benchmark con riferimento umano per qualità ASR/speaker: test software non garantiscono accuratezza. Installazione da macchina pulita e nuovi download YouTube/modelli non verificati in questo controllo.
