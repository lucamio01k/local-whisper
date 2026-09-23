---
name: local-whisper
description: Trascrivi o diarizza audio/video locale con Local Whisper CLI quando utente chiede trascrizione privata, sottotitoli, speaker o export TXT/SRT/VTT/CSV/JSON. Non usare per trascrizione cloud o editing audio non correlato.
---

# Local Whisper

Usa questa skill per trasformare file audio/video locali in trascrizioni, sottotitoli o dati strutturati, mantenendo elaborazione sul computer dell'utente.

## Trigger

Attivala per richieste come:

- "trascrivi questa registrazione localmente";
- "fammi SRT/VTT da questo video";
- "separa chi parla" o "diarizza intervista";
- "estrai JSON/CSV della trascrizione";
- "usa il mio Local Whisper invece di un servizio cloud".

Non attivarla per semplice editing, riproduzione o conversione audio senza richiesta di trascrizione, né per upload o servizi cloud.

## Principi operativi

- Preferisci `local-whisper transcribe INPUT` se comando è disponibile. Se non lo è, dalla root del progetto usa `.venv/bin/python -m backend.cli transcribe INPUT`.
- In un bridge locale (per esempio Claude Cowork), verifica prima che host sia Mac utente e che bridge abbia accesso a progetto e launcher. Skill non installa programmi, non trasferisce audio e non aggiunge directory al `PATH` del bridge.
- Prima di una nuova integrazione o se opzioni sembrano diverse, consulta `local-whisper transcribe --help`: CLI installata è fonte di verità. Non presumere dettagli interni di backend, modelli o flag.
- Di default CLI legge preferenze locali da `config.json`, produce TXT e applica diarizzazione secondo configurazione. Non modificare quella configurazione per una singola richiesta senza autorizzazione.
- Richiedi o rispetta directory output. Senza `--output`, output è `./local-whisper-output/<nome-file>/`; CLI non sovrascrive directory già esistente. Per rieseguire, scegli nuova directory o chiedi utente cosa fare.
- Per richieste agentiche o dati da elaborare, aggiungi `--format json`; JSON contiene risultato completo. Per consegna umana, scegli formato richiesto, non JSON per default.

## Privacy e dati sensibili

- Mantieni file in locale. Non fare upload, fallback cloud o chiamate a servizi di trascrizione senza autorizzazione esplicita.
- `HF_TOKEN` può essere presente in ambiente o `config.json`: non stamparlo, non inserirlo in log, prompt, output o commit.
- Non aggiungere a Git audio/video, directory di output, `config.json`, cache modelli, log o credenziali. Non leggere né ripetere contenuto di registrazioni oltre quanto serve alla richiesta.
- Se diarizzazione fallisce ma trascrizione riesce, conserva ed esporta trascrizione e segnala avviso; non dichiarare fallimento totale.

## Scelte

- `--diarize` forza speaker labels; `--no-diarize` le disabilita. `--speakers N` richiede `--diarize`.
- `--language it` evita rilevamento automatico quando lingua è nota. Usa solo codice lingua dichiarato o chiaramente deducibile dal file/contesto.
- `--model`, `--backend` e `--profile` sono override temporanei. Mantieni default configurato salvo richiesta di velocità, qualità o compatibilità.
- Formati disponibili e comandi pronti sono in [riferimento CLI](references/cli.md). Leggilo quando devi costruire comando o scegliere export.

## Adattabilità

Questa skill descrive intenzione (trascrivere localmente), non vincola un'implementazione. Se Local Whisper evolve, verifica help e README locale, conserva privacy e comportamento utente, poi usa equivalente supportato. Se CLI non è disponibile, fermati e spiega prerequisito: non sostituirla autonomamente con WhisperX, API esterne o altro strumento.
