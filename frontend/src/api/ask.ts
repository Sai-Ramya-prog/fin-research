/**
 * src/api/ask.ts
 * Ask and report: the companies in a session, the saved report, and the chat.
 */

import { api, BASE } from './client'

export interface YearOption {
  fiscal_year: number
  document_id: string
  filename: string
  ready: boolean
  reason: string | null
}
export interface CompanyOption { company: string; years: YearOption[] }

export interface ReportRow { cells: string[]; page: number | null; warn: boolean }
export interface ReportTable { title: string; columns: string[]; rows: ReportRow[] }
export interface ReportFlag {
  title: string
  severity: 'High' | 'Medium' | 'Low'
  description: string
  evidence: string | null
  page: number | null
}
export interface ReportSentence { text: string; page: number; quote: string | null }
export interface ReportSection {
  key: string
  title: string
  status: 'ready' | 'unavailable'
  sentences: ReportSentence[]
  tables: ReportTable[]
  flags: ReportFlag[]
  note: string | null
}

export type ReportStatus = 'pending' | 'running' | 'ready' | 'partial' | 'failed'
export interface Report {
  id: string
  company: string
  fiscal_year: number
  status: ReportStatus
  sections: ReportSection[]
  error: string | null
  progress: { done: number; total: number }
  updated_at: string
}
export interface ReportState {
  company: string
  fiscal_year: number
  readiness: { ready: boolean; reason: string | null }
  report: Report | null
}

export interface Citation { pdf_page: number | null; quote: string | null; verified: boolean }
export type ChatKind = 'question' | 'answer' | 'numeric' | 'not_found' | 'unverified' | 'other_company' | 'smalltalk' | 'off_topic'
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  kind: ChatKind
  content: string
  citations: Citation[]
  created_at: string
}

/** Section order and names, so the progress list can show sections that are not written yet. */
export const SECTION_NAMES: [string, string][] = [
  ['snapshot', 'Company snapshot'],
  ['outlook', 'Outlook and summary'],
]

const q = (company: string, year: number) =>
  `company=${encodeURIComponent(company)}&fiscal_year=${year}`

export const getCompanies = (sid: string) => api.get<CompanyOption[]>(`/sessions/${sid}/companies`)

export const getReportState = (sid: string, company: string, year: number) =>
  api.get<ReportState>(`/sessions/${sid}/report?${q(company, year)}`)

export const generateReport = (sid: string, company: string, year: number, regenerate = false) =>
  api.post<ReportState>(`/sessions/${sid}/report/generate?${q(company, year)}&regenerate=${regenerate}`)

export const reportPdfUrl = (sid: string, company: string, year: number, inline = false) =>
  `${BASE}/sessions/${sid}/report/pdf?${q(company, year)}${inline ? '&inline=true' : ''}`

export const getChat = (sid: string, company: string, year: number) =>
  api.get<ChatMessage[]>(`/sessions/${sid}/chat?${q(company, year)}`)

export const askChat = (sid: string, company: string, year: number, question: string) =>
  api.post<ChatMessage>(`/sessions/${sid}/chat`, { company, fiscal_year: year, question })

export const clearChat = (sid: string, company: string, year: number) =>
  api.delete(`/sessions/${sid}/chat?${q(company, year)}`)
