import { useEffect, useRef, useState, forwardRef, useImperativeHandle } from 'react'
import { Play, Pause, Volume2 } from 'lucide-react'

function formatTime(s) {
  if (!isFinite(s)) return '0:00'
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}`
}

const AudioPlayer = forwardRef(function AudioPlayer({ src }, ref) {
  const audioRef = useRef(null)
  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(1)

  useImperativeHandle(ref, () => ({
    seekTo(seconds) {
      if (audioRef.current) {
        audioRef.current.currentTime = seconds
        audioRef.current.play()
        setPlaying(true)
      }
    },
    getCurrentTime() {
      return audioRef.current?.currentTime ?? 0
    },
  }))

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    const onTime = () => setCurrentTime(audio.currentTime)
    const onDur = () => setDuration(audio.duration)
    const onEnded = () => setPlaying(false)
    audio.addEventListener('timeupdate', onTime)
    audio.addEventListener('loadedmetadata', onDur)
    audio.addEventListener('ended', onEnded)
    return () => {
      audio.removeEventListener('timeupdate', onTime)
      audio.removeEventListener('loadedmetadata', onDur)
      audio.removeEventListener('ended', onEnded)
    }
  }, [src])

  function togglePlay() {
    if (!audioRef.current) return
    if (playing) { audioRef.current.pause(); setPlaying(false) }
    else { audioRef.current.play(); setPlaying(true) }
  }

  function seekScrub(e) {
    if (!audioRef.current || !duration) return
    const rect = e.currentTarget.getBoundingClientRect()
    const ratio = (e.clientX - rect.left) / rect.width
    audioRef.current.currentTime = ratio * duration
  }

  function changeVolume(e) {
    const v = parseFloat(e.target.value)
    setVolume(v)
    if (audioRef.current) audioRef.current.volume = v
  }

  return (
    <div className="card p-4 flex items-center gap-4">
      <audio ref={audioRef} src={src} preload="metadata" />

      <button onClick={togglePlay} className="btn-primary rounded-full w-10 h-10 p-0 flex items-center justify-center flex-shrink-0">
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
          type="range" min="0" max="1" step="0.05" value={volume}
          onChange={changeVolume}
          className="w-20 accent-brand-500"
        />
      </div>
    </div>
  )
})

export default AudioPlayer
