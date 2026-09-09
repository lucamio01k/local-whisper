import { useState, useRef } from 'react'
import { Upload, Youtube, X, FileAudio, Loader, Info } from 'lucide-react'
import { getYouTubeInfo } from '../api'

const ACCEPTED = '.mp3,.wav,.m4a,.mp4,.mov,.ogg,.opus,.webm'

export default function FileUpload({ onFile, onYouTube }) {
  const [mode, setMode] = useState('file') // 'file' | 'youtube'
  const [dragging, setDragging] = useState(false)
  const [file, setFile] = useState(null)
  const [ytUrl, setYtUrl] = useState('')
  const [ytInfo, setYtInfo] = useState(null)
  const [ytLoading, setYtLoading] = useState(false)
  const [ytError, setYtError] = useState('')
  const inputRef = useRef(null)

  function handleFile(f) {
    setFile(f)
    onFile(f)
  }

  function handleDrop(e) {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }

  async function handleYtBlur() {
    if (!ytUrl.trim()) return
    setYtLoading(true)
    setYtError('')
    setYtInfo(null)
    try {
      const info = await getYouTubeInfo(ytUrl.trim())
      setYtInfo(info)
      onYouTube(ytUrl.trim())
    } catch (e) {
      setYtError(e.message)
    } finally {
      setYtLoading(false)
    }
  }

  function clearFile() {
    setFile(null)
    onFile(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  function clearYt() {
    setYtUrl('')
    setYtInfo(null)
    setYtError('')
    onYouTube(null)
  }

  function formatDuration(s) {
    if (!s) return ''
    const m = Math.floor(s / 60)
    const sec = s % 60
    return `${m}:${String(sec).padStart(2, '0')}`
  }

  return (
    <div className="space-y-3">
      {/* Mode tabs */}
      <div className="flex gap-1 p-1 bg-gray-800 rounded-lg w-fit">
        {[
          { id: 'file', label: 'File locale', Icon: Upload },
          { id: 'youtube', label: 'YouTube', Icon: Youtube },
        ].map(({ id, label, Icon }) => (
          <button
            key={id}
            onClick={() => setMode(id)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-all ${
              mode === id ? 'bg-gray-700 text-white' : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}
      </div>

      {mode === 'file' ? (
        file ? (
          <div className="flex items-center gap-3 p-3 rounded-lg border border-white/10 bg-gray-800/60">
            <FileAudio size={20} className="text-brand-400 flex-shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-gray-200 truncate">{file.name}</p>
              <p className="text-xs text-gray-500">{(file.size / 1024 / 1024).toFixed(1)} MB</p>
            </div>
            <button onClick={clearFile} className="text-gray-500 hover:text-gray-300 transition-colors">
              <X size={16} />
            </button>
          </div>
        ) : (
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => inputRef.current?.click()}
            className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all ${
              dragging
                ? 'border-brand-500 bg-brand-900/10'
                : 'border-white/10 hover:border-white/20 hover:bg-white/3'
            }`}
          >
            <Upload size={28} className="mx-auto mb-3 text-gray-500" />
            <p className="text-sm text-gray-300 font-medium">Trascina un file qui</p>
            <p className="text-xs text-gray-500 mt-1">o clicca per sfogliare</p>
            <p className="text-xs text-gray-600 mt-2">{ACCEPTED.replaceAll(',', ' ')}</p>
            <input
              ref={inputRef}
              type="file"
              accept={ACCEPTED}
              className="hidden"
              onChange={(e) => e.target.files[0] && handleFile(e.target.files[0])}
            />
          </div>
        )
      ) : (
        <div className="space-y-2">
          <div className="flex gap-2">
            <input
              className="input flex-1"
              placeholder="https://youtube.com/watch?v=..."
              value={ytUrl}
              onChange={(e) => { setYtUrl(e.target.value); setYtInfo(null); setYtError('') }}
              onBlur={handleYtBlur}
              onKeyDown={(e) => e.key === 'Enter' && handleYtBlur()}
            />
            {ytUrl && (
              <button onClick={clearYt} className="btn-ghost px-2">
                <X size={16} />
              </button>
            )}
          </div>

          {ytLoading && (
            <div className="flex items-center gap-2 text-sm text-gray-400">
              <Loader size={14} className="animate-spin" /> Caricamento info…
            </div>
          )}

          {ytError && (
            <p className="text-sm text-red-400">{ytError}</p>
          )}

          {ytInfo && (
            <div className="flex items-start gap-3 p-3 rounded-lg border border-white/10 bg-gray-800/60">
              {ytInfo.thumbnail && (
                <img src={ytInfo.thumbnail} alt="" className="w-16 h-10 object-cover rounded flex-shrink-0" />
              )}
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-200 truncate">{ytInfo.title}</p>
                <p className="text-xs text-gray-500">
                  {ytInfo.uploader} {ytInfo.duration ? `· ${formatDuration(ytInfo.duration)}` : ''}
                </p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
