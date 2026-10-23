/**
 * src/App.tsx
 * Shell (navy side rail + content) and the six screens.
 * Sessions and Workspace are live; the rest show sample data for the demo.
 */

import { BrowserRouter, NavLink, Routes, Route, Navigate } from 'react-router-dom'
import { SessionsPage } from './pages/SessionsPage'
import { SessionDetailPage } from './pages/SessionDetailPage'
import { MetricsPage } from './pages/MetricsPage'
import { ComparePage } from './pages/ComparePage'
import { RedFlagsPage } from './pages/RedFlagsPage'
import { AskPage } from './pages/AskPage'
import { ThemeToggle } from './components/ThemeToggle'

const NAV = [
  { to: '/', label: 'Sessions', hint: 'Your workspaces', live: true, end: true },
  { to: '/metrics', label: 'Metrics', hint: 'Numbers with sources' },
  { to: '/compare', label: 'Compare', hint: 'Companies side by side' },
  { to: '/red-flags', label: 'Red flags', hint: 'Warnings with evidence' },
  { to: '/ask', label: 'Ask and report', hint: 'Why, how, and the write-up' },
]

export function App() {
  return (
    <BrowserRouter>
      <div className="shell">
        <aside className="rail">
          <div className="brand"><span className="brand-mark">F</span>Fin-Research</div>
          <nav className="nav" aria-label="Main">
            {NAV.map(n => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) => [isActive ? 'active' : '', n.live ? 'built' : ''].join(' ').trim()}
              >
                <span>{n.label}</span>
                <span>{n.hint}</span>
              </NavLink>
            ))}
          </nav>
          <div className="rail-foot">Milestone 1: documents are read, tagged and indexed. The other screens preview what comes next.</div>
        </aside>
        <main className="content">
          <div className="topbar"><ThemeToggle /></div>
          <Routes>
            <Route path="/" element={<SessionsPage />} />
            <Route path="/sessions/:id" element={<SessionDetailPage />} />
            <Route path="/metrics" element={<MetricsPage />} />
            <Route path="/compare" element={<ComparePage />} />
            <Route path="/red-flags" element={<RedFlagsPage />} />
            <Route path="/ask" element={<AskPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
