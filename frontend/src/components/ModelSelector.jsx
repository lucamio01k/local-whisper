import { useState, useEffect } from 'react'
import { Download, CheckCircle, Loader, Info } from 'lucide-react'
import { fetchModels, subscribeModelDownload } from '../api'

const MODEL_INFO = {
  'tiny':           { speed: 5, quality: 2 },
  'base':           { speed: 4, quality: 3 },
  'small':          { speed: 3, quality: 4 },
  'medium':         { speed: 2, quality: 4 },
  'large-v2':       { speed: 1, quality: 5 },
  'large-v3':       { speed: 1, quality: 5 },
  'large-v3-turbo': { speed: 3, quality: 5 },
}

function Stars({ n, max = 5 }) {
  return (
    <span className="text-xs tracking-tight">
      {'★'.repeat(n)}<span className="text-gray-700">{'★'.repeat(max - n)}</span>
    </span>
  )
}

const SIZE_LABELS = {
  tiny: '75 MB', base: '145 MB', small: '465 MB', medium: '1.5 GB',
  'large-v2': '3.1 GB', 'large-v3': '3.1 GB', 'large-v3-turbo': '800 MB',
}

export default function ModelSelector({ selected, onChange, onSelectedStatusChange, backend = 'faster_whisper' }) {
  const [models, setModels] = useState([])
  const [loadedBackend, setLoadedBackend] = useState(null)
  const [downloading, setDownloading] = useState({})
  const [errors, setErrors] = useState({})
  const [showInfo, setShowInfo] = useState(false)

  useEffect(() => {
    let alive = true
    fetchModels(backend).then(data => {
      if (alive) { setModels(data); setLoadedBackend(backend) }
    }).catch(e => console.error(e))
    return () => { alive = false }
  }, [backend])

  useEffect(() => {
    if (!models.length || loadedBackend !== backend) {
      onSelectedStatusChange?.(false)
      return
    }

    const selectedModel = models.find(m => m.name === selected)
    const isSelectedReady = Boolean(selectedModel?.downloaded)
    onSelectedStatusChange?.(isSelectedReady)

    if (!isSelectedReady) {
      const firstDownloaded = models.find(m => m.downloaded)
      if (firstDownloaded) onChange(firstDownloaded.name)
    }
  }, [models, selected, onChange, onSelectedStatusChange, backend, loadedBackend])

  function startDownload(modelName) {
    setErrors(e => ({ ...e, [modelName]: null }))
    setDownloading(d => ({ ...d, [modelName]: 0 }))

    const unsub = subscribeModelDownload(
      modelName,
      (data) => {
        if (data.status === 'done') {
          setDownloading(d => { const n = { ...d }; delete n[modelName]; return n })
          setModels(m => m.map(mod => mod.name === modelName ? { ...mod, downloaded: true } : mod))
          // Auto-select the model just downloaded
          onChange(modelName)
        } else if (data.status === 'error') {
          setDownloading(d => { const n = { ...d }; delete n[modelName]; return n })
          setErrors(e => ({ ...e, [modelName]: data.error || 'Errore download' }))
        } else {
          setDownloading(d => ({ ...d, [modelName]: data.progress || 0 }))
        }
      },
      () => {
        setDownloading(d => { const n = { ...d }; delete n[modelName]; return n })
        setErrors(e => ({ ...e, [modelName]: 'Connessione interrotta' }))
      },
      backend
    )
    return unsub
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="label mb-0">Modello Whisper</p>
        <button
          onClick={() => setShowInfo(v => !v)}
          className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-300 transition-colors"
        >
          <Info size={13} />
          {showInfo ? 'Nascondi confronto' : 'Confronta modelli'}
        </button>
      </div>

      {showInfo && (
        <div className="rounded-lg border border-white/10 overflow-hidden text-xs">
          <table className="w-full">
            <thead>
              <tr className="bg-gray-800/80 text-gray-400">
                <th className="text-left px-3 py-2 font-medium">Modello</th>
                <th className="text-right px-3 py-2 font-medium">Dim.</th>
                <th className="px-3 py-2 font-medium">Velocità</th>
                <th className="px-3 py-2 font-medium">Qualità</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m, i) => {
                const info = MODEL_INFO[m.name]
                return (
                  <tr
                    key={m.name}
                    onClick={() => m.downloaded && onChange(m.name)}
                    className={`border-t border-white/5 transition-colors ${
                      m.downloaded ? 'cursor-pointer hover:bg-white/3' : 'opacity-50'
                    } ${selected === m.name ? 'bg-brand-900/20' : ''}`}
                  >
                    <td className="px-3 py-2 font-mono font-medium text-gray-200">{m.name}</td>
                    <td className="px-3 py-2 text-right text-gray-400">{SIZE_LABELS[m.name]}</td>
                    <td className="px-3 py-2 text-center"><Stars n={info?.speed ?? 0} /></td>
                    <td className="px-3 py-2 text-center"><Stars n={info?.quality ?? 0} /></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="px-3 py-2 text-gray-600 bg-gray-900/60 border-t border-white/5">
            💡 <strong className="text-gray-500">large-v3-turbo</strong> = modello rapido; qualità da confrontare sui propri audio.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {models.map((m) => {
          const isDownloading = modelName => downloading[modelName] !== undefined
          const pct = downloading[m.name]
          const isDl = pct !== undefined
          const err = errors[m.name]

          return (
            <div
              key={m.name}
              onClick={() => m.downloaded && !isDl && onChange(m.name)}
              className={`relative rounded-lg border p-3 cursor-pointer transition-all ${
                selected === m.name && m.downloaded
                  ? 'border-brand-500 bg-brand-900/20'
                  : m.downloaded
                  ? 'border-white/10 bg-gray-800/60 hover:border-white/20'
                  : 'border-white/5 bg-gray-800/30 cursor-default opacity-70'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  {m.downloaded ? (
                    <CheckCircle size={14} className="text-green-400 flex-shrink-0" />
                  ) : isDl ? (
                    <Loader size={14} className="text-brand-400 animate-spin flex-shrink-0" />
                  ) : (
                    <Download size={14} className="text-gray-500 flex-shrink-0" />
                  )}
                  <span className="font-mono text-sm font-medium text-gray-200 truncate">{m.name}</span>
                </div>
                <span className="text-xs text-gray-500 flex-shrink-0">{SIZE_LABELS[m.name]}</span>
              </div>

              {/* Download progress bar */}
              {isDl && (
                <div className="mt-2">
                  <div className="h-1 bg-gray-700 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-brand-500 transition-all duration-300"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <p className="text-xs text-gray-500 mt-1">{pct}%</p>
                </div>
              )}

              {err && <p className="text-xs text-red-400 mt-1">{err}</p>}

              {/* Download button */}
              {!m.downloaded && !isDl && (
                <button
                  onClick={(e) => { e.stopPropagation(); startDownload(m.name) }}
                  className="mt-2 text-xs text-brand-400 hover:text-brand-300 font-medium flex items-center gap-1"
                >
                  <Download size={12} /> Scarica
                </button>
              )}

              {selected === m.name && m.downloaded && (
                <span className="absolute top-1.5 right-1.5 text-[10px] font-semibold text-brand-400 bg-brand-900/60 px-1.5 py-0.5 rounded">
                  selezionato
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
