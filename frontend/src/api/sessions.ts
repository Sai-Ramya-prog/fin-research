/**
 * src/api/sessions.ts
 * Typed wrappers for the Sessions + Documents endpoints.
 */

import { api } from './client'

// ── Types ────────────────────────────────────────────────────────────────────

export type DocumentStatus =
  | 'uploaded'
  | 'parsing'
  | 'tagging'
  | 'chunking'
  | 'indexing'
  | 'ready'
  | 'failed'

export interface Document {
  id: string
  filename: string
  company: string
  fiscal_year: number
  page_count: number | null
  status: DocumentStatus
  error_message: string | null
  created_at: string
}

export interface Session {
  id: string
  name: string
  created_at: string
  documents: Document[]
}

export interface SessionSummary {
  id: string
  name: string
  created_at: string
  document_count: number
}

// ── Sessions API ─────────────────────────────────────────────────────────────

export const listSessions = () => api.get<SessionSummary[]>('/sessions')

export const createSession = (name: string) =>
  api.post<Session>('/sessions', { name })

export const getSession = (id: string) => api.get<Session>(`/sessions/${id}`)

export const deleteSession = (id: string) => api.delete(`/sessions/${id}`)

// ── Documents API ─────────────────────────────────────────────────────────────

export const getDocument = (id: string) => api.get<Document>(`/documents/${id}`)

export const deleteDocument = (id: string) => api.delete(`/documents/${id}`)

/**
 * Upload a PDF. Returns the created Document.
 * We bypass the JSON client and use FormData directly.
 */
export async function uploadDocument(
  sessionId: string,
  file: File,
  company: string,
  fiscalYear: number,
  onProgress?: (pct: number) => void,
): Promise<Document> {
  const BASE = (import.meta.env.VITE_API_URL as string) || 'http://localhost:8000'
  const url = `${BASE}/sessions/${sessionId}/documents`

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as Document)
      } else {
        let msg = xhr.statusText
        try {
          msg = JSON.parse(xhr.responseText)?.detail ?? msg
        } catch {
          // ignore
        }
        reject(new Error(msg))
      }
    }

    xhr.onerror = () => reject(new Error('Network error'))

    const fd = new FormData()
    fd.append('file', file)
    fd.append('company', company)
    fd.append('fiscal_year', String(fiscalYear))
    xhr.send(fd)
  })
}
