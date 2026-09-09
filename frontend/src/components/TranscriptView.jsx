import { useState, useRef, useEffect } from 'react'
import { Search, Edit2, Check, X, GitMerge, Loader, RefreshCw, AlertTriangle } from 'lucide-react'
import { fetchSpeakerSuggestions, rediarizeJob, subscribeJobEvents, updateSpeakers } from '../api'

function formatTs(s) {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}`
}

function formatDuration(s) {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}`
}

function highlight(text, query) {
  if (!query) return text
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const parts = text.split(new RegExp(`(${escaped})`, 'gi'))
  return parts.map((p, i) =>
    p.toLowerCase() === query.toLowerCase()
      ? <mark key={i} className="bg-yellow-400/30 text-yellow-200 rounded px-0.5">{p}</mark>
      : p
  )
}

// Generate a stable color per speaker name
const SPEAKER_COLORS = [
  'text-blue-400', 'text-emerald-400', 'text-violet-400',
  'text-orange-400', 'text-pink-400', 'text-cyan-400',
]
const speakerColor = (() => {
  const cache = {}
  let idx = 0
  return (name) => {
    if (!cache[name]) { cache[name] = SPEAKER_COLORS[idx++ % SPEAKER_COLORS.length] }
    return cache[name]
  }
})()

export default function TranscriptView({ jobId, segments, onSeek, currentTime, onJobUpdate }) {
  const [search, setSearch] = useState('')
  const [editingSpeaker, setEditingSpeaker] = useState(null) // speaker name being edited
  const [speakerDraft, setSpeakerDraft] = useState('')
  const [localSegments, setLocalSegments] = useState(segments || [])
  const [rediarizeSpeakers, setRediarizeSpeakers] = useState('')
  const [rediarizeMode, setRediarizeMode] = useState('conservative')
  const [rediarizeState, setRediarizeState] = useState(null)
  const [rediarizeMsg, setRediarizeMsg] = useState('')
  const [mergeSource, setMergeSource] = useState('')
  const [mergeTarget, setMergeTarget] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [loadingSuggestions, setLoadingSuggestions] = useState(false)
  const activeRef = useRef(null)

  useEffect(() => { setLocalSegments(segments || []) }, [segments])

  // Highlight active segment
  const activeIdx = localSegments.findIndex(
    (s) => currentTime >= s.start && currentTime < s.end
  )

  useEffect(() => {
    if (activeIdx >= 0 && activeRef.current) {
      activeRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [activeIdx])

  async function commitSpeakerRename(oldName) {
    if (!speakerDraft.trim() || speakerDraft === oldName) {
      setEditingSpeaker(null)
      return
    }
    const newName = speakerDraft.trim()
    // Optimistic update
    setLocalSegments(segs => segs.map(s => s.speaker === oldName ? { ...s, speaker: newName } : s))
    setEditingSpeaker(null)
    try {
      await updateSpeakers(jobId, { [oldName]: newName })
    } catch (e) {
      console.error(e)
    }
  }

  async function commitSpeakerMerge(source, target) {
    if (!source || !target || source === target) return
    setLocalSegments(segs => segs.map(s => s.speaker === source ? { ...s, speaker: target } : s))
    setSuggestions(items => items.filter(item => item.source !== source))
    setMergeSource('')
    setMergeTarget('')
    try {
      await updateSpeakers(jobId, { [source]: target })
    } catch (e) {
      console.error(e)
    }
  }

  async function handleRediarize() {
    if (!jobId || rediarizeState === 'running') return
    setRediarizeState('running')
    setRediarizeMsg('Avvio diarizzazione...')
    try {
      await rediarizeJob(
        jobId,
        rediarizeSpeakers ? Number(rediarizeSpeakers) : undefined,
        { diarization_mode: rediarizeMode },
      )
      const unsub = subscribeJobEvents(jobId, (data) => {
        setRediarizeMsg(data.message || 'Diarizzazione in corso...')
        if (data.diarization_error || data.stage === 'error' || data.error) {
          setRediarizeState('error')
          setRediarizeMsg(data.diarization_error || data.error || data.message || 'Diarizzazione fallita')
          onJobUpdate?.(data)
          unsub()
        } else if (data.status === 'done') {
          setRediarizeState('done')
          setRediarizeMsg('Diarizzazione completata')
          setLocalSegments(data.segments || [])
          onJobUpdate?.(data)
          unsub()
        } else if (data.status === 'error') {
          setRediarizeState('error')
          setRediarizeMsg(data.error || data.message || 'Diarizzazione fallita')
          unsub()
        }
      }, () => {
        setRediarizeState('error')
        setRediarizeMsg('Connessione persa durante la diarizzazione')
      })
    } catch (e) {
      setRediarizeState('error')
      setRediarizeMsg(e.message)
    }
  }

  // Unique speakers in order
  const speakers = [...new Set(localSegments.map(s => s.speaker).filter(Boolean))]

  useEffect(() => {
    if (!jobId || speakers.length < 2) {
      setSuggestions([])
      return
    }
    let canceled = false
    setLoadingSuggestions(true)
    fetchSpeakerSuggestions(jobId)
      .then(data => {
        if (!canceled) setSuggestions(data.suggestions || [])
      })
      .catch(() => {
        if (!canceled) setSuggestions([])
      })
      .finally(() => {
        if (!canceled) setLoadingSuggestions(false)
      })
    return () => { canceled = true }
  }, [jobId, speakers.join('|')])

  return (
    <div className="space-y-4">
      {/* Re-diarization */}
      <div className="rounded-lg border border-white/5 bg-gray-900/40 p-3 space-y-3">
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label className="label text-[11px]">Speaker attesi</label>
            <select
              className="input py-1.5 text-xs"
              value={rediarizeSpeakers}
              onChange={(e) => setRediarizeSpeakers(e.target.value)}
              disabled={rediarizeState === 'running'}
            >
              <option value="">Auto</option>
              {[1, 2, 3, 4, 5, 6].map(n => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="label text-[11px]">Modalita</label>
            <select
              className="input py-1.5 text-xs"
              value={rediarizeMode}
              onChange={(e) => setRediarizeMode(e.target.value)}
              disabled={rediarizeState === 'running'}
            >
              <option value="conservative">Conservativa</option>
              <option value="balanced">Bilanciata</option>
            </select>
          </div>
          <button
            onClick={handleRediarize}
            disabled={rediarizeState === 'running'}
            className="btn-ghost text-xs disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {rediarizeState === 'running'
              ? <Loader size={13} className="animate-spin" />
              : <RefreshCw size={13} />
            }
            Ridiarizza
          </button>
        </div>
        {rediarizeMsg && (
          <div className={`text-xs flex items-center gap-1.5 ${
            rediarizeState === 'error' ? 'text-red-400'
            : rediarizeState === 'done' ? 'text-green-400'
            : 'text-brand-400'
          }`}>
            {rediarizeState === 'running' && <Loader size={11} className="animate-spin" />}
            {rediarizeState === 'error' && <AlertTriangle size={11} />}
            {rediarizeMsg}
          </div>
        )}
      </div>

      {/* Search */}
      <div className="relative">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
        <input
          className="input pl-9"
          placeholder="Cerca nel testo…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* Speaker legend */}
      {speakers.length > 1 && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {speakers.map(sp => (
              <div key={sp} className="flex items-center gap-1.5 bg-gray-800 rounded-full px-3 py-1">
                {editingSpeaker === sp ? (
                  <>
                    <input
                      autoFocus
                      className="bg-transparent text-xs font-medium w-24 outline-none border-b border-white/30"
                      value={speakerDraft}
                      onChange={(e) => setSpeakerDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') commitSpeakerRename(sp)
                        if (e.key === 'Escape') setEditingSpeaker(null)
                      }}
                    />
                    <button onClick={() => commitSpeakerRename(sp)} className="text-green-400 hover:text-green-300">
                      <Check size={12} />
                    </button>
                    <button onClick={() => setEditingSpeaker(null)} className="text-gray-500 hover:text-gray-300">
                      <X size={12} />
                    </button>
                  </>
                ) : (
                  <>
                    <span className={`text-xs font-medium ${speakerColor(sp)}`}>{sp}</span>
                    <button
                      onClick={() => { setEditingSpeaker(sp); setSpeakerDraft(sp) }}
                      className="text-gray-600 hover:text-gray-400 transition-colors"
                    >
                      <Edit2 size={11} />
                    </button>
                  </>
                )}
              </div>
            ))}
          </div>

          <div className="rounded-lg border border-white/5 bg-gray-900/40 p-3 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-gray-400">Merge speaker</span>
              {loadingSuggestions && <Loader size={12} className="animate-spin text-gray-500" />}
            </div>

            {suggestions.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {suggestions.slice(0, 3).map(item => (
                  <button
                    key={`${item.source}-${item.target}`}
                    onClick={() => commitSpeakerMerge(item.source, item.target)}
                    className="btn-ghost text-xs py-1"
                    title={`${item.source_duration ? formatDuration(item.source_duration) : ''}`}
                  >
                    <GitMerge size={12} />
                    {item.source} → {item.target}
                  </button>
                ))}
              </div>
            )}

            <div className="grid grid-cols-[1fr_1fr_auto] gap-2">
              <select
                className="input py-1.5 text-xs"
                value={mergeSource}
                onChange={(e) => setMergeSource(e.target.value)}
              >
                <option value="">Da</option>
                {speakers.map(sp => <option key={sp} value={sp}>{sp}</option>)}
              </select>
              <select
                className="input py-1.5 text-xs"
                value={mergeTarget}
                onChange={(e) => setMergeTarget(e.target.value)}
              >
                <option value="">A</option>
                {speakers.filter(sp => sp !== mergeSource).map(sp => <option key={sp} value={sp}>{sp}</option>)}
              </select>
              <button
                onClick={() => commitSpeakerMerge(mergeSource, mergeTarget)}
                disabled={!mergeSource || !mergeTarget || mergeSource === mergeTarget}
                className="btn-ghost text-xs disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <GitMerge size={12} /> Unisci
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Segments */}
      <div className="space-y-1 max-h-[60vh] overflow-y-auto pr-1">
        {localSegments
          .map((seg, index) => ({ seg, index }))
          .filter(({ seg }) => !search || seg.text.toLowerCase().includes(search.toLowerCase()))
          .map(({ seg, index }) => {
            const isActive = index === activeIdx
            return (
              <div
                key={seg.id ?? index}
                ref={isActive ? activeRef : null}
                onClick={() => onSeek?.(seg.start)}
                className={`flex gap-3 p-2.5 rounded-lg cursor-pointer transition-all group ${
                  isActive
                    ? 'bg-brand-900/30 border border-brand-500/30'
                    : 'hover:bg-white/3 border border-transparent'
                }`}
              >
                {/* Timestamp */}
                <span className="text-xs text-gray-500 font-mono mt-0.5 flex-shrink-0 w-12">
                  {formatTs(seg.start)}
                </span>

                {/* Content */}
                <div className="flex-1 min-w-0">
                  {seg.speaker && (
                    <span className={`text-xs font-semibold mb-0.5 block ${speakerColor(seg.speaker)}`}>
                      {seg.speaker}
                    </span>
                  )}
                  <p className="text-sm text-gray-300 leading-relaxed">
                    {highlight(seg.text, search)}
                  </p>
                </div>
              </div>
            )
          })}
      </div>
    </div>
  )
}
