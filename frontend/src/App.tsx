/**
 * src/App.tsx
 * Root router — two routes: sessions list and session detail.
 */

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { SessionsPage } from './pages/SessionsPage'
import { SessionDetailPage } from './pages/SessionDetailPage'

export function App() {
  return (
    <BrowserRouter>
      <header style={headerStyle}>
        <span style={logoStyle}>📊 Fin-Research</span>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<SessionsPage />} />
          <Route path="/sessions/:id" element={<SessionDetailPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}

const headerStyle: React.CSSProperties = {
  padding: '12px 24px',
  borderBottom: '1px solid #e5e7eb',
  background: '#fff',
  position: 'sticky',
  top: 0,
  zIndex: 10,
}

const logoStyle: React.CSSProperties = {
  fontSize: '1rem',
  fontWeight: 700,
  color: '#111827',
  letterSpacing: '-0.01em',
}
