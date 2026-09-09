export type JobStatus = 'New' | 'Applied' | 'Not Interested' | 'Declined'
export type SortField = 'date_found' | 'posted_at' | 'source' | 'status' | 'company' | 'title'
export type SortOrder = 'asc' | 'desc'

export interface StatusHistory {
  id: number
  job_id: number
  previous_status: JobStatus
  new_status: JobStatus
  change_source: 'manual_dashboard' | 'gmail_suggestion_confirmed'
  changed_at: string
}

export interface Job {
  id: number
  dedupe_hash: string
  source: string
  external_job_id: string | null
  title: string
  company: string
  location: string
  source_url: string
  posted_at: string | null
  date_found: string
  first_seen_at: string
  last_seen_at: string
  match_reasons: string[]
  job_description: string | null
  work_arrangement: string | null
  employment_type: string | null
  remote_flag: boolean | null
  salary_min: number | null
  salary_max: number | null
  salary_currency: string | null
  salary_period: string | null
  preferred_company: boolean
  is_stale: boolean
  stale_marked_at: string | null
  status: JobStatus
  notes: string
  created_at: string
  updated_at: string
  status_history?: StatusHistory[]
}

export interface ManualJobCreate {
  source_url: string
  title: string
  company: string
}

export interface JobFilters {
  status?: JobStatus
  source?: string
  date_from?: string
  date_to?: string
  stale?: boolean
  work_arrangement?: string
  employment_type?: string
  preferred_company?: boolean
  search?: string
  sort_by: SortField
  sort_order: SortOrder
  page: number
  page_size: number
}

export interface JobSummary {
  New: number
  Applied: number
  'Not Interested': number
  Declined: number
  Stale: number
}

export interface JobsResponse {
  items: Job[]
  total: number
  page: number
  page_size: number
  total_pages: number
  summary: JobSummary
}

export type RefreshState = 'running' | 'completed' | 'partial_failure' | 'failed'
export interface RefreshError { source: string; code: string; message: string }
export interface RefreshRun {
  id: number
  trigger_type: 'scheduled' | 'manual'
  started_at: string
  completed_at: string | null
  status: RefreshState
  state: RefreshState
  sources_checked: string[]
  new_jobs_count: number
  existing_jobs_count: number
  stale_jobs_count: number
  errors: RefreshError[]
}

export interface SavedView {
  id: number
  name: string
  filters: Partial<JobFilters>
  sort_by: SortField
  sort_order: SortOrder
  created_at: string
  updated_at: string
}

export interface Health {
  status: string
  database: string
  timezone: string
  scheduler_running: boolean
  next_refresh_at: string | null
  active_refresh_run_id: number | null
}

export interface GmailStatus {
  configured: boolean
  connected: boolean
  checking: boolean
  scope: string
  last_checked_at: string | null
  last_error: string | null
}

export interface GmailSuggestion {
  id: number
  gmail_message_id: string
  sender: string
  subject: string
  received_at: string
  inferred_company: string | null
  suggested_status: 'Applied' | 'Declined' | null
  sanitized_excerpt: string
  matched_pattern: string
  job_id: number | null
  job_title: string | null
  job_company: string | null
  state: 'pending' | 'confirmed' | 'dismissed'
  created_at: string
  resolved_at: string | null
}

export interface GmailCheckResult {
  messages_found: number
  suggestions_created: number
  duplicates_skipped: number
  checked_at: string
}
