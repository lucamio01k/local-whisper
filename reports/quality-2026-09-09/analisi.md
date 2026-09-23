**Local Whisper — analisi qualità, diarizzazione e prestazioni, 9 settembre 2026**

Margine concreto soprattutto nell'unione fra trascrizione e diarizzazione. Prima priorità: conservare e usare tempi delle parole, rispettare cambi speaker interni ai segmenti, evitare fusioni arbitrarie. Aumentare semplicemente beam size non garantisce testo migliore.

Analisi di codice, quattro TXT forniti, JSON e log dei job corrispondenti. Audio originali verificati identici alle copie dei job tramite SHA-256. Nuove prove sulla lezione completa; riunione lunga valutata tramite output e misure già salvate. Nessuna modifica a backend, frontend, configurazione o trascrizioni esistenti. Nessun audio inviato a servizi esterni.

TXT forniti trattati come risultati da valutare, non come riferimento umano certificato. Nessuna misura WER o DER: servono testo corretto e turni annotati/ascoltati. Non effettuato ascolto umano di riferimento. Conteggi di ripetizioni e disaccordi sono indicatori diagnostici, non percentuali di errore.

**Situazione misurata**

| Dato | Lezione Pawel | Riunione EMV |
|---|---:|---:|
| Durata file, ffprobe | 515,09 s | 3.086,36 s |
| Modello/backend | large-v3-turbo / whisper.cpp | large-v3-turbo / whisper.cpp |
| Lingua del job confrontato | it | it |
| Segmenti raw | 180 | 2.854 |
| Turni export speaker | 29 | 393 |
| Timestamp parole salvati | 0 | 0 |
| Testo conservato fra raw e speaker | sì, identico | sì, identico |
| Turno più lungo | 118,18 s | 186,08 s |
| Segmenti realmente a durata nulla | 3 | 6 |
| Segmenti che appaiono a durata nulla nel TXT | 4 | 685 |
| Coppie consecutive con testo identico | 14 | 41 |
| Tempo ASR storico | 31,31 s | 168,04 s |
| Tempo diarizzazione storico | 39,41 s | non registrato |

Tempi storici: metadati esistenti, non nuova misura. ASR già rapido: circa 16,5× e 18,4× tempo reale. Sulla lezione, diarizzazione pesa più dell'ASR. Il divario fra dimensioni dei TXT raw/speaker dipende dalla fusione e dalla riduzione dei timestamp, non da parole eliminate.

**Diarizzazione: problemi e ordine di intervento**

1. **Speaker assegnato per segmento, mai per parola.** `_apply_diarization` assegna un solo speaker a tutto il segmento ASR. Anche faster-whisper in profilo qualità produce parole che questa funzione ignora. Cambi rapidi, domande, conferme e sovrapposizioni possono quindi finire sotto voce sbagliata. Riferimento: [backend/main.py:611](/Users/lucamiotti/scripts/_luca/local-whisper/backend/main.py:611).

   Nuova inferenza locale community-1, MPS, due speaker, sulla lezione: 181 turni standard, 126 esclusivi. Fra 180 segmenti ASR, 55 intersecano almeno 250 ms di ciascuno dei due speaker nei turni standard; 25 ancora nei turni esclusivi. Nei primi possono contribuire sovrapposizioni; gli esclusivi mostrano cambi di etichetta entro segmento. Non sono 25 errori verificati, ma 25 segmenti che l'attuale schema non può rappresentare fedelmente rispetto al modello.

   Intervento: allineamento parole → assegnazione speaker alle parole → divisione ai cambi → fusione per sola presentazione. Conservare raw invariato e tempi originali. Per etichette incerte usare stato esplicito e revisione, non speaker inventato.

2. **Overlap calcolato per singolo turno, non sommato per speaker.** Vince il turno individuale più lungo. Esempio: A parla 2 s + 2 s, B 3 s; oggi vince B. Nella prova reale, sommare per speaker cambia assegnazione di 2 segmenti usando turni standard. Quattro segmenti non intersecano alcun turno: oggi vengono attribuiti automaticamente a `SPEAKER_00`. Serve gestione di tempi nulli, assenza di evidenza e pareggi. Sommare overlap non sostituisce divisione alle parole.

3. **Output esclusivo community-1 disponibile ma ignorato.** `_diarization_turns` legge solo `speaker_diarization`. La versione installata, pyannote.audio 4.0.5, espone anche `exclusive_speaker_diarization`. Usarlo per riconciliazione con testo, mantenendo turni standard per segnalare voci sovrapposte. Non elimina da solo il problema dei segmenti lunghi e non recupera parole che Whisper non ha riconosciuto. Riferimento: [backend/main.py:250](/Users/lucamiotti/scripts/_luca/local-whisper/backend/main.py:250).

4. **“Conservative” può cancellare interlocutori reali poco presenti.** Lo speaker viene protetto solo se supera TUTTE le soglie: 3 segmenti, 18 s, 4% durata, 4% segmenti. In prova sintetica, B con 5 interventi e 25 s viene interamente unito ad A perché sotto 4% della durata. Fusione basata su vicinanza temporale, senza verifica della voce. Consigliato disabilitare fusione automatica basata soltanto su quota di parlato; eventuale proposta di fusione richiede evidenza acustica e revisione. Riferimento: [backend/main.py:671](/Users/lucamiotti/scripts/_luca/local-whisper/backend/main.py:671).

   Precisazione: nei due job esaminati `expected_speakers` vale 2 e 4; questa fusione viene quindi saltata. Difetto reale della modalità automatica, ma non causa dei risultati di questi due job. Numero esatto utile quando noto; altrimenti meglio supportare intervallo minimo/massimo anziché forzare numero arbitrario.

5. **Ri-diarizzazione ignora CPU/MPS scelto.** `_run_rediarization` salva preferenza ma non la passa a `_run_diarization_worker`, che usa `auto`. Correzione piccola e verificabile con worker simulato. Riferimento: [backend/main.py:1235](/Users/lucamiotti/scripts/_luca/local-whisper/backend/main.py:1235).

**Qualità testo e timestamp**

- **Lingua automatica difettosa su whisper.cpp.** Senza lingua esplicita il comando non passa `-l`; CLI installato ha default `en`, non `auto`. Un altro job della stessa lezione registra infatti inglese. Passare esplicitamente `-l auto`, mantenendo `it` quando richiesto. Per registrazioni italiane note conviene selezionare italiano già ora. [backend/main.py:951](/Users/lucamiotti/scripts/_luca/local-whisper/backend/main.py:951).
- **Profilo qualità incompleto su whisper.cpp.** Cambia beam size, ma `words` resta sempre vuoto e `best_of` non viene passato alla CLI: rimane default 5 anche se metadati riportano 1/3. Distinguere parametri realmente applicati e capacità del backend. [backend/main.py:905](/Users/lucamiotti/scripts/_luca/local-whisper/backend/main.py:905).
- **Timestamp token già ottenibili.** Prova sui primi 60 s con `-ojf`: JSON con 224 token, tempi e probabilità, circa 5,99 s di tempo totale interno CLI. Include token speciali e frammenti di parola; serve filtrarli e ricomporli. Timestamp approssimati, `t_dtw=-1`: non è allineamento forzato e la sola presenza dei tempi non ne dimostra precisione. Confrontare allineamento DTW/forced alignment su campione prima di promuoverlo. Non occorre cambiare subito backend per iniziare.
- **Parser tempi fragile.** Offset numerici interpretati come ms solo oltre 1000: 500 ms diventa 500 s. Catena di `or` considera 0 come valore mancante. Riprodotto con helper originali isolati. Nei JSON forniti i timestamp testuali normalmente evitano errore sugli offset; zero iniziale viene poi recuperato dal fallback. Bug da correggere, non spiegazione generale degli errori ascoltati. [backend/main.py:844](/Users/lucamiotti/scripts/_luca/local-whisper/backend/main.py:844).
- **Lessico tecnico.** Raw contiene forme come “essure”, “embertitore”, “acqua”, “INV”, “best fair”. Glossario per progetto e prompt ASR sono candidati sensati: EMV, PSP, acquirer, issuer, POS, GTFS, OpenMove. Le forme corrette vanno confermate dal parlato; nomi, cifre e negazioni non vanno sostituiti ciecamente. Prompt utile come suggerimento, non dizionario di sostituzione garantito.
- **Ripetizioni.** Fra 6:40 e 6:50 della lezione compare nove volte “Ah, è più da gestire…”. È già nel raw. Segnalare ripetizioni, bassa probabilità e intervalli sospetti, quindi ritrascrivere soltanto quelle finestre con contesto e parametri alternativi. Valutare VAD conservativo: entrambi i rami non lo abilitano esplicitamente. Può aiutare su silenzio/rumore, ma soglie aggressive possono eliminare brevi interventi.
- **Export leggibile.** Millisecondi nei TXT di revisione; paragrafi per frase e limite indicativo 30–45 s nei blocchi di lettura. SRT/VTT devono usare sottotitoli brevi, non i turni fusi che oggi arrivano a tre minuti. Mantenere separati dati di allineamento e formattazione.

**Nuovo confronto ASR sulla lezione completa**

Stesso WAV, modello locale non quantizzato `ggml-large-v3-turbo.bin`, italiano, 8 thread; esecuzioni seriali sulla macchina corrente con Metal. Una sola esecuzione per variante, nessun confronto statisticamente robusto; cache, temperatura e altri processi possono influire. Tempi wall-clock comprendono caricamento CLI/modello, escludono conversione WAV già disponibile e diarizzazione. `best_of` CLI lasciato al default 5 per riprodurre comando attuale.

| Variante | Tempo | Segmenti | Parole, split su spazi | Coppie consecutive identiche |
|---|---:|---:|---:|---:|
| Bilanciato, beam 3 | 37,07 s | 180 | 1.271 | 14 |
| Veloce, beam 1 | 30,26 s | 161 | 1.199 | 2 |
| Qualità, beam 5 | 37,77 s | 138 | 1.149 | 8 |
| Beam 3 + glossario | 34,87 s | 90 | 1.262 | 0 |

Veloce: circa 18% meno tempo in questa prova. Nessuna prova di maggiore accuratezza: cambiano anche contenuto e segmentazione. Qualità non risolve automaticamente ripetizioni; nel tratto critico compaiono ripetizioni di “che…”. Glossario evita duplicati consecutivi esatti ma conserva errori, ad esempio “issueware” e “emvertitore”. Meno segmenti può perfino peggiorare assegnazione speaker attuale. Confrontare parole rispetto a riferimento verificato, non conteggio righe.

Nuova diarizzazione community-1: caricamento/import 3,58 s, decodifica + inferenza 45,61 s, MPS. Non confronto diretto con tempo storico: misurazioni e perimetri diversi. Il margine maggiore per qualità non richiede necessariamente modello più pesante.

**Efficienza: interventi utili dopo correttezza**

- Un solo WAV normalizzato 16 kHz mono condiviso: oggi vengono creati `whisper_cpp.wav` e `diarization.wav`, poi FFmpeg decodifica di nuovo per pyannote. Conversioni ridondanti confermate; guadagno temporale non misurato. Preservare originale stereo: prima di downmix irreversibile valutare se canali contengano microfoni separati. Due canali nel file non dimostrano separazione dei parlanti.
- Salvare turni grezzi/esclusivi, modello effettivamente caricato, device effettivo, parametri e hash audio. Modificare assegnazione o impaginazione dovrebbe riusare inferenza, non rieseguire pyannote. Invalidare cache quando cambiano modello o numero speaker.
- Worker leggero dedicato: ora importa `backend.main`, che carica anche tutti i job da disco. Separare motore da FastAPI/storico. Valutare worker residente per caricamento modelli riutilizzabile, con rilascio dopo inattività: meno latenza, più RAM occupata. Nella prova diarizzazione il caricamento è circa 3,6 s, quindi non promette grandi guadagni su audio lunghi.
- Coda con concorrenza limitata: job diversi non dovrebbero saturare simultaneamente stessa GPU/RAM. ASR e diarizzazione teoricamente indipendenti, ma entrambi usano acceleratore su questo Mac; benchmark prima di parallelizzarli. Nessuna promessa di dimezzare tempi.
- Metriche separate per conversione, caricamento, inferenza ASR, inferenza speaker, allineamento e totale; aggiungerle anche a ri-diarizzazione. Durata reale da ffprobe, distinta da fine ultimo segmento.
- `start.sh` reinstalla/verifica dipendenze a ogni avvio; separare setup e avvio rapido, conservando `restart.sh` già presente. Bloccare versioni provate: requisiti attuali usano molti limiti inferiori senza lock. Il warning TorchCodec presente nei log non blocca pipeline grazie al decoder FFmpeg già implementato.
- Restare per ora su whisper.cpp/Metal e community-1. Valutare quantizzazione Q5 e large-v3 completo soltanto dopo benchmark qualità/tempo e con cache del modello; non necessario scaricare altri modelli per correggere difetti individuati.

**Sequenza proposta**

1. Correzioni certe: lingua auto, device nella ri-diarizzazione, parser tempi, parametri effettivi, conservazione turni e tempi di fase. Test piccoli su casi limite reali.
2. Diarizzazione: timestamp parole/allineamento, turni esclusivi per assegnazione, divisione ai cambi, sconosciuto per assenza di evidenza; rimuovere fusione automatica delle minoranze. Validazione su brevi conferme e interruzioni.
3. Qualità: glossario per progetto, rilevazione anomalie e ritrascrizione selettiva; confronto VAD acceso/spento. Export leggibile distinto dal raw.
4. Prestazioni: WAV condiviso, cache inferenza, worker leggero, coda; poi prove su modelli/quantizzazione.

Campione di riferimento consigliato: 4:15–4:45 e 6:20–7:10 della lezione; 0:20–1:05 e 5:15–6:40 della riunione, più finestre casuali e un tratto pulito lungo. Annotare parole, identità coerenti, confini speaker, sovrapposizioni. Per DER specificare collar e trattamento overlap; per testo WER e accuratezza di termini/cifre/negazioni; per speaker verificare anche parole attribuite alla voce corretta. Ripetere misure di velocità almeno tre volte separando avvio freddo/caldo. Questa validazione manca e impedisce dichiarazioni quantitative di miglioramento qualità.

Risultati e riproduzione: [audit.json](/Users/lucamiotti/scripts/_luca/local-whisper/reports/quality-2026-09-09/audit.json), [benchmark.json](/Users/lucamiotti/scripts/_luca/local-whisper/reports/quality-2026-09-09/benchmark.json), [run_benchmark.sh](/Users/lucamiotti/scripts/_luca/local-whisper/reports/quality-2026-09-09/run_benchmark.sh). Script riusa i job locali e modelli già presenti, sovrascrive solo artefatti di valutazione in questa cartella. [Variante glossario, sperimentale](/Users/lucamiotti/scripts/_luca/local-whisper/reports/quality-2026-09-09/glossary_experimental.txt): da confrontare con audio, non testo corretto certificato.
