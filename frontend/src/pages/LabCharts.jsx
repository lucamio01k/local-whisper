import { useState } from 'react'

const colors = { baseline: '#94a3b8', acoustic: '#38bdf8', acoustic_laya: '#c084fc', acoustic_gliner: '#fbbf24' }
const fmt = (value, metric) => value == null ? '—' : ['wer', 'der', 'wder'].includes(metric) ? `${(value * 100).toFixed(2)}%` : value.toFixed(2)

export default function LabCharts({ run, onCase }) {
  const [quality, setQuality] = useState('wder')
  const [cost, setCost] = useState('total_seconds')
  const [split, setSplit] = useState('evaluation')
  const [kind, setKind] = useState('original')
  if (!run?.results?.length) return null
  const rows = run.results.filter(r => (!r.split || r.split === split) && (kind === 'all' || r.original === (kind === 'original')))
  const groups = (run.aggregates || []).filter(r => r.split === split && (kind === 'all' || r.original === (kind === 'original')))
  const qualityRows = groups.filter(r => r[quality] != null)
  const maximum = Math.max(.01, ...qualityRows.map(r => r[quality]))
  const costValue = row => cost === 'peak_ram_bytes' ? row.performance[cost] / 1024 ** 3 : row.performance[cost]
  const costMax = Math.max(.01, ...rows.map(costValue))
  const points = rows.filter(r => r.metrics.wder != null)
  const maxTime = Math.max(1, ...points.map(r => r.performance.refiner_seconds))
  return <div className="space-y-4 bg-white/5 p-5 rounded-xl">
    <h2 className="font-semibold">Grafici · {run.mode || 'complete'} · {run.selection || 'complete'} · {run.status}</h2>
    <div className="flex flex-wrap gap-3 text-sm">
      <label>Partizione <select className="bg-gray-900 rounded p-2" value={split} onChange={e => setSplit(e.target.value)}>{['evaluation', 'calibration', 'user'].map(s => <option key={s}>{s}</option>)}</select></label>
      <label>Audio <select className="bg-gray-900 rounded p-2" value={kind} onChange={e => setKind(e.target.value)}><option value="original">Originali</option><option value="degraded">Degradati controllati</option><option value="all">Entrambi, gruppi separati</option></select></label>
      <span className="self-center text-gray-400">Clic su barra/punto → analisi clip</span>
    </div>
    <div className="grid lg:grid-cols-2 gap-6">
      <div><label className="text-sm">Qualità <select className="bg-gray-900 p-2 rounded" value={quality} onChange={e => setQuality(e.target.value)}>{['wer', 'der', 'wder'].map(m => <option key={m}>{m}</option>)}</select></label>
        {qualityRows.map((r, i) => {
          const baseline = groups.find(b => b.variant === 'baseline' && b.language === r.language && b.scenario === r.scenario && b.original === r.original)
          const delta = baseline?.[quality] != null ? r[quality] - baseline[quality] : null
          const row = rows.find(row => row.variant === r.variant && row.scenario === r.scenario)
          return <button key={i} className="block w-full text-left text-xs mt-3" onClick={() => row && onCase(row)}>
            <span>{r.language.toUpperCase()} · {r.scenario} · {r.variant} · {fmt(r[quality], quality)} · Δ {fmt(delta, quality)}</span>
            <span className="block h-3 rounded mt-1" style={{ width: `${Math.max(1, r[quality] / maximum * 100)}%`, background: colors[r.variant] }} />
            <span className="text-gray-500">Gold {r.coverage[quality]}/{r.clips} clip · {r.reference_words} parole · {r.aligned_correct_words} parole corrette allineate</span>
          </button>
        })}
        {!qualityRows.length && <p className="text-gray-400 text-sm mt-3">Reference adeguata assente per questa selezione.</p>}
      </div>
      <div><label className="text-sm">Costo <select className="bg-gray-900 rounded p-2" value={cost} onChange={e => setCost(e.target.value)}>{['total_seconds', 'rtf', 'peak_ram_bytes'].map(m => <option key={m}>{m}</option>)}</select></label>
        {rows.map((row, i) => <button className="block w-full text-left text-xs mt-3" key={i} onClick={() => onCase(row)}>
          {row.language} · {row.scenario} · {row.variant}: {fmt(costValue(row), cost)} {cost === 'peak_ram_bytes' ? 'GiB RSS' : cost === 'rtf' ? 'RTF' : 's'}
          <span className="block h-3 rounded mt-1" style={{ width: `${Math.max(1, costValue(row) / costMax * 100)}%`, background: colors[row.variant] }} />
        </button>)}
      </div>
      <div><h3 className="text-sm">Errori speaker / tempo refiner</h3>
        {points.length ? <svg className="w-full" viewBox="0 0 400 220" role="img" aria-label="WDER rispetto secondi aggiunti dal refiner">
          <path d="M40 10V185H390" fill="none" stroke="#64748b" /><text x="40" y="211" fill="#94a3b8" fontSize="11">0 → {maxTime.toFixed(1)} secondi refiner</text><text x="2" y="14" fill="#94a3b8" fontSize="11">100%</text><text x="5" y="185" fill="#94a3b8" fontSize="11">0%</text>
          {points.map((row, i) => <g key={i} tabIndex={0} role="button" aria-label={`${row.variant} WDER ${fmt(row.metrics.wder, 'wder')}`} onClick={() => onCase(row)} onKeyDown={e => e.key === 'Enter' && onCase(row)} style={{ cursor: 'pointer' }}>
            <circle cx={40 + row.performance.refiner_seconds / maxTime * 340} cy={185 - row.metrics.wder * 170} r="5" fill={colors[row.variant]} /><title>{row.language} {row.scenario}: {row.variant}, {fmt(row.metrics.wder, 'wder')}, {row.performance.refiner_seconds.toFixed(2)} s</title>
          </g>)}
        </svg> : <p className="text-gray-400 text-sm mt-3">WDER assente: nessun punto inventato.</p>}
      </div>
      <div><h3 className="text-sm">Esiti decisioni</h3>{rows.filter(r => r.variant !== 'baseline').map((row, i) => <button className="block text-left w-full mt-3 text-xs" key={i} onClick={() => onCase(row)}>
        {row.language} · {row.variant} · {row.scenario}
        <div className="flex h-4 mt-1">{Object.entries({ correct_correction: '#22c55e', new_error: '#ef4444', wrong_correction: '#f97316', remaining_error: '#eab308', unscored: '#64748b', abstentions: '#a78bfa' }).map(([name, color]) => {
          const value = name === 'abstentions' ? row.abstentions || 0 : row.outcomes?.[name] || 0
          return value > 0 && <span key={name} title={`${name}: ${value}`} style={{ background: color, flex: value }} />
        })}</div><span className="text-gray-400">Corrette {row.outcomes?.correct_correction ?? '—'} · nuovi errori {row.outcomes?.new_error ?? '—'} · sbagliate {row.outcomes?.wrong_correction ?? '—'} · rimasti {row.outcomes?.remaining_error ?? '—'} · astensioni {row.abstentions ?? '—'} · non valutabili {row.outcomes?.unscored ?? '—'}</span>
      </button>)}</div>
    </div>
    <p className="text-xs text-gray-500">Aggregazioni da conteggi e denominatori. Frozen: WER e DER grezzo Pyannote invariati; DER prodotto e WDER misurano attribuzione. Tempo equivalente = preparazione condivisa + replay; esecuzione reale nel budget del run; RSS esclude parte della memoria GPU. Esiti al punto medio del segmento: diagnostica distinta dalle metriche parola.</p>
  </div>
}

export function AudioWindow({ datasetId, interval }) {
  const key = `${datasetId}:${interval?.join(':')}`
  return <audio key={key} className="w-full mt-2" controls preload="metadata" src={`/api/lab/datasets/${datasetId}/audio`} onLoadedMetadata={e => { if (interval) e.currentTarget.currentTime = interval[0] }} onTimeUpdate={e => { if (interval && e.currentTarget.currentTime >= interval[1]) e.currentTarget.pause() }} />
}
