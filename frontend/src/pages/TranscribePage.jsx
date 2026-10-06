import { useState, useRef, useEffect } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import {
  Mic, ChevronDown, ChevronUp, Loader, AlertTriangle,
  ArrowLeft, Pause, Play, Square, Plus, Pencil, Check, X,
} from 'lucide-react'
import FileUpload from '../components/FileUpload'
import DiarizationPanel from '../components/DiarizationPanel'
import { speakerErrors, diarizationRequest } from '../lib/diarizationOptions'
import ModelSelector from '../components/ModelSelector'
import AudioPlayer from '../components/AudioPlayer'
import TranscriptView from '../components/TranscriptView'
import ExportPanel from '../components/ExportPanel'
import {
  startTranscription, subscribeJobEvents, fetchConfig, getJob,
  audioUrl, pauseJob, resumeJob, cancelJob, resumeQwenJob,
  retryWithFasterWhisper, renameJob,
  fetchModels, subscribeModelDownload,
} from '../api'

const LANGUAGES = [
  { code: '', label: 'Auto-detect' },
  { code: 'it', label: 'Italiano' },
  { code: 'en', label: 'English' },
  { code: 'fr', label: 'Français' },
  { code: 'de', label: 'Deutsch' },
  { code: 'es', label: 'Español' },
  { code: 'pt', label: 'Português' },
  { code: 'zh', label: '中文' },
  { code: 'ja', label: '日本語' },
  { code: 'ru', label: 'Русский' },
  { code: 'ar', label: 'العربية' },
]

const STAGE_LABELS = {
  queued: 'In coda…',
  downloading_yt: 'Download YouTube…',
  transcribing: 'Trascrizione…',
  diarizing: 'Diarizzazione speaker…',
  done: 'Completato!',
  error: 'Errore',
  paused: 'In pausa',
  canceled: 'Annullato',
}

const PROCESS_STEPS = [
  { key: 'queued', label: 'Preparazione' },
  { key: 'transcribing', label: 'Trascrizione' },
  { key: 'diarizing', label: 'Diarizzazione' },
  { key: 'done', label: 'Risultato' },
]

const BACKEND_LABELS = {
  faster_whisper: 'faster-whisper',
  whisper_cpp: 'whisper.cpp',
  qwen3_asr: 'Qwen3-ASR (MLX)',
}

const PROFILE_LABELS = {
  fast: 'Veloce',
  balanced: 'Bilanciato',
  quality: 'Qualità',
}

const DIARIZATION_START_LABELS = {
  auto: 'Dopo la trascrizione',
  after: 'Avvio manuale',
  off: 'Spenta',
}

const DIARIZATION_DEVICE_LABELS = {
  auto: 'Auto',
  cpu: 'CPU',
  mps: 'MPS',
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '00:00'
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

function getElapsedSeconds(job, nowMs) {
  if (!job?.created_at) return 0
  const started = Date.parse(job.created_at)
  if (!Number.isFinite(started)) return 0
  return Math.max(0, (nowMs - started) / 1000)
}

function getPhaseProgress(job) {
  if (job?.stage === 'transcribing' && job.transcription_backend === 'qwen3_asr' && Number.isFinite(job.qwen_phase_progress)) return job.qwen_phase_progress
  const msgPct = job?.message?.match(/(\d{1,3})%/)
  if (msgPct) return Math.min(100, Number(msgPct[1]))
  if (job?.stage === 'transcribing') {
    return Math.max(0, Math.min(100, Math.round(((job.progress || 0) - 20) / 0.6)))
  }
  return null
}

function getStepState(job, stepKey) {
  const stage = job?.stage
  if (job?.status === 'done') return 'done'
  if (job?.status === 'paused' && job?.previous_stage === stepKey) return 'active'
  if (stage === stepKey) return 'active'
  if (stepKey === 'queued') return ['transcribing', 'diarizing', 'done'].includes(stage) ? 'done' : 'pending'
  if (stepKey === 'transcribing') return ['diarizing', 'done'].includes(stage) ? 'done' : 'pending'
  if (stepKey === 'diarizing') return stage === 'done' ? 'done' : 'pending'
  return 'pending'
}

export default function TranscribePage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const preloadJobId = searchParams.get('job')

  const [file, setFile] = useState(null)
  const [ytUrl, setYtUrl] = useState(null)
  const [model, setModel] = useState('small')
  const [modelReady, setModelReady] = useState(false)
  const [language, setLanguage] = useState('')
  const [performanceProfile, setPerformanceProfile] = useState('balanced')
  const [qwenWordTimes, setQwenWordTimes] = useState(false)
  const [diarize, setDiarize] = useState(true)
  const [expectedSpeakers, setExpectedSpeakers] = useState('')
  const [speakerMode, setSpeakerMode] = useState('auto')
  const [tokenConfigured, setTokenConfigured] = useState(null)
  const [diarizationMode, setDiarizationMode] = useState('conservative')
  const [diarizationStart, setDiarizationStart] = useState('auto')
  const [diarizationDevice, setDiarizationDevice] = useState('auto')
  const [showOptions, setShowOptions] = useState(false)
  const [glossary, setGlossary] = useState('')
  const [diarizationPrecision, setDiarizationPrecision] = useState('segments')
  const [minSpeakers, setMinSpeakers] = useState('')
  const [maxSpeakers, setMaxSpeakers] = useState('')

  const [job, setJob] = useState(null) // current job state
  const [uploadKey, setUploadKey] = useState(0)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const [titleError, setTitleError] = useState('')
  const [savingTitle, setSavingTitle] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [fallbackModelReady, setFallbackModelReady] = useState(false)
  const [fallbackDownload, setFallbackDownload] = useState(null)
  const [fallbackError, setFallbackError] = useState('')

  const [audioPlaying, setAudioPlaying] = useState(false)
  const playerRef = useRef(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [nowTick, setNowTick] = useState(Date.now())
  const timeRef = useRef(null)
  const previousPreloadJobId = useRef(preloadJobId)

  // Load diarization default from config
  useEffect(() => {
    fetchConfig().then(cfg => {
      setGlossary(cfg.glossary || '')
      setDiarizationPrecision(cfg.diarization_precision || 'segments')
      setModel(cfg.default_model || 'small')
      if (cfg.default_model === 'qwen3-asr-1.7b') setQwenWordTimes(cfg.performance_profile === 'quality')
      else setPerformanceProfile(cfg.performance_profile || 'balanced')
      setDiarize((cfg.diarization_enabled ?? true) && cfg.diarization_start !== 'off')
      setTokenConfigured(Boolean(cfg.hf_token_set))
      setDiarizationMode(cfg.diarization_mode || 'conservative')
      setDiarizationStart(cfg.diarization_start === 'after' ? 'after' : 'auto')
      setDiarizationDevice(cfg.diarization_device || 'auto')
    }).catch(() => {})
  }, [])

  // If opened from History (?job=<id>), load the existing job
  useEffect(() => {
    if (!preloadJobId) return
    let active = true
    getJob(preloadJobId)
      .then(data => { if (active) setJob({ ...data, id: preloadJobId }) })
      .catch(() => {})
    return () => { active = false }
  }, [preloadJobId])

  useEffect(() => {
    if (previousPreloadJobId.current && !preloadJobId) {
      setJob(null)
      setFile(null)
      setYtUrl(null)
      setUploadKey(key => key + 1)
      setError('')
      setEditingTitle(false)
      setTitleError('')
      setCurrentTime(0)
      setAudioPlaying(false)
    }
    previousPreloadJobId.current = preloadJobId
  }, [preloadJobId])

  useEffect(() => {
    if (!job?.id || loading) return
    if (!['queued', 'downloading_yt', 'processing', 'paused'].includes(job.status)) return

    const jobId = job.id
    const unsub = subscribeJobEvents(jobId, (data) => {
      setJob({ ...data, id: jobId })
    }, () => {
      setError('Connessione persa durante l’elaborazione')
    })
    return unsub
  }, [job?.id, job?.status, loading])

  // Poll audio current time for transcript sync
  useEffect(() => {
    if (!job || job.status !== 'done') return
    timeRef.current = setInterval(() => {
      setCurrentTime(playerRef.current?.getCurrentTime() ?? 0)
    }, 200)
    return () => clearInterval(timeRef.current)
  }, [job?.status])

  useEffect(() => {
    if (!job || !['queued', 'downloading_yt', 'processing', 'paused'].includes(job.status)) return
    const timer = setInterval(() => setNowTick(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [job?.id, job?.status])

  useEffect(() => {
    if (job?.status !== 'error' || !job.fallback_available) return
    let alive = true
    fetchModels('faster_whisper').then(models => {
      if (alive) setFallbackModelReady(Boolean(models.find(item => item.name === job.model)?.downloaded))
    }).catch(e => { if (alive) setFallbackError(e.message) })
    return () => { alive = false }
  }, [job?.id, job?.status, job?.fallback_available, job?.model])

  const diarizationOptions = { enabled: diarize, speakerMode, expectedSpeakers, minSpeakers, maxSpeakers, start: diarizationStart }
  const diarizationErrors = speakerErrors(diarizationOptions)
  const needsWordTimes = diarize && diarizationPrecision === 'words'
  const effectiveProfile = model === 'qwen3-asr-1.7b'
    ? (qwenWordTimes || needsWordTimes ? 'quality' : 'balanced')
    : (needsWordTimes ? 'quality' : performanceProfile)
  const canStart = !Object.keys(diarizationErrors).length && (!diarize || tokenConfigured === true) && (file || ytUrl) && model && modelReady

  async function handleStart() {
    if (!canStart) return
    setLoading(true)
    setError('')
    setJob(null)
    setEditingTitle(false)
    setTitleError('')
    try {
      const { job_id } = await startTranscription({
        glossary, diarizationPrecision: diarize ? diarizationPrecision : 'segments',
        ...diarizationRequest(diarizationOptions),
        file: file || undefined,
        youtubeUrl: ytUrl || undefined,
        modelName: model,
        language,
        performanceProfile: effectiveProfile,
        diarizationMode,
        diarizationDevice,
      })

      const initialJob = {
        status: 'queued', stage: 'queued', progress: 0, message: 'In coda…', segments: [],
        model, performance_profile: effectiveProfile,
        request_diarize: diarize, diarization_start: diarize ? diarizationStart : 'off',
        diarization_precision: diarize ? diarizationPrecision : 'segments',
        expected_speakers: diarize && speakerMode === 'exact' ? Number(expectedSpeakers) : null,
        transcription_options: { word_timestamps: effectiveProfile === 'quality' },
      }
      setJob({ ...initialJob, id: job_id })

      const unsub = subscribeJobEvents(job_id, (data) => {
        setJob({ ...data, id: job_id })
        if (data.status === 'done' || data.status === 'error' || data.status === 'canceled') {
          unsub()
          setLoading(false)
        }
      }, () => {
        setLoading(false)
        setError('Connessione persa durante la trascrizione')
      })
    } catch (e) {
      setError(e.message)
      setLoading(false)
    }
  }

  async function handleRetryFaster() {
    if (!job?.id) return
    setLoading(true)
    setError('')
    try {
      const { job_id } = await retryWithFasterWhisper(job.id)
      setJob({ id: job_id, status: 'queued', stage: 'queued', progress: 0, message: 'In coda…', segments: [], transcription_backend: 'faster_whisper' })
      const unsub = subscribeJobEvents(job_id, data => {
        setJob({ ...data, id: job_id })
        if (['done', 'error', 'canceled'].includes(data.status)) {
          unsub()
          setLoading(false)
        }
      }, () => {
        setLoading(false)
        setError('Connessione persa durante la trascrizione')
      })
    } catch (e) {
      setError(e.message)
      setLoading(false)
    }
  }

  function handleDownloadFallback() {
    if (!job?.model) return
    setFallbackError('')
    setFallbackDownload(0)
    subscribeModelDownload(job.model, data => {
      if (data.status === 'done') {
        setFallbackDownload(null)
        setFallbackModelReady(true)
      } else if (data.status === 'error') {
        setFallbackDownload(null)
        setFallbackError(data.error || 'Download non riuscito')
      } else setFallbackDownload(data.progress || 0)
    }, () => {
      setFallbackDownload(null)
      setFallbackError('Connessione interrotta durante il download')
    }, 'faster_whisper')
  }

  async function handlePause() {
    if (!job?.id) return
    setError('')
    try {
      await pauseJob(job.id)
      setJob(j => j ? { ...j, status: 'paused', stage: 'paused', message: 'In pausa…' } : j)
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleResume() {
    if (!job?.id) return
    setError('')
    try {
      await resumeJob(job.id)
      setJob(j => j ? {
        ...j,
        status: 'processing',
        stage: j.previous_stage || j.stage || 'processing',
        message: 'Ripresa…',
      } : j)
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleCancel() {
    if (!job?.id) return
    setError('')
    try {
      await cancelJob(job.id)
      setJob(j => j ? { ...j, message: 'Annullamento richiesto…' } : j)
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleResumeQwen() {
    if (!job?.id) return
    setLoading(true)
    setError('')
    try {
      await resumeQwenJob(job.id)
      setJob({ ...await getJob(job.id), id: job.id })
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  function handleNewTranscription() {
    if (preloadJobId) {
      navigate('/')
      return
    }
    setJob(null)
    setFile(null)
    setYtUrl(null)
    setUploadKey(key => key + 1)
    setError('')
    setEditingTitle(false)
    setTitleError('')
    setCurrentTime(0)
    setAudioPlaying(false)
  }

  async function handleRename() {
    const title = titleDraft.trim()
    if (!title) {
      setTitleError('Inserisci un titolo.')
      return
    }
    if (title === (job.title || job.filename)) {
      setEditingTitle(false)
      return
    }
    setSavingTitle(true)
    setTitleError('')
    try {
      await renameJob(job.id, title)
      setJob(current => current?.id === job.id ? { ...current, title } : current)
      setEditingTitle(false)
    } catch (e) {
      setTitleError(e.message)
    } finally {
      setSavingTitle(false)
    }
  }

  const isDone = job?.status === 'done'
  const hasError = job?.status === 'error'
  const isCanceled = job?.status === 'canceled'
  const isTerminal = isDone || hasError || isCanceled
  const elapsedSeconds = getElapsedSeconds(job, nowTick)
  const phaseProgress = getPhaseProgress(job)
  const backendLabel = BACKEND_LABELS[job?.transcription_backend] || 'Non registrato'
  const profileLabel = job?.transcription_backend === 'qwen3_asr'
    ? (job?.transcription_options?.word_timestamps == null ? 'Non registrato' : job.transcription_options.word_timestamps ? 'Attivi' : 'Spenti')
    : PROFILE_LABELS[job?.performance_profile] || 'Non registrato'
  const diarizationStartLabel = job?.diarization_start ? DIARIZATION_START_LABELS[job.diarization_start] || 'Non registrato' : 'Non registrato'
  const expectedCount = job?.expected_speakers
  const peopleLabel = job?.request_diarize === false || job?.diarization_start === 'off' ? 'Disattivata' : expectedCount ? String(expectedCount) : job && 'expected_speakers' in job ? 'Rilevamento automatico' : 'Non registrato'
  const assignmentLabel = job?.request_diarize === false || job?.diarization_start === 'off' ? 'Disattivata' : job?.diarization_precision === 'words' ? 'Per parola' : job?.diarization_precision === 'segments' ? 'Per segmento' : 'Non registrato'
  const wordTimesLabel = job?.transcription_options?.word_timestamps == null ? 'Non registrato' : job.transcription_options.word_timestamps ? 'Attivi' : 'Spenti'
  const diarizationDeviceLabel = DIARIZATION_DEVICE_LABELS[job?.diarization_device] || DIARIZATION_DEVICE_LABELS[diarizationDevice] || 'Auto'
  const jobStatusLabel = job?.status === 'processing'
    ? STAGE_LABELS[job.stage] || 'In corso'
    : STAGE_LABELS[job?.status] || 'In attesa'

  return (
    <div className="space-y-6">
      {job?.id && (
        <div className="card p-4 sm:p-5 space-y-3">
          <div className="min-w-0">
              <p className="text-xs text-gray-500">Audio corrente · {jobStatusLabel}</p>
              {editingTitle ? (
                <div className="flex flex-wrap items-center gap-2 mt-1">
                  <input
                    autoFocus
                    className="input min-w-0 flex-1"
                    aria-label="Titolo trascrizione"
                    value={titleDraft}
                    onChange={e => { setTitleDraft(e.target.value); setTitleError('') }}
                    onKeyDown={e => {
                      if (e.key === 'Enter') handleRename()
                      if (e.key === 'Escape') { setEditingTitle(false); setTitleError('') }
                    }}
                    disabled={savingTitle}
                  />
                  <button type="button" className="btn-ghost text-xs" onClick={handleRename} disabled={savingTitle}>
                    <Check size={14} /> Salva
                  </button>
                  <button type="button" className="btn-ghost text-xs" onClick={() => { setEditingTitle(false); setTitleError('') }} disabled={savingTitle}>
                    <X size={14} /> Annulla
                  </button>
                </div>
              ) : (
                <div className="flex flex-wrap items-center gap-2 mt-1">
                  <h1 className="text-lg font-semibold text-white break-words">{job.title || job.filename || file?.name || 'Trascrizione'}</h1>
                  {isTerminal && <button type="button" className="btn-ghost text-xs" onClick={() => { setTitleDraft(job.title || job.filename || ''); setTitleError(''); setEditingTitle(true) }}>
                    <Pencil size={13} /> Rinomina
                  </button>}
                </div>
              )}
              {titleError && <p role="alert" className="text-xs text-red-400 mt-2">{titleError}</p>}
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 text-xs">
            {[
              ['Modello', job.model || 'Non registrato'], ['Motore', backendLabel],
              ['Tempi parola', wordTimesLabel], ['Diarizzazione', diarizationStartLabel],
              ['Assegnazione speaker', assignmentLabel], ['Persone', peopleLabel],
            ].map(([label, value]) => <div key={label} className="min-w-0 rounded-md bg-gray-900/60 border border-gray-800 px-3 py-2">
              <p className="text-gray-500">{label}</p><p className="text-gray-200 mt-0.5 break-words">{value}</p>
            </div>)}
          </div>
        </div>
      )}
      {/* Banner "aperto dall'archivio" */}
      {preloadJobId && (
        <div className="flex items-center justify-between px-4 py-2.5 rounded-lg bg-brand-900/20 border border-brand-500/20 text-sm">
          <span className="text-brand-300">
            Stai visualizzando una trascrizione dall'archivio: <strong>{job?.title || job?.filename || '…'}</strong>
          </span>
          <button
            onClick={() => navigate('/history')}
            className="btn-ghost text-xs py-1"
          >
            <ArrowLeft size={13} /> Archivio
          </button>
        </div>
      )}

      {isTerminal && (
        <div className="card p-4 sm:p-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-gray-200">Pronto per un altro audio?</p>
            <p className="text-xs text-gray-500 mt-1">Questo risultato resta nell’Archivio.</p>
          </div>
          <button type="button" className="btn-primary self-start sm:self-auto" onClick={handleNewTranscription}>
            <Plus size={15} /> Nuova trascrizione
          </button>
        </div>
      )}

    <div className="grid grid-cols-1 lg:grid-cols-[1fr_2fr] gap-8">
      {/* ── Left panel: controls ─────────────────────────────────────────────── */}
      <div className="space-y-6">
        {/* Source */}
        <div className="card p-5 space-y-5">
          <h2 className="text-sm font-semibold text-gray-200 uppercase tracking-wider">Sorgente</h2>
          <FileUpload
            key={uploadKey}
            onFile={(f) => { setFile(f); setYtUrl(null) }}
            onYouTube={(url) => { setYtUrl(url); setFile(null) }}
          />
        </div>

        {/* Model */}
        <div className="card p-5">
          <ModelSelector
            selected={model}
            onChange={setModel}
            onSelectedStatusChange={setModelReady}
          />
        </div>

        <DiarizationPanel
          enabled={diarize} onEnabledChange={setDiarize}
          speakerMode={speakerMode} onSpeakerModeChange={setSpeakerMode}
          expectedSpeakers={expectedSpeakers} onExpectedSpeakersChange={setExpectedSpeakers}
          minSpeakers={minSpeakers} onMinSpeakersChange={setMinSpeakers}
          maxSpeakers={maxSpeakers} onMaxSpeakersChange={setMaxSpeakers}
          start={diarizationStart} onStartChange={setDiarizationStart}
          precision={diarizationPrecision} onPrecisionChange={setDiarizationPrecision}
          device={diarizationDevice} onDeviceChange={setDiarizationDevice}
          tokenConfigured={tokenConfigured} archived={Boolean(preloadJobId)} errors={diarizationErrors}
        />

        <div className="card overflow-hidden">
          <button type="button" onClick={() => setShowOptions(v => !v)}
            aria-expanded={showOptions} aria-controls="transcription-options"
            className="w-full flex items-center justify-between px-5 py-3 text-sm font-medium text-gray-400 hover:text-gray-200 transition-colors">
            Opzioni di trascrizione
            {showOptions ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          </button>
          <div id="transcription-options" hidden={!showOptions} className="px-5 pb-5 space-y-4 border-t border-white/5 pt-4">
            <div>
              <label htmlFor="transcription-language" className="label">Lingua (opzionale)</label>
              <select id="transcription-language" className="input" value={language} onChange={e => setLanguage(e.target.value)}>
                {LANGUAGES.map(l => <option key={l.code} value={l.code}>{l.label}</option>)}
              </select>
            </div>
            {model === 'qwen3-asr-1.7b' ? (
              <div>
                <label htmlFor="transcription-profile" className="label">Tempi parola Qwen</label>
                <select id="transcription-profile" className="input" value={needsWordTimes ? 'quality' : qwenWordTimes ? 'quality' : 'balanced'} onChange={e => setQwenWordTimes(e.target.value === 'quality')} disabled={needsWordTimes}>
                  <option value="balanced">Spenti</option>
                  <option value="quality">Attivi</option>
                </select>
                <p className="text-xs text-gray-400 mt-1">Collocano le parole nell'audio. Non cambiano il testo riconosciuto né obbligano la diarizzazione per parola.</p>
                <p className="text-xs text-gray-400 mt-1">Su audio lunghi, Spenti riduce i tempi. Qwen si arresta se supera 4 GiB di memoria per proteggere il Mac.</p>
                {needsWordTimes && <p className="text-xs text-amber-300 mt-1">Attivi per questo job: assegnazione speaker Per parola selezionata.</p>}
              </div>
            ) : (
              <div>
                <label htmlFor="transcription-profile" className="label">Profilo prestazioni Whisper</label>
                <select id="transcription-profile" className="input" value={needsWordTimes ? 'quality' : performanceProfile} onChange={e => setPerformanceProfile(e.target.value)} disabled={needsWordTimes}>
                  <option value="fast">Veloce</option><option value="balanced">Bilanciato</option><option value="quality">Qualità + tempi parola</option>
                </select>
                {needsWordTimes && <p className="text-xs text-amber-300 mt-1">Qualità e tempi parola attivi per questo job: assegnazione speaker Per parola selezionata.</p>}
              </div>
            )}
            <div>
              <label htmlFor="transcription-glossary" className="label">Glossario registrazione</label>
              <textarea id="transcription-glossary" className="input w-full" maxLength={4000} placeholder="EMV, PSP, acquirer, issuer…" value={glossary} onChange={e => setGlossary(e.target.value)} />
              <p className="text-xs text-gray-500 mt-1">Suggerisce termini al modello, senza sostituzioni automatiche.</p>
              {model === 'qwen3-asr-1.7b' && <p className="text-xs text-amber-400 mt-1">Qwen non usa glossario come suggerimento; testo resta invariato.</p>}
            </div>
          </div>
        </div>

        {diarize && tokenConfigured === false && <p role="alert" className="text-sm text-amber-300">Configura il token HuggingFace nelle Impostazioni per avviare la diarizzazione.</p>}

        {/* Start button */}
        <button
          onClick={handleStart}
          disabled={!canStart || loading}
          className="btn-primary w-full justify-center text-base py-3"
        >
          {loading ? <Loader size={18} className="animate-spin" /> : <Mic size={18} />}
          {loading ? 'Elaborazione…' : 'Avvia trascrizione'}
        </button>

        {error && (
          <div className="flex items-start gap-2 p-3 rounded-lg bg-red-950/40 border border-red-500/20 text-sm text-red-300">
            <AlertTriangle size={16} className="flex-shrink-0 mt-0.5" />
            {error}
          </div>
        )}
      </div>

      {/* ── Right panel: progress + result ──────────────────────────────────── */}
      <div className="space-y-6">
        {!job && !loading && (
          <div className="card p-12 text-center text-gray-600">
            <Mic size={40} className="mx-auto mb-4 opacity-30" />
            <p className="text-sm">Carica un file o inserisci un URL YouTube per iniziare.</p>
          </div>
        )}

        {/* Progress */}
        {job && !isDone && !hasError && !isCanceled && (
          <div className="card p-6 space-y-5">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-gray-200">
                  {STAGE_LABELS[job.stage] || job.stage}
                </p>
                {job.message && <p className="text-xs text-gray-500 mt-1">{job.message}</p>}
              </div>
              <div className="text-right flex-shrink-0">
                <p className="text-sm text-brand-400 font-mono">{job.progress}%</p>
                <p className="text-[11px] text-gray-500">globale</p>
              </div>
            </div>

            <div className="space-y-2">
              <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-brand-600 to-brand-400 rounded-full transition-all duration-500"
                  style={{ width: `${job.progress}%` }}
                />
              </div>
              {phaseProgress !== null && (
                <p className="text-xs text-gray-500">
                  Fase corrente: <span className="text-gray-300 font-mono">{phaseProgress}%</span>
                </p>
              )}
              {job.stage === 'transcribing' && Number.isFinite(job.eta_seconds) && (
                <p className="text-xs text-gray-400">
                  {job.eta_phase === 'alignment' ? 'Allineamento' : 'Trascrizione'} residuo stimato: circa {formatDuration(Math.ceil(job.eta_seconds))}
                </p>
              )}
              {Number.isFinite(job.worker_memory_bytes) && (
                <p className="text-xs text-gray-400">
                  Memoria Qwen: {(job.worker_memory_bytes / 1024 ** 3).toFixed(1)} GiB / {(job.worker_memory_limit_bytes / 1024 ** 3).toFixed(0)} GiB limite
                </p>
              )}
            </div>

            <div className="grid grid-cols-4 gap-2">
              {PROCESS_STEPS.map((step) => {
                const state = getStepState(job, step.key)
                return (
                  <div key={step.key} className="min-w-0">
                    <div className={`h-1 rounded-full ${state === 'done' ? 'bg-brand-500' : state === 'active' ? 'bg-brand-300' : 'bg-gray-800'}`} />
                    <p className={`mt-2 text-[11px] truncate ${state === 'active' ? 'text-gray-100' : state === 'done' ? 'text-brand-300' : 'text-gray-600'}`}>
                      {step.label}
                    </p>
                  </div>
                )
              })}
            </div>

            <div className="grid grid-cols-2 lg:grid-cols-5 gap-2 text-xs">
              <div className="rounded-md bg-gray-900/60 border border-gray-800 px-3 py-2">
                <p className="text-gray-500">Tempo</p>
                <p className="text-gray-200 font-mono mt-0.5">{formatDuration(elapsedSeconds)}</p>
              </div>
              <div className="rounded-md bg-gray-900/60 border border-gray-800 px-3 py-2">
                <p className="text-gray-500">Backend</p>
                <p className="text-gray-200 mt-0.5 truncate">{backendLabel}</p>
              </div>
              <div className="rounded-md bg-gray-900/60 border border-gray-800 px-3 py-2">
                <p className="text-gray-500">{job?.transcription_backend === 'qwen3_asr' ? 'Tempi parola' : 'Profilo'}</p>
                <p className="text-gray-200 mt-0.5 truncate">{profileLabel}</p>
              </div>
              <div className="rounded-md bg-gray-900/60 border border-gray-800 px-3 py-2">
                <p className="text-gray-500">Persone</p>
                <p className="text-gray-200 mt-0.5">{peopleLabel}</p>
              </div>
              <div className="rounded-md bg-gray-900/60 border border-gray-800 px-3 py-2">
                <p className="text-gray-500">Diarizzazione</p>
                <p className="text-gray-200 mt-0.5">{diarizationStartLabel}</p>
              </div>
            </div>

            <details className="rounded-md border border-gray-800 bg-gray-950/40 px-3 py-2">
              <summary className="cursor-pointer text-xs text-gray-400">Dettagli processo</summary>
              <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs text-gray-500">
                <p>Modello: <span className="text-gray-300">{job.model || model}</span></p>
                <p>Lingua: <span className="text-gray-300">{job.language || language || 'Auto'}</span></p>
                <p>Beam: <span className="text-gray-300">{job.transcription_options?.beam_size ?? 'auto'}</span></p>
                <p>Thread: <span className="text-gray-300">{job.transcription_options?.threads ?? 'auto'}</span></p>
                <p>Diarizzazione: <span className="text-gray-300">{diarizationStartLabel}</span></p>
                <p>Assegnazione speaker: <span className="text-gray-300">{assignmentLabel}</span></p>
                <p>Tempi parola: <span className="text-gray-300">{wordTimesLabel}</span></p>
                <p>Device speaker: <span className="text-gray-300">{diarizationDeviceLabel}</span></p>
              </div>
            </details>

            <div className="flex flex-wrap gap-2 pt-1">
              {job.status === 'paused' ? (
                <button onClick={handleResume} className="btn-ghost text-xs">
                  <Play size={13} /> Riprendi
                </button>
              ) : (
                <button
                  onClick={handlePause}
                  disabled={!job.pausable}
                  className="btn-ghost text-xs disabled:opacity-40 disabled:cursor-not-allowed"
                  title={job.pausable ? 'Metti in pausa' : 'Questa fase non supporta pausa reale'}
                >
                  <Pause size={13} /> Pausa
                </button>
              )}
              <button
                onClick={handleCancel}
                disabled={!job.cancelable}
                className="btn-ghost text-xs text-red-400 hover:text-red-300 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Square size={13} /> Annulla
              </button>
            </div>
            {job.transcription_backend === 'qwen3_asr' && <p className="text-xs text-amber-400">Pausa mantiene Qwen in RAM; Annulla libera memoria.</p>}
          </div>
        )}

        {isCanceled && (
          <div className="card p-6 flex items-start gap-3 text-orange-300">
            <Square size={20} className="flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium">Operazione annullata</p>
              <p className="text-sm text-orange-400/80 mt-1">{job.message}</p>
            </div>
          </div>
        )}

        {(hasError || isCanceled) && job.qwen_resume_available && (
          <div className="card p-4 space-y-2">
            <p className="text-sm text-gray-300">Blocchi salvati fino a {formatDuration(job.qwen_resume_available.covered_seconds)}. Ripresa conserva trascrizione già completata.</p>
            <button type="button" className="btn-primary" disabled={loading} onClick={handleResumeQwen}>
              {loading ? 'Ripresa…' : 'Riprendi dai blocchi salvati'}
            </button>
          </div>
        )}

        {hasError && (
          <div className="card p-6 flex items-start gap-3 text-red-300">
            <AlertTriangle size={20} className="flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium">Trascrizione fallita</p>
              <p className="text-sm text-red-400/80 mt-1">{job.error}</p>
              {job.fallback_available && (fallbackModelReady
                ? <button type="button" className="btn-primary mt-3" disabled={loading} onClick={handleRetryFaster}>Riprova con faster-whisper</button>
                : <button type="button" className="btn-ghost mt-3" disabled={fallbackDownload !== null} onClick={handleDownloadFallback}>
                    {fallbackDownload === null ? 'Scarica modello faster-whisper per riprovare' : `Download ${fallbackDownload}%`}
                  </button>)}
              {fallbackError && <p className="text-xs text-red-400 mt-2">{fallbackError}</p>}
            </div>
          </div>
        )}

        {/* Result */}
        {isDone && (
          <>
            {/* Audio player */}
            {job.audio_url && (
              <AudioPlayer key={job.id} ref={playerRef} src={audioUrl(job.id)} onPlayingChange={setAudioPlaying} />
            )}

            {/* Metadata */}
            <div className="flex items-center gap-4 text-xs text-gray-500 px-1">
              {job.language && <span>Lingua rilevata: <strong className="text-gray-300">{job.language}</strong></span>}
              {job.duration && <span>Durata: <strong className="text-gray-300">{Math.floor(job.duration / 60)}:{String(Math.floor(job.duration % 60)).padStart(2,'0')}</strong></span>}
              <span>{job.segments?.length} segmenti</span>
        </div>

        {job.diarization_error && (
          <div className="flex items-start gap-2 p-3 rounded-lg bg-orange-950/35 border border-orange-500/20 text-sm text-orange-300">
            <AlertTriangle size={16} className="flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium">Diarizzazione non completata</p>
              <p className="text-xs text-orange-300/80 mt-1">{job.diarization_error}</p>
            </div>
          </div>
        )}

        {/* Export */}
            <div className="card p-4">
              <p className="text-xs text-gray-500 mb-3 font-medium uppercase tracking-wider">Esporta trascrizione</p>
              <ExportPanel jobId={job.id} />
            </div>

            {/* Transcript */}
            <div className="card p-5">
            <TranscriptView
              jobId={job.id}
              segments={job.segments}
              currentTime={currentTime}
              playing={audioPlaying}
              onPause={() => playerRef.current?.pause()}
              onListen={job.audio_url ? (start, end) => playerRef.current?.playRange(start, end) : undefined}
              onSeek={job.audio_url ? (t) => playerRef.current?.seekTo(t) : undefined}
              onJobUpdate={(data) => setJob({ ...data, id: job.id })}
            />
            </div>
          </>
        )}
      </div>
    </div>
    </div>
  )
}
