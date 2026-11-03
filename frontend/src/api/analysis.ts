/**
 * src/api/analysis.ts
 * Real extraction + red-flag results (saved by the agents in the database).
 */

import { api, BASE } from './client'

export interface DocItem {
  id: string
  filename: string
  company: string | null
  fiscal_year: number | null
  status: string
  page_count?: number | null
  extraction_status: string | null
  red_flag_status: string | null
}

export interface Metric {
  name: string
  year: number | null
  value: number | null
  unit: string | null
  kind: 'extracted' | 'derived' | 'ratio'
  status: 'verified' | 'needs_review'
  source_page: number | null
  source_text: string | null
  formula: string | null
  note: string | null
}

export interface Flag {
  type: string
  severity: 'High' | 'Medium' | 'Low'
  title: string | null
  description: string
  evidence: string | null
  source_page: number | null
}

export const listDocs = () => api.get<DocItem[]>('/documents')
export const getMetrics = (id: string) => api.get<Metric[]>(`/documents/${id}/metrics`)
export const getFlags = (id: string) => api.get<Flag[]>(`/documents/${id}/red-flags`)

// ── helpers used by several screens ────────────────────────────────────────

export const analysed = (docs: DocItem[]) => docs.filter(d => d.extraction_status === 'done')

export function label(d: DocItem): string {
  return d.company || d.filename
}

/** Latest year in a document's metrics = the report year. */
export function latestYear(rows: Metric[]): number | null {
  const ys = rows.map(r => r.year).filter((y): y is number => y !== null)
  return ys.length ? Math.max(...ys) : null
}

export function pick(rows: Metric[], name: string, year: number | null): Metric | undefined {
  return rows.find(r => r.name === name && r.year === year)
}

export function fmtAmount(value: number, unit: string | null): string {
  const locale = unit?.startsWith('₹') ? 'en-IN' : 'en-US'
  const digits = Math.abs(value) < 100 && !Number.isInteger(value) ? 2 : 0
  return value.toLocaleString(locale, { maximumFractionDigits: digits, minimumFractionDigits: digits })
}

export function pretty(name: string): string {
  const map: Record<string, string> = {
    revenue: 'Revenue', gross_profit: 'Gross profit', operating_profit: 'Operating profit',
    net_profit: 'Net profit', eps: 'Earnings per share', total_assets: 'Total assets',
    total_liabilities: 'Total liabilities', total_equity: 'Total equity', current_assets: 'Current assets',
    current_liabilities: 'Current liabilities', cash: 'Cash and equivalents', receivables: 'Receivables',
    total_debt: 'Total debt', operating_cash_flow: 'Operating cash flow', capex: 'Capital expenditure',
    free_cash_flow: 'Free cash flow',
    gross_margin_pct: 'Gross margin', operating_margin_pct: 'Operating margin', net_margin_pct: 'Net margin',
    roe_pct: 'Return on equity', debt_to_equity: 'Debt to equity', current_ratio: 'Current ratio',
    ocf_to_net_profit: 'Cash flow / net profit', revenue_growth_pct: 'Revenue growth',
    net_profit_growth_pct: 'Profit growth', receivables_growth_pct: 'Receivables growth',
    total_debt_growth_pct: 'Debt growth',
    falling_margin: 'Falling margin', rising_debt: 'Rising debt', high_leverage: 'High leverage',
    weak_liquidity: 'Weak liquidity', cash_profit_gap: 'Cash vs profit', net_loss: 'Net loss',
    revenue_decline: 'Revenue decline', receivables_outpacing_sales: 'Receivables',
    auditor_qualified_opinion: 'Auditor', auditor_adverse_opinion: 'Auditor', auditor_disclaimer: 'Auditor',
    going_concern: 'Auditor', emphasis_of_matter: 'Auditor',
  }
  return map[name] ?? name.replace(/_/g, ' ')
}


// ── document screen: page map, source page, validation ─────────────────────

export interface PageInfo {
  pdf_page: number
  printed_page: number | null
  section: string | null
  tier: number
  indexed: boolean
  is_image_only: boolean
}

export interface PageMap {
  page_count: number
  tier1: number
  embedded: number
  sections: { section: string; pages: number }[]
  pages: PageInfo[]
}

export interface Validation {
  extracted_total: number
  verified: number
  needs_review: number
  ratios_total: number
  ratios_verified: number
  statement_pages: Record<string, number[]>
  missing_statements: string[]
  review_items: { name: string; year: number | null; value: number | null; unit: string | null; source_page: number | null; note: string | null }[]
  checks: string[]
  golden_matched: number | null
  golden_total: number | null
}

export const getDocDetail = (id: string) => api.get<DocItem>(`/documents/${id}/detail`)
export const getPageMap = (id: string) => api.get<PageMap>(`/documents/${id}/pages`)
export const getValidation = (id: string) => api.get<Validation>(`/documents/${id}/validation`)

export function pageImageUrl(id: string, page: number, highlight?: string | null): string {
  const q = highlight ? `?q=${encodeURIComponent(highlight)}` : ''
  return `${BASE}/documents/${id}/pages/${page}/image${q}`
}

/** Link target that opens the document screen on a given page. */
export function sourceLink(docId: string, page: number, text?: string | null): string {
  const q = text ? `&q=${encodeURIComponent(text)}` : ''
  return `/documents/${docId}?page=${page}${q}`
}

export const SECTION_LABEL: Record<string, string> = {
  profit_loss: 'Profit and loss', balance_sheet: 'Balance sheet', cash_flow: 'Cash flow',
  equity_changes: 'Changes in equity', auditor_report: 'Auditor report', notes: 'Notes',
  mdna_narrative: 'Management discussion', other: 'Other',
}
