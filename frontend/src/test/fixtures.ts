import { vi } from 'vitest'
import type { DashboardApi } from '../api/client'
import type { Job, JobsResponse, RefreshRun, SavedView } from '../types'

export const job: Job = {
  id: 1, dedupe_hash: 'hash', source: 'adzuna', external_job_id: 'adz-1',
  title: 'Junior React Developer', company: 'Northstar Labs', location: 'Metro Manila, Philippines',
  source_url: 'https://jobs.example.test/role/1', posted_at: '2026-09-01T02:00:00+00:00',
  date_found: '2026-09-07T00:30:00+00:00', first_seen_at: '2026-09-07T00:30:00+00:00',
  last_seen_at: '2026-09-07T00:30:00+00:00',
  match_reasons: ['Title: react developer', 'Location: Metro Manila', 'Work arrangement: Hybrid', 'Employment type: Full-time'],
  job_description: 'Build accessible React interfaces and collaborate with a small product team.',
  work_arrangement: 'Hybrid', employment_type: 'Full-time', remote_flag: false,
  salary_min: 35000, salary_max: 50000, salary_currency: 'PHP', salary_period: 'month',
  preferred_company: true, is_stale: false, stale_marked_at: null, status: 'New', notes: '',
  created_at: '2026-09-07T00:30:00+00:00', updated_at: '2026-09-07T00:30:00+00:00',
  status_history: [{ id: 1, job_id: 1, previous_status: 'New', new_status: 'Applied', change_source: 'manual_dashboard', changed_at: '2026-09-07T03:00:00+00:00' }],
}

export const data = (items: Job[] = [job]): JobsResponse => ({
  items, total: items.length, page: 1, page_size: 25, total_pages: items.length ? 1 : 0,
  summary: { New: items.filter(item => item.status === 'New').length, Applied: items.filter(item => item.status === 'Applied').length,
    'Not Interested': 0, Declined: 0, Stale: items.filter(item => item.is_stale).length },
})

export const completedRun: RefreshRun = {
  id: 10, trigger_type: 'manual', started_at: '2026-09-07T00:00:00+00:00', completed_at: '2026-09-07T00:01:00+00:00',
  status: 'completed', state: 'completed', sources_checked: ['adzuna'], new_jobs_count: 12,
  existing_jobs_count: 36, stale_jobs_count: 2, errors: [],
}

export const savedView: SavedView = {
  id: 4, name: 'New remote jobs', filters: { status: 'New', work_arrangement: 'Remote' },
  sort_by: 'date_found', sort_order: 'desc', created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
}

export function makeApi(overrides: Partial<DashboardApi> = {}): DashboardApi {
  let currentJob = { ...job }
  let views = [savedView]
  const implementation: DashboardApi = {
    health: vi.fn().mockResolvedValue({ status: 'ok', database: 'ok', timezone: 'Asia/Manila', scheduler_running: true, next_refresh_at: null, active_refresh_run_id: null }),
    jobs: vi.fn(async () => data([currentJob])),
    job: vi.fn(async () => ({ ...currentJob, status_history: job.status_history })),
    createManualJob: vi.fn(async payload => ({ ...currentJob, id: 99, ...payload, source: 'linkedin', status: 'New', location: '', work_arrangement: null, employment_type: null, match_reasons: ['Manually saved from a trusted source'] })),
    updateJob: vi.fn(async (_id, patch) => { currentJob = { ...currentJob, ...patch }; return { ...currentJob, status_history: job.status_history } }),
    startRefresh: vi.fn().mockResolvedValue({ run_id: 10, state: 'running', status_url: '/api/refresh/10' }),
    refresh: vi.fn().mockResolvedValue(completedRun),
    syncRuns: vi.fn().mockResolvedValue({ items: [completedRun] }),
    savedViews: vi.fn(async () => ({ items: views })),
    createSavedView: vi.fn(async payload => { const created = { ...payload, id: 9, created_at: 'now', updated_at: 'now' }; views = [...views, created]; return created }),
    updateSavedView: vi.fn(async (id, patch) => { const updated = { ...views.find(view => view.id === id)!, ...patch }; views = views.map(view => view.id === id ? updated : view); return updated }),
    deleteSavedView: vi.fn(async id => { views = views.filter(view => view.id !== id) }),
    backup: vi.fn().mockResolvedValue({ filename: 'backup.sqlite3', path: 'C:\\Private\\backup.sqlite3', created_at: 'now', message: 'Local backup created' }),
    exportJobs: vi.fn(),
    gmailStatus: vi.fn().mockResolvedValue({ configured: false, connected: false, checking: false, scope: 'https://www.googleapis.com/auth/gmail.readonly', last_checked_at: null, last_error: null }),
    gmailSuggestions: vi.fn().mockResolvedValue({ items: [] }),
    checkGmail: vi.fn().mockResolvedValue({ messages_found: 0, suggestions_created: 0, duplicates_skipped: 0, checked_at: 'now' }),
    confirmGmail: vi.fn(),
    dismissGmail: vi.fn(),
    connectGmail: vi.fn(),
  }
  return { ...implementation, ...overrides }
}
