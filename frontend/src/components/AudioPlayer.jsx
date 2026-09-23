import { useEffect, useRef, useState, forwardRef, useImperativeHandle } from 'react'
import { Play, Pause, Volume2 } from 'lucide-react'

function formatTime(s) {
  if (!isFinite(s)) return '0:00'
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}`
}

const AudioPlayer = forwardRef(function AudioPlayer({ src, onPlayingChange }, ref) {
  const audioRef = useRef(null)
  const stopAtRef = useRef(null)
  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(1)
  const [playError, setPlayError] = useState('')

  function startPlayback() {
    setPlayError('')
    audioRef.current?.play().catch(() => setPlayError('Riproduzione non disponibile. Riprova dal player.'))
  }

  useImperativeHandle(ref, () => ({
    seekTo(seconds) {
      stopAtRef.current = null
      if (audioRef.current) {
        audioRef.current.currentTime = seconds
        startPlayback()
      }
    },
    playRange(start, end) {
      if (audioRef.current) {
        stopAtRef.current = end
        audioRef.current.currentTime = start
        startPlayback()
      }
    },
    pause() { audioRef.current?.pause() },
    getCurrentTime() {
      return audioRef.current?.currentTime ?? 0
    },
  }))

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    stopAtRef.current = null
    setPlaying(false)
    setCurrentTime(0)
    setDuration(0)
    setPlayError('')
    const onTime = () => {
      setCurrentTime(audio.currentTime)
      if (stopAtRef.current !== null && audio.currentTime >= stopAtRef.current) {
        audio.pause(); stopAtRef.current = null; setPlaying(false)
      }
    }
    const onDur = () => setDuration(audio.duration)
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onEnded = () => { stopAtRef.current = null; setPlaying(false) }
    const onError = () => { setPlaying(false); setPlayError('Audio non disponibile.') }
    audio.addEventListener('play', onPlay)
    audio.addEventListener('pause', onPause)
    audio.addEventListener('error', onError)
    audio.addEventListener('timeupdate', onTime)
    audio.addEventListener('loadedmetadata', onDur)
    audio.addEventListener('ended', onEnded)
    return () => {
      audio.removeEventListener('play', onPlay)
      audio.removeEventListener('pause', onPause)
      audio.removeEventListener('error', onError)
      audio.removeEventListener('timeupdate', onTime)
      audio.removeEventListener('loadedmetadata', onDur)
      audio.removeEventListener('ended', onEnded)
    }
  }, [src])

  useEffect(() => { onPlayingChange?.(playing) }, [playing, onPlayingChange])

  function togglePlay() {
    if (!audioRef.current) return
    if (!audioRef.current.paused) audioRef.current.pause()
    else startPlayback()
  }

  function seekScrub(e) {
    if (!audioRef.current || !duration) return
    const rect = e.currentTarget.getBoundingClientRect()
    const ratio = (e.clientX - rect.left) / rect.width
    stopAtRef.current = null
    audioRef.current.currentTime = Math.max(0, Math.min(1, ratio)) * duration
  }

  function changeVolume(e) {
    const v = parseFloat(e.target.value)
    setVolume(v)
    if (audioRef.current) audioRef.current.volume = v
  }

  return (
    <>
    <div className="card p-4 flex items-center gap-4">
      <audio ref={audioRef} src={src} preload="metadata" />

      <button aria-label={playing ? "Pausa audio" : "Riprendi audio"} onClick={togglePlay} className="btn-primary rounded-full w-10 h-10 p-0 flex items-center justify-center flex-shrink-0">
        {playing ? <Pause size={18} /> : <Play size={18} className="ml-0.5" />}
      </button>

      <div className="flex-1 space-y-1 min-w-0">
        <div
          className="h-2 bg-gray-700 rounded-full cursor-pointer relative overflow-hidden"
          onClick={seekScrub}
        >
          <div
            className="absolute left-0 top-0 h-full bg-brand-500 rounded-full transition-none"
            style={{ width: duration ? `${(currentTime / duration) * 100}%` : '0%' }}
          />
        </div>
        <div className="flex justify-between text-xs text-gray-500">
          <span>{formatTime(currentTime)}</span>
          <span>{formatTime(duration)}</span>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-shrink-0">
        <Volume2 size={14} className="text-gray-500" />
        <input
          aria-label="Volume audio" type="range" min="0" max="1" step="0.05" value={volume}
          onChange={changeVolume}
          className="w-20 accent-brand-500"
        />
      </div>
    </div>
    {playError && <p role="alert" className="text-sm text-red-300">{playError}</p>}
    {playing && <div className="fixed bottom-4 right-4 z-50 flex items-center gap-3 rounded-xl border border-brand-400/40 bg-gray-950 px-4 py-3 shadow-xl max-w-[calc(100vw-2rem)]">
      <span className="text-xs text-gray-300 font-mono">{formatTime(currentTime)}</span>
      <button type="button" onClick={() => audioRef.current?.pause()} className="btn-primary" aria-label="Pausa audio, comando sempre visibile">
        <Pause size={16} /> Pausa audio
      </button>
    </div>}
    </>
  )
})

export default AudioPlayer
