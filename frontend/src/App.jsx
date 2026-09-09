import { Routes, Route, NavLink } from 'react-router-dom'
import { Mic, Clock, Settings } from 'lucide-react'
import TranscribePage from './pages/TranscribePage'
import HistoryPage from './pages/HistoryPage'
import SettingsPage from './pages/SettingsPage'

const NAV = [
  { to: '/',         label: 'Trascrivi', Icon: Mic,      end: true },
  { to: '/history',  label: 'Archivio',  Icon: Clock,    end: false },
  { to: '/settings', label: 'Impostazioni', Icon: Settings, end: false },
]

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-white/5 bg-gray-950/80 backdrop-blur sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="text-brand-500 text-xl">🎙️</span>
            <span className="font-semibold text-white tracking-tight">Local Whisper</span>
          </div>
          <nav className="flex items-center gap-1">
            {NAV.map(({ to, label, Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  `flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors ${
                    isActive ? 'bg-white/10 text-white' : 'text-gray-400 hover:text-white hover:bg-white/5'
                  }`
                }
              >
                <Icon size={15} />
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="flex-1 max-w-6xl mx-auto w-full px-4 py-8">
        <Routes>
          <Route path="/" element={<TranscribePage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  )
}
