import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronUp, Users } from 'lucide-react'

export default function DiarizationPanel({
  enabled, onEnabledChange, speakerMode, onSpeakerModeChange,
  expectedSpeakers, onExpectedSpeakersChange, minSpeakers, onMinSpeakersChange,
  maxSpeakers, onMaxSpeakersChange, start, onStartChange, precision, onPrecisionChange,
  device, onDeviceChange, tokenConfigured, archived, errors,
}) {
  const [details, setDetails] = useState(false)
  const limits = minSpeakers && maxSpeakers ? `${minSpeakers}–${maxSpeakers} persone`
    : minSpeakers ? `almeno ${minSpeakers} persone` : maxSpeakers ? `massimo ${maxSpeakers} persone` : 'senza limiti impostati'
  const field = (id, label, value, change, error, optional = false) => (
    <div className="min-w-0">
      <label htmlFor={id} className="label">{label}</label>
      <input id={id} className="input w-full" type="number" min="1" max="20" step="1"
        value={value} onChange={e => change(e.target.value)} placeholder={optional ? 'Facoltativo' : '1–20'}
        aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} />
      {error && <p id={`${id}-error`} className="text-xs text-red-300 mt-1" role="alert">{error}</p>}
    </div>
  )
  return (
    <section className="card p-5 space-y-4" aria-labelledby="diarization-heading">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="diarization-heading" className="text-sm font-semibold text-gray-200 flex items-center gap-2"><Users size={17} className="shrink-0 text-brand-400" />Diarizzazione — chi parla</h2>
          <p className="text-xs text-gray-400 mt-1.5">Separa gli interventi delle diverse persone.</p>
        </div>
        <button type="button" role="switch" aria-checked={enabled} aria-label="Diarizzazione — chi parla"
          onClick={() => onEnabledChange(!enabled)}
          className={`shrink-0 relative w-11 h-6 rounded-full transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand-400 ${enabled ? 'bg-brand-600' : 'bg-gray-700'}`}>
          <span className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-all ${enabled ? 'left-6' : 'left-1'}`} />
        </button>
      </div>
      <p className="text-xs text-gray-500">{archived ? 'Queste impostazioni valgono per la prossima elaborazione, non modificano la registrazione aperta.' : 'Impostazioni per la prossima elaborazione.'}</p>
      {!enabled ? <p className="text-sm text-gray-400">Trascrivi senza distinguere chi parla.</p> : <>
        {tokenConfigured === false && <p className="text-xs text-amber-300 bg-amber-950/30 border border-amber-500/20 rounded-lg p-3">Token HuggingFace non configurato: necessario per eseguire la diarizzazione. <Link className="underline underline-offset-2" to="/settings">Apri Impostazioni</Link></p>}
        <div>
          <label htmlFor="speaker-count-mode" className="label">Quante persone parlano?</label>
          <select id="speaker-count-mode" className="input w-full" value={speakerMode} onChange={e => onSpeakerModeChange(e.target.value)}>
            <option value="auto">Rileva automaticamente</option><option value="exact">Numero esatto</option>
          </select>
          <p className="text-xs text-gray-500 mt-1.5">Se conosci il numero, indicalo.</p>
        </div>
        {speakerMode === 'exact' && field('speaker-count', 'Numero di persone', expectedSpeakers, onExpectedSpeakersChange, errors.expectedSpeakers)}
        <div className="rounded-lg bg-white/[0.03] px-3 py-2.5 text-xs text-gray-400 space-y-1" aria-live="polite">
          <p>{start === 'after' ? 'Avvio manuale' : 'Dopo la trascrizione'} · {precision === 'words' ? 'Per parola · sperimentale' : 'Per segmento'}</p>
          <p>Dispositivo: {({ auto: 'Automatico', cpu: 'CPU', mps: 'GPU Apple · MPS' })[device]}</p>
          {speakerMode === 'auto' && <p>{Object.keys(errors).length ? 'Controlla i limiti nei dettagli.' : `Rilevamento automatico: ${limits}`}</p>}
        </div>
        <button type="button" className="w-full flex items-center justify-between text-sm text-gray-300 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400 rounded"
          aria-expanded={details} aria-controls="diarization-details" onClick={() => setDetails(v => !v)}>
          Dettagli {details ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
        <div id="diarization-details" hidden={!details} className="space-y-4 border-t border-white/5 pt-4">
          <div><label htmlFor="diarization-start" className="label">Quando eseguirla</label>
            <select id="diarization-start" className="input w-full" value={start} onChange={e => onStartChange(e.target.value)}>
              <option value="auto">Dopo la trascrizione</option><option value="after">La avvio io dopo</option>
            </select>
          </div>
          <div><label htmlFor="diarization-precision" className="label">Assegnazione del testo</label>
            <select id="diarization-precision" className="input w-full" value={precision} onChange={e => onPrecisionChange(e.target.value)}>
              <option value="segments">Per segmento</option><option value="words">Per parola · sperimentale</option>
            </select>
            <p className="text-xs text-gray-400 mt-1.5">{precision === 'words' ? 'Può separare cambi di persona dentro una frase. Richiede tempi parola utilizzabili; altrimenti conserva il segmento. Consigliato profilo Qualità nelle opzioni di trascrizione.' : 'Assegna ogni segmento di testo a una persona.'}</p>
          </div>
          {speakerMode === 'auto' && <fieldset className="space-y-2">
            <legend className="label">Limiti del rilevamento</legend>
            <p className="text-xs text-gray-500">Lascia vuoti se non conosci un intervallo.</p>
            <div className="grid grid-cols-2 gap-3">
              {field('speaker-min', 'Minimo', minSpeakers, onMinSpeakersChange, errors.minSpeakers, true)}
              {field('speaker-max', 'Massimo', maxSpeakers, onMaxSpeakersChange, errors.maxSpeakers, true)}
            </div>
          </fieldset>}
          <div><label htmlFor="diarization-device" className="label">Dispositivo</label>
            <select id="diarization-device" className="input w-full" value={device} onChange={e => onDeviceChange(e.target.value)}>
              <option value="auto">Automatico</option><option value="cpu">CPU</option><option value="mps">GPU Apple · MPS</option>
            </select>
            <p className="text-xs text-gray-500 mt-1.5">Automatico sceglie il dispositivo disponibile. MPS usa la GPU sui Mac compatibili.</p>
          </div>
        </div>
      </>}
    </section>
  )
}
