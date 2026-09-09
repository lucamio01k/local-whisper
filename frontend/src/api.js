const BASE = '/api'

export async function fetchModels() {
  const r = await fetch(`${BASE}/models`)
  if (!r.ok) throw new Error('Errore caricamento modelli')
  return r.json()
}

export async function fetchConfig() {
  const r = await fetch(`${BASE}/config`)
  if (!r.ok) throw new Error('Errore config')
  return r.json()
}

export async function saveConfig(cfg) {
  const r = await fetch(`${BASE}/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  })
  if (!r.ok) throw new Error('Errore salvataggio config')
  return r.json()
}

export async function getYouTubeInfo(url) {
  const r = await fetch(`${BASE}/youtube/info`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({}))
    throw new Error(err.detail || 'Errore info YouTube')
  }
  return r.json()
}

export async function startTranscription({
  file,
  youtubeUrl,
  modelName,
  language,
  diarize,
  expectedSpeakers,
  diarizationMode,
  diarizationStart,
  diarizationDevice,
  performanceProfile,
  transcriptionBackend,
}) {
  const form = new FormData()
  if (file) form.append('file', file)
  if (youtubeUrl) form.append('youtube_url', youtubeUrl)
  form.append('model_name', modelName)
  if (language) form.append('language', language)
  form.append('diarize', String(diarize))
  if (expectedSpeakers) form.append('expected_speakers', String(expectedSpeakers))
  if (diarizationMode) form.append('diarization_mode', diarizationMode)
  if (diarizationStart) form.append('diarization_start', diarizationStart)
  if (diarizationDevice) form.append('diarization_device', diarizationDevice)
  if (performanceProfile) form.append('performance_profile', performanceProfile)
  if (transcriptionBackend) form.append('transcription_backend', transcriptionBackend)

  const r = await fetch(`${BASE}/transcribe`, { method: 'POST', body: form })
  if (!r.ok) {
    const err = await r.json().catch(() => ({}))
    throw new Error(err.detail || 'Errore avvio trascrizione')
  }
  return r.json()
}

export async function getJob(jobId) {
  const r = await fetch(`${BASE}/jobs/${jobId}`)
  if (!r.ok) throw new Error('Job non trovato')
  return r.json()
}

export async function pauseJob(jobId) {
  const r = await fetch(`${BASE}/jobs/${jobId}/pause`, { method: 'POST' })
  if (!r.ok) {
    const err = await r.json().catch(() => ({}))
    throw new Error(err.detail || 'Errore pausa job')
  }
  return r.json()
}

export async function resumeJob(jobId) {
  const r = await fetch(`${BASE}/jobs/${jobId}/resume`, { method: 'POST' })
  if (!r.ok) {
    const err = await r.json().catch(() => ({}))
    throw new Error(err.detail || 'Errore ripresa job')
  }
  return r.json()
}

export async function cancelJob(jobId) {
  const r = await fetch(`${BASE}/jobs/${jobId}/cancel`, { method: 'POST' })
  if (!r.ok) {
    const err = await r.json().catch(() => ({}))
    throw new Error(err.detail || 'Errore annullamento job')
  }
  return r.json()
}

export function subscribeJobEvents(jobId, onData, onError) {
  const es = new EventSource(`${BASE}/jobs/${jobId}/events`)
  let completed = false
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data)
      if (data.status === 'done' || data.status === 'error' || data.status === 'canceled') {
        completed = true
        es.close()
      }
      onData(data)
    } catch { /* ignore */ }
  }
  es.onerror = () => {
    es.close()
    if (!completed) onError?.()
  }
  return () => es.close()
}

export async function updateSpeakers(jobId, mapping) {
  const r = await fetch(`${BASE}/jobs/${jobId}/speakers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mapping }),
  })
  if (!r.ok) throw new Error('Errore aggiornamento speaker')
  return r.json()
}

export async function fetchSpeakerSuggestions(jobId) {
  const r = await fetch(`${BASE}/jobs/${jobId}/speakers/suggestions`)
  if (!r.ok) throw new Error('Errore suggerimenti speaker')
  return r.json()
}

export async function renameJob(jobId, title) {
  const r = await fetch(`${BASE}/jobs/${jobId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  if (!r.ok) throw new Error('Errore rinomina')
  return r.json()
}

export async function deleteJob(jobId) {
  const r = await fetch(`${BASE}/jobs/${jobId}`, { method: 'DELETE' })
  if (!r.ok) throw new Error('Errore eliminazione')
  return r.json()
}

export async function deleteAudio(jobId) {
  const r = await fetch(`${BASE}/jobs/${jobId}/audio`, { method: 'DELETE' })
  if (!r.ok) throw new Error('Errore eliminazione audio')
  return r.json()
}

export async function rediarizeJob(jobId, expectedSpeakers, options = {}) {
  const payload = {
    ...(expectedSpeakers ? { expected_speakers: expectedSpeakers } : {}),
    ...options,
  }
  const body = Object.keys(payload).length ? JSON.stringify(payload) : undefined
  const r = await fetch(`${BASE}/jobs/${jobId}/diarize`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body,
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({}))
    throw new Error(err.detail || 'Errore avvio diarizzazione')
  }
  return r.json()
}

export async function fetchHistory() {
  const r = await fetch(`${BASE}/history`)
  if (!r.ok) throw new Error('Errore caricamento storia')
  return r.json()
}

export function exportUrl(jobId, fmt, variant = 'speakers') {
  return `${BASE}/jobs/${jobId}/export/${fmt}?variant=${encodeURIComponent(variant)}`
}

export function audioUrl(jobId) {
  return `${BASE}/audio/${jobId}`
}

export function subscribeModelDownload(modelName, onData, onError) {
  const es = new EventSource(`${BASE}/models/${modelName}/download`)
  let completed = false

  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data)
      if (data.status === 'done' || data.status === 'error') {
        completed = true
        es.close()
      }
      onData(data)
    } catch { /* ignore */ }
  }
  es.onerror = () => {
    es.close()
    if (!completed) onError?.()
  }
  return () => es.close()
}
