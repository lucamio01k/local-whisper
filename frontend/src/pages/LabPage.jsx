import { useEffect, useState } from 'react'
import { reviewRequest } from '../api'
import LabCharts, { AudioWindow } from './LabCharts'

const percentage = value => value == null ? '—' : `${(value * 100).toFixed(2)}%`
const decimal = value => value == null ? '—' : value.toFixed(3)
const labels = { ambiguity_margin: 'Margine ambiguità', acoustic_confidence: 'Soglia acustica (coseno)', semantic_confidence: 'Soglia semantica', change_confidence: 'Soglia modifica', decision_margin: 'Margine decisione', context_turns: 'Turni contesto per lato', audio_window: 'Finestra audio (s)', min_audio: 'Minimo audio (s)' }
const outcomeLabels = { correct_correction: 'Correzione corretta', wrong_correction: 'Correzione sbagliata', new_error: 'Nuovo errore', remaining_error: 'Errore rimasto', unscored: 'Gold assente/overlap' }
const inputClass = 'w-full bg-gray-900 border border-white/10 rounded-lg px-3 py-2 text-sm'

export default function LabPage() {
  const [catalog, setCatalog] = useState(null)
  const [datasets, setDatasets] = useState([])
  const [runs, setRuns] = useState([])
  const [datasetId, setDatasetId] = useState('')
  const [parameters, setParameters] = useState({})
  const [pipeline, setPipeline] = useState({})
  const [variants, setVariants] = useState(['baseline', 'acoustic', 'acoustic_laya'])
  const [detail, setDetail] = useState(null)
  const [caseName, setCaseName] = useState('acoustic')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [notes, setNotes] = useState('')
  const [suiteCatalog, setSuiteCatalog] = useState(null)
  const [suiteId, setSuiteId] = useState('public-it-en')
  const [selection, setSelection] = useState('small')
  const [resourceProfile, setResourceProfile] = useState('light')
  const [mode, setMode] = useState('frozen')
  const [budgetMinutes, setBudgetMinutes] = useState(30)
  const [repetitions, setRepetitions] = useState(1)
  const [split, setSplit] = useState('')
  const [intervalStart, setIntervalStart] = useState('')
  const [intervalEnd, setIntervalEnd] = useState('')
  const [preview, setPreview] = useState(null)
  const [audioInterval, setAudioInterval] = useState(null)
  const [reference, setReference] = useState(null)
  const [activeRunId, setActiveRunId] = useState('')
  const selectionBody = () => ({ ...(suiteId !== 'single' ? { suite_id: suiteId } : { dataset_id: datasetId }), selection, seed: 42, ...(split ? { split } : {}), ...(suiteId === 'single' && intervalEnd ? { interval: [Number(intervalStart || 0), Number(intervalEnd)] } : {}) })
  useEffect(() => {
    let alive = true
    if (suiteId === 'single' && !datasetId) return
    reviewRequest('/lab/selection', 'POST', selectionBody()).then(data => { if (alive) setPreview(data) }).catch(e => { if (alive) setPreview({ error: e.message }) })
    return () => { alive = false }
  }, [suiteId, datasetId, selection, split, intervalStart, intervalEnd, suiteCatalog?.suites.length])
  useEffect(() => {
    let alive = true
    if (datasetId) reviewRequest(`/lab/datasets/${datasetId}/reference`).then(data => { if (alive) setReference(data) }).catch(() => {})
    return () => { alive = false }
  }, [datasetId])
  async function openCase(run, row) {
    await act(async () => { setDetail(await reviewRequest(`/lab/runs/${run.id}`)); setCaseName(row.case_key || row.variant); setAudioInterval(null) })
  }

  async function refresh() {
    const [ds, rs, suites] = await Promise.all([reviewRequest('/lab/datasets'), reviewRequest('/lab/runs'), reviewRequest('/lab/suites')])
    setSuiteCatalog(suites)
    setDatasets(ds); setRuns(rs)
    setError(current => /Failed to fetch|NetworkError/.test(current) ? '' : current)
    setDatasetId(current => current || ds[0]?.id || '')
  }
  useEffect(() => {
    let alive = true
    reviewRequest('/lab').then(data => {
      if (alive) { setCatalog(data); setParameters(data.parameters); setPipeline(data.pipeline) }
    }).catch(e => { if (alive) setError(e.message) })
    refresh().catch(e => { if (alive) setError(e.message) })
    const timer = setInterval(() => refresh().catch(e => { if (alive) setError(e.message) }), 3000)
    return () => { alive = false; clearInterval(timer) }
  }, [])
  async function act(work) {
    setBusy(true); setError('')
    try { await work(); await refresh() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  async function upload(event) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    for (const key of ['gold', 'diarization']) if (!form.get(key)?.size) form.delete(key)
    form.set('verified', form.get('verified') === 'on' ? 'true' : 'false')
    await act(async () => {
      const response = await fetch('/api/lab/datasets', { method: 'POST', body: form })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Import fallito')
      setDatasetId(data.id); setSuiteId('single'); setSelection('complete')
    })
  }
  async function start() {
    await act(async () => {
      const allowedPipeline = Object.fromEntries(['model', 'language', 'expected_speakers', 'diarization_precision', 'performance_profile', 'diarization_device', 'transcription_backend'].filter(k => pipeline[k] !== undefined).map(k => [k, pipeline[k]]))
      const run = await reviewRequest('/lab/runs', 'POST', { ...selectionBody(), variants, parameters, pipeline: allowedPipeline, notes, mode, budget_seconds: budgetMinutes * 60, resource_profile: resourceProfile, repetitions: mode === 'frozen' ? 1 : repetitions })
      setActiveRunId(run.id)
    })
  }
  const selected = detail?.details?.[caseName]
  const visibleRuns = runs.filter(run => suiteId !== 'single' ? run.suite?.id === suiteId : (run.datasets || [run.dataset]).some(d => d.id === datasetId))
  const activeRun = visibleRuns.find(r => r.id === activeRunId) || visibleRuns[0]
  const rows = (activeRun ? [activeRun] : []).flatMap(run => run.results.map(row => ({ ...row, run })))
  const chartRun = detail || activeRun
  return <div className="space-y-6">
    <div><h1 className="text-2xl font-semibold">Benchmark / Lab</h1><p className="text-gray-400 mt-2">Esperimenti locali separati. Baseline congelata e confronti da audio. Suite locali IT/EN, budget regolabili e gold esplicito.</p></div>
    {error && <p role="alert" className="text-red-300 bg-red-950/30 rounded-lg p-3">{error}</p>}
    <div className="bg-white/5 p-5 rounded-xl space-y-3"><h2 className="font-semibold">Catalogo e accesso</h2>
      <p className="text-sm">Disco: {((suiteCatalog?.used_bytes || 0) / 1e6).toFixed(1)} MB / 2000 MB · preparazione {suiteCatalog?.preparation.status}</p>
      {suiteCatalog?.sources.map(source => <p key={source.id} className="text-sm"><a className="text-brand-400" href={source.url} target="_blank" rel="noreferrer">{source.id.toUpperCase()}</a> · {source.language} · {source.status} · {source.license}</p>)}
      {suiteCatalog?.preparation.error && <p className="text-amber-300">{suiteCatalog.preparation.error}</p>}
      <button className="btn-primary" disabled={busy || suiteCatalog?.preparation.status === 'running'} onClick={() => act(() => reviewRequest('/lab/prepare', 'POST'))}>Prepara / riprendi dataset pubblici</button>
      <p className="text-xs text-gray-400">TIGR: sessione autorizzata richiesta. Import ELAN disponibile via CLI con tier trascrizione espliciti. Italiano VoxForge: parlato letto, metriche speaker assenti.</p>
    </div>
    <div className="grid md:grid-cols-2 gap-6">
      <form onSubmit={upload} className="space-y-3 bg-white/5 p-5 rounded-xl">
        <h2 className="font-semibold">Test set riutilizzabile</h2>
        <label className="block text-sm">Audio<input className={inputClass} name="audio" type="file" accept="audio/*,video/*" required /></label>
        <label className="block text-sm">Gold trascrizione (JSON / TXT)<input className={inputClass} name="gold" type="file" accept=".json,.txt" /></label>
        <label className="block text-sm">Gold diarizzazione (JSON / RTTM)<input className={inputClass} name="diarization" type="file" accept=".json,.rttm" /></label>
        <label className="block text-sm">Nome<input className={inputClass} name="title" maxLength={200} /></label>
        <label className="block text-sm">Scenario<select className={inputClass} name="scenario">{catalog?.scenarios.map(s => <option key={s}>{s}</option>)}</select></label>
        <label className="flex gap-2 text-sm"><input name="verified" type="checkbox" />Gold verificato manualmente sull’audio</label>
        <p className="text-xs text-gray-400">Senza gold verificato: solo performance. JSON con testo, turni e parole speaker per tutte le metriche.</p>
        <button className="btn-primary" disabled={busy}>Importa test set</button>
      </form>
      <div className="space-y-3 bg-white/5 p-5 rounded-xl">
        <h2 className="font-semibold">Configurazioni</h2>
        <label className="block text-sm">Suite<select className={inputClass} value={suiteId} onChange={e => setSuiteId(e.target.value)}><option value="single">Singolo audio</option>{suiteCatalog?.suites.map(s => <option key={s.id} value={s.id}>{s.title} · {(s.duration / 60).toFixed(1)} min</option>)}</select></label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">Selezione<select className={inputClass} value={selection} onChange={e => setSelection(e.target.value)}><option value="small">Piccola ≈ 2 min</option><option value="medium">Media ≈ 10 min</option><option value="complete">Completa</option></select></label>
          <label className="text-sm">Partizione<select className={inputClass} value={split} onChange={e => setSplit(e.target.value)}><option value="">Entrambe, analisi separate</option><option value="evaluation">Valutazione</option><option value="calibration">Calibrazione</option></select></label>
          <label className="text-sm">Risorse<select className={inputClass} value={resourceProfile} onChange={e => setResourceProfile(e.target.value)}><option value="light">Leggero · 2 thread / 8 GiB</option><option value="balanced">Bilanciato · 4 thread / 10 GiB</option><option value="heavy">Spinto · fino 8 thread / 10 GiB</option></select></label>
          <label className="text-sm">Limite totale (min)<input className={inputClass} type="number" min="1" max="360" value={budgetMinutes} onChange={e => setBudgetMinutes(Number(e.target.value))} /></label>
          <label className="text-sm">Confronto<select className={inputClass} value={mode} onChange={e => { setMode(e.target.value); if (e.target.value === 'complete') setVariants(['baseline', 'acoustic_laya']) }}><option value="frozen">Congelato: ASR/Pyannote una volta</option><option value="complete">Completo: riparti dall’audio</option></select></label>
          {mode === 'complete' && <label className="text-sm">Ripetizioni abbinate<input className={inputClass} type="number" min="1" max="3" value={repetitions} onChange={e => setRepetitions(Number(e.target.value))} /></label>}
        </div>
        <p className="text-xs text-gray-400">{preview?.error || (preview ? `${preview.datasets.length} clip · ${(preview.duration / 60).toFixed(2)} min audio · stima iniziale ${(preview.estimated_seconds / 60).toFixed(1)} min (indicativa; cache e modelli influiscono)` : 'Calcolo selezione…')}</p>

        <label className="block text-sm">{suiteId === 'single' ? 'Dataset' : 'Audio catalogo (ascolto)'}<select className={inputClass} value={datasetId} onChange={e => setDatasetId(e.target.value)}><option value="">Scegli dataset</option>{datasets.map(ds => <option key={ds.id} value={ds.id}>{ds.title} · {(ds.duration / 60).toFixed(1)} min · {ds.verified ? 'gold' : 'non verificato'}</option>)}</select></label>
        {datasetId && <><AudioWindow datasetId={datasetId} /><p className="text-xs text-gray-400">{datasets.find(d => d.id === datasetId)?.scenario} · {JSON.stringify(datasets.find(d => d.id === datasetId)?.gold)} · {datasets.find(d => d.id === datasetId)?.split || 'user'} · {datasets.find(d => d.id === datasetId)?.channel || 'microfono non documentato'} · overlap gold {((datasets.find(d => d.id === datasetId)?.gold_overlap_ratio || 0) * 100).toFixed(1)}%</p></>}
        {suiteId === 'single' && <div className="grid grid-cols-2 gap-3"><label className="text-sm">Inizio (s)<input className={inputClass} type="number" min="0" value={intervalStart} onChange={e => setIntervalStart(e.target.value)} /></label><label className="text-sm">Fine (s, opzionale)<input className={inputClass} type="number" min="0" value={intervalEnd} onChange={e => setIntervalEnd(e.target.value)} /></label></div>}
        {catalog && Object.entries(catalog.variants).map(([key, label]) => <label className="flex gap-2 text-sm" key={key}><input type="checkbox" checked={variants.includes(key)} disabled={key === 'baseline'} onChange={e => setVariants(current => e.target.checked ? (mode === 'complete' ? ['baseline', key] : [...current, key]) : current.filter(v => v !== key))} />{label}</label>)}
        {catalog && Object.entries(catalog.models).map(([key, model]) => <p key={key} className={`text-xs ${model.available ? 'text-green-300' : 'text-amber-300'}`}>{key}: {model.reason}</p>)}
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">Lingua<input className={inputClass} placeholder="auto / it / en" value={pipeline.language || ''} onChange={e => setPipeline(p => ({ ...p, language: e.target.value || null }))} /></label>
          <label className="text-sm">Speaker attesi<input className={inputClass} type="number" min="1" max="20" placeholder="auto" value={pipeline.expected_speakers ?? ''} onChange={e => setPipeline(p => ({ ...p, expected_speakers: e.target.value ? Number(e.target.value) : null }))} /></label>
          <label className="text-sm">Attribuzione<select className={inputClass} value={pipeline.diarization_precision || 'segments'} onChange={e => setPipeline(p => ({ ...p, diarization_precision: e.target.value }))}><option value="segments">Per segmento</option><option value="words">Per parola</option></select></label>
          <label className="text-sm">Profilo ASR<select className={inputClass} value={pipeline.performance_profile || 'balanced'} onChange={e => setPipeline(p => ({ ...p, performance_profile: e.target.value }))}>{['fast', 'balanced', 'quality'].map(p => <option key={p}>{p}</option>)}</select></label>
        </div>
        <p className="text-xs text-gray-400">ASR: {pipeline.model} · {pipeline.transcription_backend} · Pyannote {pipeline.diarization_device}. Impostazioni congelate per ogni confronto.</p>
        <label className="block text-sm">Note esperimento<input className={inputClass} maxLength={2000} value={notes} onChange={e => setNotes(e.target.value)} /></label>
        <button className="btn-primary" disabled={busy || (!datasetId && suiteId === 'single') || !!preview?.error} onClick={start}>Esegui confronto</button>
      </div>
    </div>
    <details className="bg-white/5 rounded-xl p-5"><summary className="cursor-pointer">Parametri refinement</summary><div className="grid sm:grid-cols-4 gap-3 mt-4">{Object.entries(parameters).map(([key, value]) => <label className="text-sm" key={key}>{labels[key]}<input className={inputClass} type="number" step={key === 'context_turns' ? 1 : .05} min={['min_audio', 'audio_window'].includes(key) ? 1.5 : 0} max={['context_turns', 'audio_window', 'min_audio'].includes(key) ? 5 : 1} value={value} onChange={e => setParameters(p => ({ ...p, [key]: Number(e.target.value) }))} /></label>)}</div></details>
    <div className="space-y-3"><h2 className="font-semibold">Confronto persistente</h2>
      <label className="block text-sm">Esecuzione<select className={inputClass} value={activeRun?.id || ''} onChange={e => { setActiveRunId(e.target.value); setDetail(null) }}><option value="">Scegli esecuzione</option>{visibleRuns.map(run => <option key={run.id} value={run.id}>{new Date(run.created_at).toLocaleString()} · {run.mode || 'complete'} · {run.status} · {run.id.slice(0, 8)}</option>)}</select></label>
      {visibleRuns.filter(r => ['queued', 'running', 'partial', 'error', 'interrupted', 'canceled'].includes(r.status)).map(run => <div key={run.id} className="text-sm flex gap-3"><details className="min-w-0"><summary>{new Date(run.created_at).toLocaleString()} · {run.status}</summary><p className="text-red-300 whitespace-pre-wrap break-words">{run.error}</p></details>{['queued', 'running'].includes(run.status) && <button className="text-amber-300" onClick={() => act(() => reviewRequest(`/lab/runs/${run.id}/cancel`, 'POST'))}>Annulla</button>}</div>)}
      <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="text-left text-gray-400">{['Configurazione / data', 'Stato', 'DER prodotto', 'WER', 'WDER parole corrette', 'Δ DER', 'Tempo', 'RTF', 'Peak RSS', 'Modifiche'].map(h => <th className="p-2" key={h}>{h}</th>)}</tr></thead><tbody>{rows.map(({ run, ...row }) => <tr key={`${run.id}-${row.case_key || row.variant}`} className="border-t border-white/10"><td className="p-2 min-w-[240px]"><button className="text-brand-400 text-left" onClick={() => openCase(run, row)}>{row.label} · {row.language} · {row.scenario} · {row.split}<span className="block text-xs text-gray-400">{new Date(run.created_at).toLocaleString()} · {run.version.commit?.slice(0, 7)}{run.version.dirty ? ' + modifiche locali' : ''}</span></button></td><td className={`p-2 ${row.status !== 'done' ? 'text-amber-300' : ''}`}>{row.status}</td><td className="p-2">{percentage(row.metrics.der)}</td><td className="p-2">{percentage(row.metrics.wer)}</td><td className="p-2">{percentage(row.metrics.wder)}</td><td className="p-2">{row.variant === 'baseline' ? '—' : percentage(row.delta.der)}</td><td className="p-2">{row.performance.total_seconds.toFixed(1)} s</td><td className="p-2">{decimal(row.performance.rtf)}</td><td className="p-2">{(row.performance.peak_ram_bytes / 1024 ** 3).toFixed(2)} GiB</td><td className="p-2">{row.changes} / {row.analyzed}</td></tr>)}</tbody></table></div>
      {!rows.length && <p className="text-gray-500 text-sm">Nessun benchmark completato per dataset selezionato.</p>}
      <p className="text-xs text-gray-500">DER con collar 250 ms e overlap incluso. Δ negativo = miglioramento rispetto baseline dello stesso run. RSS non misura tutta memoria unificata/GPU; metodo riportato nei dettagli. Stato degraded = fallback, confronto incompleto.</p>
    </div>
    <LabCharts run={chartRun} onCase={row => openCase(chartRun, row)} />
    {detail && <div className="space-y-4 bg-white/5 rounded-xl p-5"><div className="flex justify-between"><h2 className="font-semibold">Analisi · {detail.datasets?.find(d => d.id === selected?.dataset_id)?.title || detail.dataset.title}</h2><button onClick={() => setDetail(null)}>Chiudi</button></div>
      <select className={inputClass} value={caseName} onChange={e => setCaseName(e.target.value)}>{Object.keys(detail.details).map(key => <option key={key} value={key}>{detail.results.find(r => (r.case_key || r.variant) === key)?.scenario} · {catalog?.variants[detail.results.find(r => (r.case_key || r.variant) === key)?.variant] || key}</option>)}</select>
      <p className="text-xs text-gray-400">{detail.mode || 'complete'} · budget {detail.budget_seconds ? (detail.budget_seconds / 60) + ' min' : 'precedente'} · preparazione condivisa {(detail.preparations || []).reduce((n, p) => n + p.seconds, 0).toFixed(1)} s · cache {(detail.preparations || []).filter(p => p.reused).length} clip</p>
      <div className="flex gap-4 text-sm text-brand-400"><a href={`/api/lab/runs/${detail.id}/export?format=json`} download>Esporta JSON</a><a href={`/api/lab/runs/${detail.id}/export?format=csv`} download>Esporta CSV</a></div>
      {selected && <><AudioWindow datasetId={selected.dataset_id || detail.dataset.id} interval={audioInterval} />
        <details><summary className="text-sm cursor-pointer">Reference e trascrizioni</summary><p className="text-sm mt-2">Protocollo: {selected.reference?.turn_reference || selected.reference?.timing_provenance || 'reference utente'} · Gold: {selected.reference?.text || reference?.text || '—'}</p><p className="text-sm mt-2">Baseline: {selected.baseline_segments.map(s => `${s.speaker || 'UNKNOWN'}: ${s.text}`).join(' ')}</p><p className="text-sm mt-2">Risultato: {selected.segments.map(s => `${s.speaker || 'UNKNOWN'}: ${s.text}`).join(' ')}</p></details>
        <div className="text-sm text-gray-300">DER turni Pyannote: {percentage(selected.metrics.pyannote_der)} · Confusione: {percentage(selected.metrics.speaker_confusion)} · Missed: {percentage(selected.metrics.missed_speech)} · False alarm: {percentage(selected.metrics.false_alarm)} · Accuratezza speaker al tempo gold: {percentage(selected.metrics.word_speaker_accuracy)} · Refiner: {selected.performance.refiner_seconds.toFixed(2)} s · RAM: {selected.performance.ram_method}</div>
        {[...selected.warnings, ...selected.metrics.warnings].map((warning, i) => <p key={i} className="text-amber-300 text-sm">{warning}</p>)}
        <h3 className="font-medium">Divergenze rispetto assegnazione originale</h3>
        {selected.divergences.map(row => <div key={row.segment_id} className="border border-white/10 rounded-lg p-3 text-sm"><button className="text-brand-400" onClick={() => setAudioInterval([row.start, row.end])}>Ascolta {row.start.toFixed(1)}–{row.end.toFixed(1)} s · {outcomeLabels[row.outcome]}</button><p className="text-gray-400">Reference: {row.reference || '—'} · Baseline: {row.baseline || 'UNKNOWN'} · Refined: {row.refined || 'UNKNOWN'}</p><p>{row.text}</p></div>)}
        <h3 className="font-medium">Decisioni ({selected.decisions.length})</h3>
        {selected.decisions.map((decision, i) => <details key={i} className="border border-white/10 rounded-lg p-3 text-sm"><summary>{decision.start?.toFixed(1)}–{decision.end?.toFixed(1)} s · {decision.original || 'UNKNOWN'} → {decision.final || 'UNKNOWN'} · {decision.refiner} · {decision.changed ? 'modificato' : 'invariato'} · {decision.policy}</summary><button className="text-brand-400" onClick={() => setAudioInterval([decision.start, decision.end])}>Ascolta intervallo</button><p className="mt-2">{decision.text}</p><pre className="mt-2 whitespace-pre-wrap break-all text-xs text-gray-400">{JSON.stringify(decision, null, 2)}</pre></details>)}
        {!selected.decisions.length && <p className="text-sm text-gray-400">Baseline o nessun caso ambiguo.</p>}
        <details><summary className="text-sm cursor-pointer">Configurazione riproducibile</summary><pre className="text-xs whitespace-pre-wrap break-all">{JSON.stringify({ pipeline: detail.pipeline, parameters: detail.parameters, version: detail.version, dataset: detail.dataset, models: detail.models, semantic_model_info: selected.semantic_model_info, notes: detail.notes }, null, 2)}</pre></details>
      </>}
    </div>}
  </div>
}
