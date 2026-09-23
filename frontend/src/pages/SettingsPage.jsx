import { useState, useEffect } from 'react'
import { Save, Eye, EyeOff, ExternalLink, CheckCircle } from 'lucide-react'
import { fetchConfig, saveConfig } from '../api'

const WHISPER_MODELS = [
  { id: 'tiny', label: 'Tiny' },
  { id: 'base', label: 'Base' },
  { id: 'small', label: 'Small' },
  { id: 'medium', label: 'Medium' },
  { id: 'large-v2', label: 'Large v2' },
  { id: 'large-v3', label: 'Large v3' },
  { id: 'large-v3-turbo', label: 'Large v3 Turbo' },
]

const PERFORMANCE_PROFILES = [
  { id: 'fast', label: 'Veloce', desc: 'beam 1, senza timestamp parola' },
  { id: 'balanced', label: 'Bilanciato', desc: 'beam 3, senza timestamp parola' },
  { id: 'quality', label: 'Qualità', desc: 'beam 5, timestamp parola attivi' },
]

const TRANSCRIPTION_BACKENDS = [
  {
    id: 'faster_whisper',
    label: 'faster-whisper',
    desc: 'Stabile, usa CPU su Apple Silicon',
  },
  {
    id: 'whisper_cpp',
    label: 'whisper.cpp',
    desc: 'Usa Metal/Core ML, richiede whisper-cli e modelli GGML',
  },
]

const DIARIZATION_START_MODES = [
  { id: 'auto', label: 'Automatica', desc: 'Dopo la trascrizione' },
  { id: 'after', label: 'Dopo, manuale', desc: 'Mostra prima il testo' },
  { id: 'off', label: 'Spenta', desc: 'Nessuna diarizzazione' },
]

const DIARIZATION_DEVICES = [
  { id: 'auto', label: 'Auto', desc: 'Sceglie il device disponibile' },
  { id: 'cpu', label: 'CPU', desc: 'Più controllabile, spesso più fresca' },
  { id: 'mps', label: 'MPS', desc: 'Apple GPU, più veloce ma sperimentale' },
]

export default function SettingsPage() {
  const [cfg, setCfg] = useState({
    hf_token: '',
    default_model: 'small',
    transcription_backend: 'faster_whisper',
    performance_profile: 'balanced',
    diarization_enabled: true,
    diarization_start: 'auto',
    diarization_device: 'auto',
    diarization_model: 'community-1',
    diarization_mode: 'conservative',
  })
  const [showToken, setShowToken] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchConfig()
      .then(data => { setCfg(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  async function handleSave() {
    try {
      await saveConfig(cfg)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch (e) {
      alert(e.message)
    }
  }

  if (loading) return <div className="text-gray-500 text-sm p-8">Caricamento…</div>

  return (
    <div className="max-w-xl space-y-8">
      <div>
        <h1 className="text-xl font-semibold text-white">Impostazioni</h1>
        <p className="text-sm text-gray-500 mt-1">Configurazione locale — nessun dato inviato a server esterni.</p>
      </div>

      <div className="card p-6 space-y-3">
        <label className="block text-sm">Glossario predefinito<textarea className="input w-full" maxLength={4000} value={cfg.glossary || ''} onChange={e => setCfg({ ...cfg, glossary: e.target.value })} /></label>
        <label className="block text-sm">Precisione diarizzazione<select className="input w-full" value={cfg.diarization_precision || 'segments'} onChange={e => setCfg({ ...cfg, diarization_precision: e.target.value })}><option value="segments">Per segmento</option><option value="words">Per parola (sperimentale)</option></select></label>
        <p className="text-xs text-gray-400">Glossario suggerito al riconoscimento, senza sostituzioni automatiche.</p>
      </div>
      {/* HuggingFace token */}
      <div className="card p-6 space-y-4">
        <div>
          <h2 className="font-semibold text-gray-200">Token HuggingFace</h2>
          <p className="text-sm text-gray-500 mt-1">
            Necessario per il download dei modelli di diarizzazione (pyannote).
            Ottieni un token su{' '}
            <a
              href="https://huggingface.co/settings/tokens"
              target="_blank"
              rel="noreferrer"
              className="text-brand-400 hover:text-brand-300 inline-flex items-center gap-0.5"
            >
              huggingface.co <ExternalLink size={11} />
            </a>
            {' '}e accetta i termini dei modelli{' '}
            <a
              href="https://huggingface.co/pyannote/speaker-diarization-community-1"
              target="_blank"
              rel="noreferrer"
              className="text-brand-400 hover:text-brand-300 inline-flex items-center gap-0.5"
            >
              pyannote/speaker-diarization-community-1 <ExternalLink size={11} />
            </a>
            {' '}e{' '}
            <a
              href="https://huggingface.co/pyannote/speaker-diarization-3.1"
              target="_blank"
              rel="noreferrer"
              className="text-brand-400 hover:text-brand-300 inline-flex items-center gap-0.5"
            >
              pyannote/speaker-diarization-3.1 <ExternalLink size={11} />
            </a>.
          </p>
        </div>

        <div>
          <label className="label">Token (hf_...)</label>
          <div className="relative">
            <input
              type={showToken ? 'text' : 'password'}
              className="input pr-10"
              placeholder="hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
              value={cfg.hf_token}
              onChange={(e) => setCfg(c => ({ ...c, hf_token: e.target.value }))}
            />
            <button
              onClick={() => setShowToken(v => !v)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
            >
              {showToken ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>

        {cfg.hf_token_set && !cfg.hf_token && (
          <p className="text-xs text-green-400 flex items-center gap-1">
            <CheckCircle size={12} /> Token già salvato — lascia vuoto per mantenerlo.
          </p>
        )}
      </div>

      {/* Transcription */}
<div className="card p-6 space-y-4">
<h2 className="font-semibold text-gray-200">Trascrizione</h2>
<div>
<label className="label">Backend trascrizione</label>
<select
className="input"
value={cfg.transcription_backend || 'faster_whisper'}
onChange={(e) => setCfg(c => ({ ...c, transcription_backend: e.target.value }))}
>
{TRANSCRIPTION_BACKENDS.map(backend => (
<option key={backend.id} value={backend.id}>
{backend.label} - {backend.desc}
</option>
))}
</select>
</div>
<div>
<label className="label">Modello Whisper predefinito</label>
          <select
            className="input"
            value={cfg.default_model || 'small'}
            onChange={(e) => setCfg(c => ({ ...c, default_model: e.target.value }))}
          >
            {WHISPER_MODELS.map(model => (
              <option key={model.id} value={model.id}>{model.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Profilo prestazioni</label>
          <select
            className="input"
            value={cfg.performance_profile || 'balanced'}
            onChange={(e) => setCfg(c => ({ ...c, performance_profile: e.target.value }))}
          >
            {PERFORMANCE_PROFILES.map(profile => (
              <option key={profile.id} value={profile.id}>
                {profile.label} - {profile.desc}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Diarization */}
      <div className="card p-6 space-y-4">
        <h2 className="font-semibold text-gray-200">Diarizzazione</h2>
        <label className="flex items-center justify-between cursor-pointer">
          <div>
            <p className="text-sm text-gray-300">Attiva diarizzazione speaker</p>
            <p className="text-xs text-gray-500 mt-0.5">
              Identifica automaticamente chi parla in ogni segmento. Richiede il token HuggingFace.
            </p>
          </div>
          <div
            onClick={() => setCfg(c => {
              const enabled = !c.diarization_enabled
              return { ...c, diarization_enabled: enabled, diarization_start: enabled ? 'auto' : 'off' }
            })}
            className={`relative w-11 h-6 rounded-full transition-colors flex-shrink-0 ${
              cfg.diarization_enabled ? 'bg-brand-600' : 'bg-gray-700'
            }`}
          >
            <div
              className={`absolute top-1 w-4 h-4 rounded-full bg-white shadow transition-all ${
                cfg.diarization_enabled ? 'left-6' : 'left-1'
              }`}
            />
          </div>
          </label>

          <div>
            <label className="label">Avvio diarizzazione</label>
            <select
              className="input"
              value={cfg.diarization_start || (cfg.diarization_enabled ? 'auto' : 'off')}
              onChange={(e) => setCfg(c => ({
                ...c,
                diarization_start: e.target.value,
                diarization_enabled: e.target.value !== 'off',
              }))}
            >
              {DIARIZATION_START_MODES.map(mode => (
                <option key={mode.id} value={mode.id}>
                  {mode.label} - {mode.desc}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="label">Device diarizzazione</label>
            <select
              className="input"
              value={cfg.diarization_device || 'auto'}
              onChange={(e) => setCfg(c => ({ ...c, diarization_device: e.target.value }))}
              disabled={(cfg.diarization_start || (cfg.diarization_enabled ? 'auto' : 'off')) === 'off'}
            >
              {DIARIZATION_DEVICES.map(device => (
                <option key={device.id} value={device.id}>
                  {device.label} - {device.desc}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="label">Modello pyannote</label>
            <select
            className="input"
            value={cfg.diarization_model || 'community-1'}
            onChange={(e) => setCfg(c => ({ ...c, diarization_model: e.target.value }))}
          >
            <option value="community-1">Community-1</option>
            <option value="3.1">Speaker diarization 3.1</option>
          </select>
        </div>

        <div>
          <label className="label">Modalità automatica</label>
          <select
            className="input"
            value={cfg.diarization_mode || 'conservative'}
            onChange={(e) => setCfg(c => ({ ...c, diarization_mode: e.target.value }))}
          >
            <option value="conservative">Conservativa</option>
            <option value="balanced">Bilanciata</option>
          </select>
        </div>
      </div>

      <button onClick={handleSave} className="btn-primary">
        {saved ? <><CheckCircle size={16} /> Salvato!</> : <><Save size={16} /> Salva impostazioni</>}
      </button>
    </div>
  )
}
