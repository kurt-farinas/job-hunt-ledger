import type { GmailCheckResult, GmailStatus, GmailSuggestion, Health, Job, JobFilters, JobsResponse, JobStatus, ManualJobCreate, RefreshRun, SavedView } from '../types'

const configuredBase = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
export const API_BASE_URL = configuredBase.replace(/\/$/, '')

export class ApiError extends Error {
  constructor(message: string, public status: number, public code = 'request_failed') { super(message) }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!response.ok) {
    let error = { code: 'request_failed', message: `Request failed (${response.status}).` }
    try { error = (await response.json()).error || error } catch { /* use safe fallback */ }
    throw new ApiError(error.message, response.status, error.code)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function queryString(filters: Partial<JobFilters>, includePage = true): string {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== '' && (includePage || !['page', 'page_size'].includes(key))) {
      params.set(key, String(value))
    }
  })
  const encoded = params.toString()
  return encoded ? `?${encoded}` : ''
}

export interface DashboardApi {
  health(): Promise<Health>
  jobs(filters: JobFilters): Promise<JobsResponse>
  job(id: number): Promise<Job>
  createManualJob(payload: ManualJobCreate): Promise<Job>
  updateJob(id: number, patch: { status?: JobStatus; notes?: string }): Promise<Job>
  startRefresh(): Promise<{ run_id: number; state: string; status_url: string }>
  refresh(runId: number): Promise<RefreshRun>
  syncRuns(): Promise<{ items: RefreshRun[] }>
  savedViews(): Promise<{ items: SavedView[] }>
  createSavedView(payload: Omit<SavedView, 'id' | 'created_at' | 'updated_at'>): Promise<SavedView>
  updateSavedView(id: number, patch: Partial<Pick<SavedView, 'name' | 'filters' | 'sort_by' | 'sort_order'>>): Promise<SavedView>
  deleteSavedView(id: number): Promise<void>
  backup(): Promise<{ filename: string; path: string; created_at: string; message: string }>
  exportJobs(filters: JobFilters): void
  gmailStatus(): Promise<GmailStatus>
  gmailSuggestions(): Promise<{ items: GmailSuggestion[] }>
  checkGmail(): Promise<GmailCheckResult>
  confirmGmail(id: number): Promise<GmailSuggestion>
  dismissGmail(id: number): Promise<GmailSuggestion>
  connectGmail(): void
}

export const api: DashboardApi = {
  health: () => request('/api/health'),
  jobs: (filters) => request(`/api/jobs${queryString(filters)}`),
  job: (id) => request(`/api/jobs/${id}`),
  createManualJob: (payload) => request('/api/jobs/manual', { method: 'POST', body: JSON.stringify(payload) }),
  updateJob: (id, patch) => request(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  startRefresh: () => request('/api/refresh', { method: 'POST' }),
  refresh: (runId) => request(`/api/refresh/${runId}`),
  syncRuns: () => request('/api/sync-runs?limit=20'),
  savedViews: () => request('/api/saved-views'),
  createSavedView: (payload) => request('/api/saved-views', { method: 'POST', body: JSON.stringify(payload) }),
  updateSavedView: (id, patch) => request(`/api/saved-views/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteSavedView: (id) => request(`/api/saved-views/${id}`, { method: 'DELETE' }),
  backup: () => request('/api/backup', { method: 'POST' }),
  exportJobs: (filters) => {
    const anchor = document.createElement('a')
    anchor.href = `${API_BASE_URL}/api/export/jobs.csv${queryString(filters, false)}`
    anchor.download = ''
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  },
  gmailStatus: () => request('/api/gmail/status'),
  gmailSuggestions: () => request('/api/gmail/suggestions'),
  checkGmail: () => request('/api/gmail/check', { method: 'POST' }),
  confirmGmail: (id) => request(`/api/gmail/suggestions/${id}/confirm`, { method: 'POST' }),
  dismissGmail: (id) => request(`/api/gmail/suggestions/${id}/dismiss`, { method: 'POST' }),
  connectGmail: () => { window.location.assign(`${API_BASE_URL}/api/gmail/auth/start`) },
}
