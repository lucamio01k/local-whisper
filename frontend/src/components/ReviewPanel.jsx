import { useEffect, useState } from 'react'
import { getJob, reviewRequest } from '../api'

const labels = { speaker_ambiguity: 'Attribuzione incerta', repetition: 'Ripetizione', invalid_timing: 'Tempi non validi', alignment_missing: 'Allineamento mancante', unknown_speaker: 'Speaker incerto', overlap: 'Voci sovrapposte', invalid_word_timing: 'Tempi parola da verificare', low_probability: 'Bassa probabilità' }
const stamp = (n) => Number(n || 0).toFixed(3)

export default function ReviewPanel({ jobId, onSeek, onListen, onJobUpdate }) {
  const [job, setJob] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState('')
  const [text, setText] = useState('')
  const [speaker, setSpeaker] = useState('')
  const [split, setSplit] = useState('')
  const [segmentStart, setSegmentStart] = useState('')
  const [segmentEnd, setSegmentEnd] = useState('')
  const [start, setStart] = useState('0')
  const [end, setEnd] = useState('30')
  const [model, setModel] = useState('large-v3-turbo')
  const [glossary, setGlossary] = useState('')
  const [proposal, setProposal] = useState(null)
  const [versions, setVersions] = useState([])
  const [open, setOpen] = useState(false)
  const base = `/jobs/${jobId}`
  async function refresh() {
    const data = await getJob(jobId)
    setJob(data)
    const history = await reviewRequest(`${base}/revisions`)
    setVersions(history.revisions)
    return data
  }
  useEffect(() => {
    let alive = true
    getJob(jobId).then(data => {
      if (!alive) return
      setJob(data); setModel(data.model || 'large-v3-turbo'); setGlossary(data.glossary || '')
      setEnd(String(Math.min(30, data.duration || 30)))
    }).catch(e => alive && setError(e.message))
    return () => { alive = false }
  }, [jobId])
  useEffect(() => {
    if (!proposal || !['queued', 'running'].includes(proposal.status)) return
    let alive = true
    const timer = setInterval(() => {
      reviewRequest(`${base}/proposals/${proposal.id}`).then(p => alive && setProposal(p)).catch(e => alive && setError(e.message))
    }, 1000)
    return () => { alive = false; clearInterval(timer) }
  }, [proposal?.id, proposal?.status, jobId])
  async function run(action, reload = true) {
    setError(''); setBusy(true)
    try {
      await action()
      if (reload) {
        const data = await refresh()
        onJobUpdate?.({ ...data, id: jobId })
      }
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }
  function choose(id) {
    const seg = job?.segments.find(s => String(s.id) === String(id))
    setSelected(String(id)); setSplit('')
    if (seg) {
      setText(seg.text); setSpeaker(seg.speaker || '')
      setSegmentStart(stamp(seg.start)); setSegmentEnd(stamp(seg.end))
      setStart(stamp(seg.start)); setEnd(stamp(seg.end)); onSeek?.(seg.start)
    }
  }
  const segment = job?.segments.find(s => String(s.id) === selected)
  useEffect(() => {
    const current = job?.segments.find(s => String(s.id) === selected)
    if (current) {
      setText(current.text); setSpeaker(current.speaker || '')
      setSegmentStart(stamp(current.start)); setSegmentEnd(stamp(current.end)); setSplit('')
    } else if (selected) { setSelected('') }
  }, [job?.version, selected])
  function requestProposal(full = false) {
    return run(async () => {
      const p = await reviewRequest(`${base}/proposals`, 'POST', { version: job.version, start: Number(start), end: Number(end), full, model_name: model, glossary })
      setProposal(p)
    }, false)
  }
  return <section className="card p-4 space-y-3">
    <button className="btn-ghost" onClick={() => { setOpen(!open); if (!open) run(refresh, false) }}>Revisione qualità {open ? '▴' : '▾'}</button>
    {error && <p role="alert" className="text-red-400 text-sm">{error} <button className="underline" onClick={() => run(refresh, false)}>Ricarica</button></p>}
    {open && job && <>
      <p className="text-xs text-gray-400">Tempi in secondi con millisecondi. Correzioni versionate; raw originale conservato. Segnalazioni automatiche da verificare sull’audio.</p>
      <div className="flex gap-2 flex-wrap max-h-32 overflow-auto">
        {(job.diagnostics || []).map((issue, i) => <button key={i} className="btn-ghost text-xs" onClick={() => choose(issue.segment_id)}>{stamp(issue.start)} · {labels[issue.kind] || issue.kind}</button>)}
        {!job.diagnostics?.length && <span className="text-xs text-gray-400">Nessuna segnalazione</span>}
      </div>
      <label className="block text-sm">Segmento
        <select className="input w-full" value={selected} onChange={e => choose(e.target.value)}>
          <option value="">Seleziona segmento</option>
          {job.segments.map(s => <option key={s.id} value={s.id}>{stamp(s.start)}–{stamp(s.end)} · {s.speaker || 'Non determinato'} · {s.text.slice(0, 70)}</option>)}
        </select>
      </label>
      {segment && <div className="space-y-2">
        <div className="flex gap-2"><label className="text-xs">Inizio segmento (s)<input className="input w-full" type="number" step="0.001" value={segmentStart} onChange={e => setSegmentStart(e.target.value)} /></label><label className="text-xs">Fine segmento (s)<input className="input w-full" type="number" step="0.001" value={segmentEnd} onChange={e => setSegmentEnd(e.target.value)} /></label></div>
        <textarea aria-label="Testo segmento" className="input w-full min-h-24" value={text} onChange={e => setText(e.target.value)} />
        <input aria-label="Speaker segmento" className="input" placeholder="Non determinato" value={speaker} onChange={e => setSpeaker(e.target.value)} />
        <button disabled={busy} className="btn-ghost" onClick={() => run(() => reviewRequest(`${base}/segments/${selected}`, 'PATCH', { version: job.version, text, speaker: speaker.trim() || null, ...(Number(segmentStart) !== Number(stamp(segment.start)) || Number(segmentEnd) !== Number(stamp(segment.end)) ? { start: Number(segmentStart), end: Number(segmentEnd) } : {}) }))}>Salva correzione</button>
        <p className="text-xs text-gray-500">Modificare testo invalida vecchi tempi parola. Per nuovi tempi, crea ritrascrizione.</p>
        {segment.words?.length > 1 && segment.words.every(w => Number.isFinite(w.start) && Number.isFinite(w.end) && w.end > w.start && !w.timing_issue) && <div className="flex gap-2">
          <select aria-label="Confine divisione" className="input" value={split} onChange={e => setSplit(e.target.value)}>
            <option value="">Dividi prima della parola…</option>
            {segment.words.slice(1).map((w, i) => <option key={i} value={i + 1}>{stamp(w.start)} · {w.word}</option>)}
          </select>
          <button disabled={busy || !split} className="btn-ghost" onClick={() => run(() => reviewRequest(`${base}/segments/${selected}`, 'PATCH', { version: job.version, split_word: Number(split) }))}>Dividi</button>
        </div>}
      </div>}
      <div className="grid grid-cols-2 gap-2">
        <label className="text-sm">Da (s)<input className="input w-full" type="number" min="0" step="0.001" value={start} onChange={e => setStart(e.target.value)} /></label>
        <label className="text-sm">A (s)<input className="input w-full" type="number" min="0" step="0.001" value={end} onChange={e => setEnd(e.target.value)} /></label>
      </div>
      <label className="block text-sm">Modello proposta
        <select className="input w-full" value={model} onChange={e => setModel(e.target.value)}>
          {['tiny', 'base', 'small', 'medium', 'large-v2', 'large-v3', 'large-v3-turbo'].map(m => <option key={m}>{m}</option>)}
        </select>
      </label>
      <textarea aria-label="Glossario proposta" className="input w-full" placeholder="Terminologia per questa proposta" maxLength={4000} value={glossary} onChange={e => setGlossary(e.target.value)} />
      <div className="flex flex-wrap gap-2">
        <button className="btn-ghost" onClick={() => onListen ? onListen(Number(start), Number(end)) : onSeek?.(Number(start))}>Ascolta da inizio intervallo</button>
        <button disabled={busy || ['queued', 'running'].includes(proposal?.status)} className="btn-primary" onClick={() => requestProposal(false)}>Ritrascrivi intervallo ≤ 60 s</button>
        <button disabled={busy || ['queued', 'running'].includes(proposal?.status)} className="btn-ghost" onClick={() => requestProposal(true)}>Proponi rielaborazione completa</button>
      </div>
      <p className="text-xs text-gray-400">Contesto aggiuntivo ±5 s. Confini adeguati alle parole disponibili. Rielaborazione completa ricalcola speaker senza trasferire nomi.</p>
      {proposal && <div className="border border-gray-700 rounded p-3 space-y-2">
        <p className="text-sm">Proposta: {proposal.status} · {proposal.message || proposal.error || ''}</p>
        {['queued', 'running'].includes(proposal.status) && <button className="btn-ghost" onClick={() => run(() => reviewRequest(`${base}/proposals/${proposal.id}/cancel`, 'POST', {}), false)}>Annulla elaborazione</button>}
        {proposal.status === 'ready' && <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
            <div><strong>Originale</strong><p className="max-h-64 overflow-auto whitespace-pre-wrap">{proposal.original.map(s => s.text).join(' ')}</p></div>
            <div><strong>Proposta</strong><p className="max-h-64 overflow-auto whitespace-pre-wrap">{proposal.segments.map(s => s.text).join(' ')}</p></div>
          </div>
          <button className="btn-ghost" onClick={() => onListen ? onListen(proposal.start, proposal.end) : onSeek?.(proposal.start)}>Ascolta stesso intervallo</button>
          <button disabled={busy} className="btn-primary" onClick={() => run(async () => { await reviewRequest(`${base}/proposals/${proposal.id}/apply`, 'POST', { version: job.version }); setProposal(null) })}>Applica proposta</button>
          <button className="btn-ghost" onClick={() => setProposal(null)}>Mantieni originale</button>
        </>}
      </div>}
      <button className="btn-ghost text-xs" onClick={() => run(async () => {
        const data = await reviewRequest(`${base}/reference`)
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
        const link = document.createElement('a'); link.href = url; link.download = `${jobId}-reference-draft.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
      }, false)}>Esporta annotazione per valutazione</button>
      <details><summary className="cursor-pointer text-sm">Versioni e ripristino</summary>
        <div className="max-h-40 overflow-auto">{versions.map(v => <div key={v.version} className="flex gap-2 text-xs p-1"><span>{v.created_at} · {v.reason}</span><button disabled={busy || v.version === job.version} className="underline" onClick={() => run(() => reviewRequest(`${base}/restore`, 'POST', { version: job.version, target_version: v.version }))}>{v.version === job.version ? 'Attiva' : 'Ripristina'}</button></div>)}</div>
      </details>
    </>}
  </section>
}
