/**
 * src/data/sample.ts
 * ILLUSTRATIVE demo data for screens that are not built yet (metrics, compare,
 * red flags, ask, report). None of these numbers come from the real reports.
 */

export type Company = 'Infosys' | 'Tata Motors' | 'M&M' | 'Reliance'
export const COMPANIES: Company[] = ['Infosys', 'Tata Motors', 'M&M', 'Reliance']

export interface Kpi {
  key: string
  label: string
  value: string
  unit: string
  delta: number // % change vs last year
  goodWhenUp: boolean
  series: number[] // 5 years, oldest first
  source: string
}

export const KPIS: Record<Company, Kpi[]> = {
  Infosys: [
    { key: 'rev', label: 'Revenue', value: '1,62,990', unit: '₹ crore', delta: 6.1, goodWhenUp: true, series: [100, 118, 135, 153, 163], source: 'p.304 · Consolidated P&L' },
    { key: 'pat', label: 'Profit after tax', value: '26,750', unit: '₹ crore', delta: 7.9, goodWhenUp: true, series: [19, 22, 24, 25, 27], source: 'p.304 · Consolidated P&L' },
    { key: 'ocf', label: 'Operating cash flow', value: '27,000', unit: '₹ crore', delta: 11.2, goodWhenUp: true, series: [21, 23, 22, 24, 27], source: 'p.311 · Consolidated cash flow' },
    { key: 'cash', label: 'Cash & equivalents', value: '24,455', unit: '₹ crore', delta: 65.4, goodWhenUp: true, series: [11, 12, 13, 15, 24], source: 'p.302 · Consolidated balance sheet' },
    { key: 'assets', label: 'Total assets', value: '1,48,903', unit: '₹ crore', delta: 8.0, goodWhenUp: true, series: [95, 110, 125, 138, 149], source: 'p.303 · Consolidated balance sheet' },
    { key: 'eps', label: 'Earnings per share', value: '64.5', unit: '₹', delta: 8.4, goodWhenUp: true, series: [46, 52, 57, 59, 65], source: 'p.305 · Consolidated P&L' },
  ],
  'Tata Motors': [
    { key: 'rev', label: 'Revenue', value: '4,39,695', unit: '₹ crore', delta: 0.4, goodWhenUp: true, series: [250, 280, 340, 437, 440], source: 'p.309 · Consolidated P&L' },
    { key: 'pat', label: 'Profit after tax', value: '31,806', unit: '₹ crore', delta: -1.2, goodWhenUp: true, series: [-14, -3, 3, 32, 32], source: 'p.309 · Consolidated P&L' },
    { key: 'ocf', label: 'Operating cash flow', value: '49,300', unit: '₹ crore', delta: 6.5, goodWhenUp: true, series: [30, 33, 36, 46, 49], source: 'Consolidated cash flow' },
    { key: 'cash', label: 'Cash & equivalents', value: '35,900', unit: '₹ crore', delta: 9.0, goodWhenUp: true, series: [20, 24, 29, 33, 36], source: 'p.308 · Consolidated balance sheet' },
    { key: 'assets', label: 'Total assets', value: '4,62,000', unit: '₹ crore', delta: 5.1, goodWhenUp: true, series: [330, 350, 390, 440, 462], source: 'p.308 · Consolidated balance sheet' },
    { key: 'eps', label: 'Earnings per share', value: '86.4', unit: '₹', delta: -1.5, goodWhenUp: true, series: [-38, -8, 8, 88, 86], source: 'p.309 · Consolidated P&L' },
  ],
  'M&M': [
    { key: 'rev', label: 'Revenue', value: '1,38,000', unit: '₹ crore', delta: 15.2, goodWhenUp: true, series: [75, 90, 107, 120, 138], source: 'Consolidated P&L' },
    { key: 'pat', label: 'Profit after tax', value: '12,900', unit: '₹ crore', delta: 22.0, goodWhenUp: true, series: [5, 7, 9, 11, 13], source: 'Consolidated P&L' },
    { key: 'ocf', label: 'Operating cash flow', value: '15,200', unit: '₹ crore', delta: 12.1, goodWhenUp: true, series: [8, 10, 12, 14, 15], source: 'Cash flow statement' },
    { key: 'cash', label: 'Cash & equivalents', value: '7,400', unit: '₹ crore', delta: 14.0, goodWhenUp: true, series: [3, 4, 5, 6, 7], source: 'Consolidated balance sheet' },
    { key: 'assets', label: 'Total assets', value: '2,40,000', unit: '₹ crore', delta: 13.0, goodWhenUp: true, series: [150, 170, 195, 212, 240], source: 'Consolidated balance sheet' },
    { key: 'eps', label: 'Earnings per share', value: '108.0', unit: '₹', delta: 20.0, goodWhenUp: true, series: [42, 58, 75, 90, 108], source: 'Consolidated P&L' },
  ],
  Reliance: [
    { key: 'rev', label: 'Revenue', value: '9,30,000', unit: '₹ crore', delta: 6.0, goodWhenUp: true, series: [540, 700, 880, 880, 930], source: 'p.99 · Consolidated P&L' },
    { key: 'pat', label: 'Profit after tax', value: '81,300', unit: '₹ crore', delta: 3.5, goodWhenUp: true, series: [49, 67, 74, 79, 81], source: 'p.99 · Consolidated P&L' },
    { key: 'ocf', label: 'Operating cash flow', value: '1,57,000', unit: '₹ crore', delta: 10.0, goodWhenUp: true, series: [90, 110, 120, 142, 157], source: 'Consolidated cash flow' },
    { key: 'cash', label: 'Cash & equivalents', value: '2,00,000', unit: '₹ crore', delta: 4.0, goodWhenUp: true, series: [130, 150, 170, 192, 200], source: 'p.99 · Consolidated balance sheet' },
    { key: 'assets', label: 'Total assets', value: '19,50,000', unit: '₹ crore', delta: 7.0, goodWhenUp: true, series: [1200, 1400, 1600, 1820, 1950], source: 'p.99 · Consolidated balance sheet' },
    { key: 'eps', label: 'Earnings per share', value: '51.0', unit: '₹', delta: 3.0, goodWhenUp: true, series: [34, 42, 46, 49, 51], source: 'p.99 · Consolidated P&L' },
  ],
}

export interface Ratio { label: string; hint: string; value: number; max: number; suffix: string }
export const RATIOS: Record<Company, Ratio[]> = {
  Infosys: [
    { label: 'Operating margin', hint: 'EBIT ÷ revenue', value: 21.1, max: 40, suffix: '%' },
    { label: 'Return on equity', hint: 'profit ÷ average equity', value: 31.9, max: 40, suffix: '%' },
    { label: 'Debt to equity', hint: 'borrowings ÷ equity', value: 0.1, max: 2, suffix: '×' },
  ],
  'Tata Motors': [
    { label: 'Operating margin', hint: 'EBIT ÷ revenue', value: 11.5, max: 40, suffix: '%' },
    { label: 'Return on equity', hint: 'profit ÷ average equity', value: 38.0, max: 40, suffix: '%' },
    { label: 'Debt to equity', hint: 'borrowings ÷ equity', value: 0.9, max: 2, suffix: '×' },
  ],
  'M&M': [
    { label: 'Operating margin', hint: 'EBIT ÷ revenue', value: 13.4, max: 40, suffix: '%' },
    { label: 'Return on equity', hint: 'profit ÷ average equity', value: 18.5, max: 40, suffix: '%' },
    { label: 'Debt to equity', hint: 'borrowings ÷ equity', value: 1.1, max: 2, suffix: '×' },
  ],
  Reliance: [
    { label: 'Operating margin', hint: 'EBIT ÷ revenue', value: 15.2, max: 40, suffix: '%' },
    { label: 'Return on equity', hint: 'profit ÷ average equity', value: 9.2, max: 40, suffix: '%' },
    { label: 'Debt to equity', hint: 'borrowings ÷ equity', value: 0.4, max: 2, suffix: '×' },
  ],
}

export interface CompareMetric { key: string; label: string; unit: string; higherIsBetter: boolean; values: Record<Company, number> }
export const COMPARE: CompareMetric[] = [
  { key: 'rev', label: 'Revenue', unit: '₹ lakh crore', higherIsBetter: true, values: { Infosys: 1.63, 'Tata Motors': 4.4, 'M&M': 1.38, Reliance: 9.3 } },
  { key: 'margin', label: 'Operating margin', unit: '%', higherIsBetter: true, values: { Infosys: 21.1, 'Tata Motors': 11.5, 'M&M': 13.4, Reliance: 15.2 } },
  { key: 'roe', label: 'Return on equity', unit: '%', higherIsBetter: true, values: { Infosys: 31.9, 'Tata Motors': 38.0, 'M&M': 18.5, Reliance: 9.2 } },
  { key: 'de', label: 'Debt to equity', unit: '×', higherIsBetter: false, values: { Infosys: 0.1, 'Tata Motors': 0.9, 'M&M': 1.1, Reliance: 0.4 } },
]

export type Severity = 'High' | 'Medium' | 'Low'
export interface Flag {
  id: number
  severity: Severity
  company: Company
  category: string
  title: string
  evidence: string
  source: string
}
export const FLAGS: Flag[] = [
  { id: 1, severity: 'High', company: 'Tata Motors', category: 'Debt', title: 'Borrowings grew faster than earnings', evidence: '“Borrowings increased while profit before tax stayed flat year on year.”', source: 'p.308 · Balance sheet' },
  { id: 2, severity: 'High', company: 'Reliance', category: 'Cash flow', title: 'Profit not matched by cash from operations', evidence: '“Net cash generated from operating activities was lower than reported profit.”', source: 'Cash flow statement' },
  { id: 3, severity: 'Medium', company: 'M&M', category: 'Receivables', title: 'Trade receivables rising ahead of revenue', evidence: '“Trade receivables increased by a larger percentage than revenue from operations.”', source: 'Notes · Receivables' },
  { id: 4, severity: 'Medium', company: 'Infosys', category: 'Contingent liabilities', title: 'Large tax disputes disclosed', evidence: '“Claims against the company not acknowledged as debt: income tax matters.”', source: 'Notes · Contingencies' },
  { id: 5, severity: 'Medium', company: 'Tata Motors', category: 'Auditor', title: 'Emphasis of matter in auditor report', evidence: '“We draw attention to the matter described in the notes to the financial statements.”', source: 'p.298 · Auditor report' },
  { id: 6, severity: 'Low', company: 'Reliance', category: 'Related parties', title: 'Significant related-party transactions', evidence: '“Transactions with joint ventures and associates are disclosed in the notes.”', source: 'Notes · Related parties' },
  { id: 7, severity: 'Low', company: 'M&M', category: 'Accounting', title: 'Change in an accounting estimate', evidence: '“Useful lives of certain assets were revised during the year.”', source: 'Notes · Accounting policies' },
]

export const CHAT = {
  question: 'Why did Infosys operating margin move this year?',
  answer:
    'Margin held close to last year. Higher employee cost and subcontractor cost were largely offset by better utilisation and a favourable currency mix, according to the management discussion.',
  sources: ['MD&A · p.84', 'Consolidated P&L · p.304', 'Notes · Employee benefits'],
}

export const REPORT_SECTIONS = [
  { name: 'Company snapshot', done: true },
  { name: 'Key metrics and ratios', done: true },
  { name: 'Peer comparison', done: true },
  { name: 'Red flags with evidence', done: false },
  { name: 'Outlook and summary', done: false },
]
