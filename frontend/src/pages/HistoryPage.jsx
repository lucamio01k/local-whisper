import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Clock, FileAudio, Users, Trash2, Mic2, Edit2, Check, X,
  ExternalLink, Music, AlertTriangle, Loader, RefreshCw, PauseCircle
} from 'lucide-react'
import { fetchHistory, deleteJob, deleteAudio, renameJob, rediarizeJob, subscribeJobEvents } from '../api'

function formatDuration(s) {
  if (!s) return '—'
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}`
}

function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Intl.DateTimeFormat('it-IT', {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    }).format(new Date(iso))
  } catch {
    return iso
  }
}

function ConfirmDialog({ message, onConfirm, onCancel }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="card p-6 max-w-sm w-full mx-4 space-y-4">
        <div className="flex items-start gap-3">
          <AlertTriangle size={20} className="text-red-400 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-gray-300">{message}</p>
        </div>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="btn-ghost">Annulla</button>
          <button onClick={onConfirm} className="btn-danger">Elimina</button>
        </div>
      </div>
    </div>
  )
}

function JobCard({ job, onUpdated, onDeleted }) {
  const navigate = useNavigate()
  const [editing, setEditing] = useState(false)
  const [titleDraft, setTitleDraft] = useState(job.title)
  const [confirm, setConfirm] = useState(null) // 'job' | 'audio'
  const [rediarizingState, setRediarizingState] = useState(null) // null | 'running' | 'done' | 'error'
  const [rediarizingMsg, setRediarizingMsg] = useState('')
  const isActive = ['queued', 'downloading_yt', 'processing', 'paused'].includes(job.status)

  async function commitRename() {
    if (!titleDraft.trim() || titleDraft === job.title) { setEditing(false); return }
    try {
      await renameJob(job.id, titleDraft.trim())
      onUpdated({ ...job, title: titleDraft.trim() })
    } catch (e) {
      alert(e.message)
    }
    setEditing(false)
  }

  async function handleDeleteJob() {
    try {
      await deleteJob(job.id)
      onDeleted(job.id)
    } catch (e) {
      alert(e.message)
    }
    setConfirm(null)
  }

  async function handleDeleteAudio() {
    try {
      await deleteAudio(job.id)
      onUpdated({ ...job, has_audio: false })
    } catch (e) {
      alert(e.message)
    }
    setConfirm(null)
  }

  async function handleRediarize() {
    setRediarizingState('running')
    setRediarizingMsg('Avvio…')
    try {
      await rediarizeJob(job.id)
      const unsub = subscribeJobEvents(job.id, (data) => {
        if (data.stage === 'error' || data.error) {
          setRediarizingState('error')
          setRediarizingMsg(data.error || data.message || 'Errore')
          unsub()
        } else if (data.status === 'done') {
          setRediarizingState('done')
          setRediarizingMsg('Completata!')
          onUpdated({ ...job, has_diarization: true })
          unsub()
          setTimeout(() => setRediarizingState(null), 3000)
        } else if (data.status === 'error') {
          setRediarizingState('error')
          setRediarizingMsg(data.error || 'Errore')
          unsub()
        } else {
          setRediarizingMsg(data.message || 'In corso…')
        }
      }, () => {
        setRediarizingState('error')
        setRediarizingMsg('Connessione persa')
      })
    } catch (e) {
      setRediarizingState('error')
      setRediarizingMsg(e.message)
    }
  }

  return (
    <>
      <div className="card p-4 hover:border-white/10 transition-colors">
        <div className="flex items-start gap-3">
          {/* Icon */}
          <div className="w-10 h-10 rounded-lg bg-brand-900/40 flex items-center justify-center flex-shrink-0">
            {job.youtube_url
              ? <span className="text-base">▶️</span>
              : <FileAudio size={18} className="text-brand-400" />
            }
          </div>

          {/* Main content */}
          <div className="flex-1 min-w-0">
            {/* Title row */}
            <div className="flex items-center gap-2">
              {editing ? (
                <div className="flex items-center gap-1.5 flex-1">
                  <input
                    autoFocus
                    className="input py-1 text-sm flex-1"
                    value={titleDraft}
                    onChange={e => setTitleDraft(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') setEditing(false) }}
                  />
                  <button onClick={commitRename} className="text-green-400 hover:text-green-300"><Check size={15} /></button>
                  <button onClick={() => setEditing(false)} className="text-gray-500 hover:text-gray-300"><X size={15} /></button>
                </div>
              ) : (
                <div className="flex items-center gap-1.5 min-w-0">
                  <h3 className="font-medium text-gray-200 truncate">{job.title || job.filename || job.id}</h3>
                  <button
                    onClick={() => { setTitleDraft(job.title || job.filename || ''); setEditing(true) }}
                    className="text-gray-600 hover:text-gray-400 flex-shrink-0"
                  >
                    <Edit2 size={13} />
                  </button>
                </div>
              )}
            </div>

            {/* Meta row */}
            <div className="flex flex-wrap items-center gap-3 mt-1.5 text-xs text-gray-500">
              <span className="flex items-center gap-1"><Clock size={11} />{formatDate(job.created_at)}</span>
              {job.duration && <span>{formatDuration(job.duration)}</span>}
              {job.model && <span className="font-mono">{job.model}</span>}
              {job.language && <span>{job.language.toUpperCase()}</span>}
              {job.segment_count > 0 && <span>{job.segment_count} segmenti</span>}
            </div>

            {/* Badges */}
            <div className="flex flex-wrap gap-1.5 mt-2">
              {isActive && (
                <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-medium bg-brand-900/50 text-brand-300">
                  {job.status === 'paused'
                    ? <PauseCircle size={10} />
                    : <Loader size={10} className="animate-spin" />
                  }
                  {job.status === 'paused' ? 'In pausa' : `${job.progress || 0}%`}
                </span>
              )}
              <span className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-medium ${
                job.has_audio ? 'bg-blue-900/40 text-blue-300' : 'bg-gray-800 text-gray-600 line-through'
              }`}>
                <Music size={10} /> Audio
              </span>
              <span className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-medium ${
                job.has_transcript ? 'bg-green-900/40 text-green-300' : 'bg-gray-800 text-gray-600'
              }`}>
                <FileAudio size={10} /> Testo
              </span>
              <span className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-medium ${
                job.has_diarization ? 'bg-violet-900/40 text-violet-300' : 'bg-gray-800 text-gray-600'
              }`}>
                <Users size={10} /> Speaker
              </span>
            </div>

            {/* Re-diarize feedback */}
            {rediarizingState && (
              <div className={`mt-2 text-xs flex items-center gap-1.5 ${
                rediarizingState === 'error' ? 'text-red-400'
                : rediarizingState === 'done' ? 'text-green-400'
                : 'text-brand-400'
              }`}>
                {rediarizingState === 'running' && <Loader size={11} className="animate-spin" />}
                {rediarizingState === 'done' && <Check size={11} />}
                {rediarizingState === 'error' && <AlertTriangle size={11} />}
                {rediarizingMsg}
              </div>
            )}
            {isActive && job.message && !rediarizingState && (
              <div className="mt-2 text-xs flex items-center gap-1.5 text-brand-400">
                {job.status === 'paused' ? <PauseCircle size={11} /> : <Loader size={11} className="animate-spin" />}
                {job.message}
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="flex flex-col gap-1 flex-shrink-0">
            <button
              onClick={() => navigate(`/?job=${job.id}`)}
              className="btn-ghost text-xs px-2 py-1.5"
              title="Apri trascrizione"
            >
              <ExternalLink size={13} /> Apri
            </button>

            {job.has_transcript && !job.has_diarization && job.has_audio && (
              <button
                onClick={handleRediarize}
                disabled={rediarizingState === 'running' || isActive}
                className="btn-ghost text-xs px-2 py-1.5 text-violet-400 hover:text-violet-300"
                title={isActive ? 'Job già in elaborazione' : 'Aggiungi diarizzazione speaker'}
              >
                {rediarizingState === 'running' || isActive
                  ? <Loader size={13} className="animate-spin" />
                  : <RefreshCw size={13} />
                }
                Speaker
              </button>
            )}

            {job.has_audio && (
              <button
                onClick={() => setConfirm('audio')}
                disabled={isActive}
                className="btn-ghost text-xs px-2 py-1.5 text-orange-400 hover:text-orange-300 disabled:opacity-40 disabled:cursor-not-allowed"
                title={isActive ? 'Job in elaborazione' : 'Elimina solo il file audio'}
              >
                <Trash2 size={13} /> Audio
              </button>
            )}

            <button
              onClick={() => setConfirm('job')}
              disabled={isActive}
              className="btn-ghost text-xs px-2 py-1.5 text-red-400 hover:text-red-300 disabled:opacity-40 disabled:cursor-not-allowed"
              title={isActive ? 'Job in elaborazione' : 'Elimina tutto'}
            >
              <Trash2 size={13} /> Tutto
            </button>
          </div>
        </div>
      </div>

      {confirm === 'job' && (
        <ConfirmDialog
          message={`Eliminare "${job.title || job.filename}"? Verranno cancellati audio, testo e dati. Questa azione è irreversibile.`}
          onConfirm={handleDeleteJob}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm === 'audio' && (
        <ConfirmDialog
          message={`Eliminare il file audio di "${job.title || job.filename}"? Il testo rimarrà ma non potrai più riprodurlo o ri-diarizzare.`}
          onConfirm={handleDeleteAudio}
          onCancel={() => setConfirm(null)}
        />
      )}
    </>
  )
}

export default function HistoryPage() {
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')

  useEffect(() => {
    load()
  }, [])

  useEffect(() => {
    const hasActive = history.some(j =>
      ['queued', 'downloading_yt', 'processing', 'paused'].includes(j.status)
    )
    if (!hasActive) return undefined
    const id = setInterval(() => load(true), 3000)
    return () => clearInterval(id)
  }, [history])

  async function load(silent = false) {
    if (!silent) setLoading(true)
    try {
      const data = await fetchHistory()
      setHistory(data)
    } catch (e) {
      console.error(e)
    }
    if (!silent) setLoading(false)
  }

  function handleUpdated(updated) {
    setHistory(h => h.map(j => j.id === updated.id ? updated : j))
  }

  function handleDeleted(id) {
    setHistory(h => h.filter(j => j.id !== id))
  }

  const filtered = history.filter(j =>
    !search || (j.title || j.filename || '').toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-xl font-semibold text-white">Archivio</h1>
          <p className="text-sm text-gray-500 mt-0.5">{history.length} trascrizioni salvate</p>
        </div>
        <input
          className="input w-64"
          placeholder="Cerca per titolo…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-gray-500 py-12 justify-center">
          <Loader size={18} className="animate-spin" /> Caricamento…
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 text-gray-600">
          <Clock size={40} className="mx-auto mb-4 opacity-30" />
          <p className="text-sm">{search ? 'Nessun risultato.' : 'Nessuna trascrizione ancora. Inizia dalla pagina Trascrivi!'}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map(job => (
            <JobCard
              key={job.id}
              job={job}
              onUpdated={handleUpdated}
              onDeleted={handleDeleted}
            />
          ))}
        </div>
      )}
    </div>
  )
}
