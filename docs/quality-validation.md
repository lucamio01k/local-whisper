# Verifica pipeline qualità — 9 settembre 2026

Pipeline selezionabile, non promossa come nuovo default. Storico non rielaborato; audio originali e raw conservati.

## Verifiche funzionali

- 20 test automatici: offset e lingua, parametri dei due backend, worker isolati, parole/DTW, attribuzione speaker, cache, coda e annullamento, revisioni atomiche, conflitti, proposte, ripristino, export legacy e scoring sintetico.
- Build frontend completata; controllo sintassi script shell e `git diff --check` completati.
- `pip check`: dipendenze coerenti nell'ambiente Python 3.12 verificato.
- Prova GPU su copia di 30 secondi: turbo DTW + community-1/MPS, 45 parole, 7 segmenti, 13,47 secondi totali. Seconda diarizzazione con cache hit.
- Worker faster-whisper reale su copia di 5 secondi, modello locale `small`, CPU/int8: 2 segmenti, 8 parole, durata ffprobe 5 s; caricamento 3,13 s e ASR 8,81 s. Prova funzionale, non confronto prestazionale: contemporanea a diarizzazione del benchmark.
- Verifica browser su copia isolata: revisione, modifica speaker, ripristino; corretto aggiornamento dei campi dopo cambio versione.
- Percorso HTTP caricamento → proposta → applicazione → ripristino coperto con ASR simulato, oltre alla prova del motore reale.
- Proposta reale turbo DTW su copia di 30 secondi: pronta con 45 unità parola; versione attiva invariata fino all'applicazione, applicazione HTTP 200, ripristino HTTP 200 in nuova revisione.

Artefatti di prova in `reports/implementation-checks/`; esclusi da Git perché contengono dati degli audio.

## Interpretazione del benchmark

Campione fisso: 600 secondi in 11 finestre, passaggi critici e intervalli casuali con seed 20260909. Tre ripetizioni per baseline turbo, turbo DTW e large-v3 DTW, ciascuno con VAD spento/acceso: 198 esecuzioni ASR. Inferenza diarizzazione sulle registrazioni complete e ritaglio dei turni sulle finestre; turni condivisi fra varianti.

I tempi includono un nuovo processo e caricamento modello per ciascuna esecuzione. Prima lettura e ripetizioni successive distinguono cache filesystem presumibilmente calda, senza svuotamento controllato della cache OS. Attività di sviluppo e stato termico possono influire sui risultati. DTW incluso nel tempo ASR, non misurabile separatamente dalla CLI corrente.

Parole con tempi rifiutati indicano segmenti nei quali almeno un'ancora manca, esce dai confini o rompe ordine temporale: non equivalgono a parole trascritte male. In questi casi assegnazione conservata a livello segmento. Problema particolarmente frequente con VAD; VAD resta spento nell'app.

FFmpeg segnala un pacchetto non decodificabile nella riunione: durata MP3 dichiarata 3086,361 s, WAV decodificato 3081,144 s. Controllo tramite correlazione delle forme d'onda di tutte le 11 finestre rispetto ai WAV completi, cercando entro ±15 secondi: offset rilevato zero per ogni campione, similarità normalizzata minima 0,9976. Finestre del confronto coerenti con turni ritagliati; discrepanza della durata non interpretata come silenzio trascritto o errore ASR.


Benchmark terminato con codice 0: 198/198 risultati ASR e 198/198 risultati con speaker. lesson: caricamento 0.66 s, inferenza 77.13 s su mps. meeting: caricamento 0.48 s, inferenza 498.29 s su mps. 

## Risultati temporali ASR

33 esecuzioni per riga (11 finestre × 3 ripetizioni). Mediane; RTF = secondi elaborazione / secondi audio.

| Variante | VAD | Tempo CLI, s | RTF | Caricamento, s | Parole con tempi rifiutati |
|---|---|---:|---:|---:|---:|
| baseline | no | 7.51 | 0.130 | 0.66 | — |
| baseline | sì | 7.11 | 0.119 | 1.04 | — |
| large-words | no | 27.23 | 0.501 | 2.18 | 16.7% |
| large-words | sì | 26.42 | 0.440 | 2.19 | 83.8% |
| turbo-words | no | 8.76 | 0.155 | 0.87 | 33.0% |
| turbo-words | sì | 8.28 | 0.153 | 0.96 | 81.0% |

Baseline senza parole: percentuale non applicabile. Per segmento con ancore incoerenti vengono rifiutati tutti i tempi parola; percentuale misura quindi copertura dell’allineamento utilizzabile, non errore lessicale. Dettagli prima lettura/ripetizioni in `reports/evaluation-v1/timing_summary.json`.

## Accuratezza e promozione

WER, DER e miglioramento attribuzione speaker **non certificati**: manca riferimento umano verificato e congelato. TXT forniti sono output automatici con nomi modificati, non verità di confronto. Conteggi diagnostici e tempi non sostituiscono valutazione del contenuto.

Restano verifica umana del campione, congelamento riferimento, scoring e controllo contestuale di cifre/negazioni. Nessun cambio automatico del modello predefinito. Procedura e comandi in [quality-v1.md](quality-v1.md).
