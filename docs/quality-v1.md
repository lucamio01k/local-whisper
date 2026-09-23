# Pipeline qualità v1

Stato: selezionabile e sperimentale. Default storico `segments` conservato. Nessuna modifica automatica dei job esistenti o promozione di large-v3. Audio e testo restano locali; connessione serve soltanto al download dei modelli.

## Utilizzo

1. `./setup.sh` per preparare dipendenze; `./start.sh` o `./restart.sh` durante sviluppo. Ambiente bloccato su Python 3.12, dipendenze rilevate nel runtime macOS verificato.
2. Selezionare backend whisper.cpp, precisione **Per parola**, profilo **Qualità**. Glossario suggerisce lessico al modello; non corregge tramite sostituzioni.
3. Nella trascrizione aprire **Revisione qualità**. Segnalazioni indicano controlli utili, non errori certi. Ascoltare registrazione, correggere testo/speaker, dividere prima di una parola.
4. Ritrascrizione selettiva: massimo 60 secondi, contesto ±5 secondi. Confini estesi alle unità allineate esistenti. Segmenti legacy troppo lunghi richiedono rielaborazione completa: non vengono tagliati distribuendo arbitrariamente il testo.
5. Proposta completa ricalcola speaker e allineamento senza ereditare nomi della clusterizzazione precedente. Proposta di intervallo usa identità temporali della versione corrente. Applicazione esplicita, con controllo versione e ripristino.

Le proposte elaborano copie temporanee. Nessuna selezione automatica fra testi concorrenti. Ripristinare crea una nuova revisione che contiene il risultato precedente, preservando cronologia.

## Persistenza e compatibilità

- `transcript.json`: primo raw, immutabile dopo creazione.
- `revisions/<id>.json`: snapshot immutabile con segmenti e metadati di elaborazione, raw della versione e turni.
- `HEAD.json`: puntatore scritto atomicamente alla versione attiva; fonte autorevole dopo riavvio.
- `diarized.json` e `meta.json`: copie per compatibilità, non fonte autorevole quando HEAD esiste.
- Prima modifica di job legacy salva snapshot originario. Lettura non migra né riscrive file.
- `normalized.wav`: unica conversione condivisa, validata tramite SHA-256 originale.
- `turns_cache`: turni standard/esclusivi identificati da audio, modello, revisioni HF pyannote, dispositivo effettivo, numero/intervallo speaker e versioni pipeline/librerie. Parametri di presentazione non invalidano cache.

Nomi speaker sono dati della revisione. Non rappresentano riconoscimento biometrico persistente. Incertezza usa `speaker: null`. Vecchio `conservative` resta accettato ma non fonde più cluster basandosi sulla quota di parlato.

## API aggiuntive

Campi opzionali di `/api/transcribe`: `glossary`, `diarization_precision` (`segments`/`words`), `min_speakers`, `max_speakers`. `expected_speakers` esatto prevale. Config supporta glossario e precisione predefiniti. API modelli accetta `backend` per stato/download nel formato effettivamente usato.

- `PATCH /api/jobs/{id}/segments/{segment_id}`: `version`, testo/speaker, tempi oppure `split_word`.
- `GET /api/jobs/{id}/revisions`: elenco snapshot.
- `POST /api/jobs/{id}/restore`: `version`, `target_version`.
- `POST /api/jobs/{id}/proposals`: `version`, `start`, `end`, `model_name`, glossario opzionale; `full: true` per intera registrazione.
- `GET /api/jobs/{id}/proposals/{proposal_id}`: stato, originale, proposta.
- `POST .../cancel`: annulla copia di lavoro.
- `POST .../apply`: `version`; 409 se risultato attivo differisce dalla base.
- `GET /api/jobs/{id}/reference`: annotazione esportabile, marcata non verificata.

Vecchio endpoint speaker accetta `version` opzionale per compatibilità; UI nuova lo invia. Altri endpoint di revisione lo richiedono. Un solo job ASR/diarizzazione attivo nell'istanza backend, altri in coda. Avviare un solo worker uvicorn; coordinamento multiprocesso non implementato.

Whisper.cpp usa processo CLI, faster-whisper usa `backend.asr_worker`, pyannote usa `backend.diarize_worker`: processi rilasciati a fine elaborazione, senza importare applicazione o storico. Faster-whisper su Apple Silicon registra CPU/int8, perché CTranslate2 non supporta Metal.

## Tempi e limiti

JSON completo conserva probabilità/token e DTW. Le ancore DTW sono punti in centesimi di secondo, non intervalli; confini parola ricavati fra ancore e limitati al segmento. Token mancanti o tempi incoerenti segnalati: nessuna interpolazione uniforme del testo. DTW richiede `-nfa` nella build corrente.

Whisper.cpp misura caricamento e totale CLI. Totale include DTW: `alignment_seconds=null`, `alignment_included_in_asr=true` evita inventare un tempo indipendente non esposto dalla CLI. Pyannote misura caricamento/inferenza; assegnazione speaker misurata separatamente.

SRT/VTT: massimo due righe da 42 caratteri e 7 secondi per gruppi di parole normali, con divisione ai cambi speaker. Job legacy privi di parole conservano tempi e testo originali: possono superare limiti dei sottotitoli finché non rielaborati. Una singola parola eccezionalmente lunga o con durata patologica richiede revisione, non taglio arbitrario. TXT usa millisecondi e blocchi di lettura separati dai segmenti tecnici.

## Valutazione riproducibile

`./scripts/benchmark_quality.sh` genera 11 finestre per 600 secondi totali, seed 20260909, e conserva manifest con hash audio. Matrice: baseline turbo beam 3, turbo DTW beam 5, large-v3 DTW beam 5; VAD spento/acceso; tre ripetizioni. Ogni CLI ricarica modello: si distingue prima lettura da ripetizioni con cache filesystem presumibilmente calda, senza dichiarare cache OS svuotata. Esecuzioni riprendibili: risultati già completi non vengono rieseguiti.

Diarizzazione calcolata una volta sulle registrazioni complete, poi ritagliata sui campioni: numero speaker noto del file intero non viene forzato su finestre dove potrebbero parlare meno persone. Turni identici per tutte le varianti ASR.

Output in `reports/evaluation-v1/`, escluso dal versionamento perché contiene dati derivati dagli audio. Non confrontare numero di segmenti o ripetizioni come se fossero accuratezza.

`.venv/bin/python scripts/summarize_quality.py` riepiloga tempi osservati, RTF e parole con tempi incoerenti in `timing_summary.json`. RTF inferiore a 1 indica elaborazione più rapida della durata audio. Tempi DTW compresi nell'ASR; tabella non misura accuratezza.

Per riferimento umano:

1. Caricare WAV dei campioni nell'app come registrazioni separate, rielaborarli e verificarli ascoltando nella vista revisione.
2. Esportare annotazione, correggere/verificare parole e confini speaker; aggiungere termini tecnici da controllare. Impostare `verified: true` solo dopo verifica.
3. Importare ogni annotazione: `.venv/bin/python scripts/evaluate_quality.py import-reference --sample 01-lesson --annotation /percorso/annotazione.json`.
4. Congelare riferimento: `.venv/bin/python scripts/evaluate_quality.py freeze-reference`. Richiede testo, turni e parole di tutti i campioni. Hash congelato controllato dallo scoring.
5. Eseguire `.venv/bin/python scripts/evaluate_quality.py score`.

Scoring: WER normalizzato, DER collar 250 ms con overlap incluso, attribuzione speaker ai punti medi delle parole di riferimento, conteggi termini/cifre/negazioni. Questi ultimi sono indizi: uguaglianza dei conteggi non certifica stesso significato. `aggregate_scores.json` aggrega conteggi pesati; `content_review.json` raccoglie verifica contestuale di cifre/negazioni per caso. Ricalcolando score, `promotion.json` segnala candidati ammissibili senza cambiare configurazione. Nessuna promozione automatica; serve revisione contestuale di cifre/negazioni oltre a riduzione errori speaker e WER non peggiorato.

Senza riferimento verificato, WER/DER non vengono prodotti. I TXT forniti contengono solo rinomina speaker e non soddisfano questo requisito.

Il DER del confronto usa i segmenti finali assegnati nel transcript, dunque misura il risultato dell'intera pipeline, inclusi buchi e attribuzioni dovuti all'ASR; non è il DER dei soli turni standard pyannote. Turni standard restano salvati per analisi separata delle sovrapposizioni.
