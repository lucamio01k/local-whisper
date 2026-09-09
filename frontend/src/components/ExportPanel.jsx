import { Download } from 'lucide-react'
import { exportUrl } from '../api'

const FORMATS = [
  { id: 'txt',  label: 'TXT',      desc: 'Testo semplice' },
  { id: 'srt',  label: 'SRT',      desc: 'Sottotitoli' },
  { id: 'vtt',  label: 'VTT',      desc: 'Web Video Text' },
  { id: 'md',   label: 'Markdown', desc: 'Con speaker formattati' },
  { id: 'csv',  label: 'CSV',      desc: 'Tabella dati' },
]

export default function ExportPanel({ jobId }) {
  return (
    <div className="space-y-3">
      {[
        { id: 'raw', label: 'Trascrizione pulita' },
        { id: 'speakers', label: 'Con speaker' },
      ].map(group => (
        <div key={group.id} className="space-y-1.5">
          <p className="text-[11px] text-gray-500 font-medium uppercase tracking-wider">{group.label}</p>
          <div className="flex flex-wrap gap-2">
            {FORMATS.map(({ id, label, desc }) => (
              <a
                key={`${group.id}-${id}`}
                href={exportUrl(jobId, id, group.id)}
                download
                className="btn-ghost text-xs"
                title={desc}
              >
                <Download size={13} />
                {label}
              </a>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
