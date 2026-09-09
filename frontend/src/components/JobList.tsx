import { ChevronLeft, ChevronRight, ExternalLink, FileSearch, MapPin, Save } from 'lucide-react'
import type { Job, JobFilters, JobStatus, JobsResponse } from '../types'

const statuses: JobStatus[] = ['New', 'Applied', 'Not Interested', 'Declined']
const sourceLabel = (source: string) => {
  const labels: Record<string, string> = {
    linkedin: 'Trusted board · LinkedIn', jobstreet: 'Trusted board · JobStreet', indeed: 'Trusted board · Indeed',
    prosple: 'Trusted board · Prosple', glassdoor: 'Trusted board · Glassdoor', greenhouse: 'Direct company feed · Greenhouse', lever: 'Direct company feed · Lever',
    himalayas: 'Legacy aggregator · Himalayas (disabled)',
  }
  return labels[source] || source.replaceAll('_', ' ')
}

export function salary(job: Job) {
  if (job.salary_min == null && job.salary_max == null) return 'Not provided'
  const format = (value: number) => new Intl.NumberFormat('en-PH', { maximumFractionDigits: 2 }).format(value)
  const amount = job.salary_min != null && job.salary_max != null ? `${format(job.salary_min)}–${format(job.salary_max)}` : job.salary_min != null ? `From ${format(job.salary_min)}` : `Up to ${format(job.salary_max!)}`
  return `${job.salary_currency ? `${job.salary_currency} ` : ''}${amount}${job.salary_period ? ` / ${job.salary_period}` : ''}`
}

export function localDate(value: string | null) {
  if (!value) return 'Unknown'
  return new Intl.DateTimeFormat('en-PH', { timeZone: 'Asia/Manila', dateStyle: 'medium' }).format(new Date(value))
}

interface JobListProps {
  data: JobsResponse
  filters: JobFilters
  noteDrafts: Record<number, string>
  pending: Set<number>
  onFilters(next: JobFilters): void
  onOpen(id: number): void
  onStatus(job: Job, status: JobStatus): void
  onNoteDraft(id: number, notes: string): void
  onSaveNotes(job: Job): void
}

export function JobList({ data, filters, noteDrafts, pending, onFilters, onOpen, onStatus, onNoteDraft, onSaveNotes }: JobListProps) {
  if (!data.items.length) return <div className="empty-state"><FileSearch size={32} /><h2>No leads in this view</h2><p>Adjust the survey controls or run a fresh search. Existing notes and application history stay untouched.</p></div>
  return <>
    <div className="ledger" aria-label="Job leads">
      <div className="ledger__head" aria-hidden="true"><span>Role and place</span><span>Match evidence</span><span>Source record</span><span>Tracking</span></div>
      {data.items.map(job => {
        const draft = noteDrafts[job.id] ?? job.notes
        const changed = draft !== job.notes
        return <article className={`job-row${job.is_stale ? ' job-row--stale' : ''}`} key={job.id}>
          <div className="job-row__identity">
            <div className="job-row__flags">{job.preferred_company && <span className="tag tag--preferred">Preferred company</span>}{job.is_stale && <span className="tag tag--stale">Stale listing</span>}</div>
            <button className="job-title" onClick={() => onOpen(job.id)}>{job.title}</button>
            <p className="company">{job.company || 'Company not provided'}</p>
            <p className="location"><MapPin size={14} />{job.location || 'Location not provided'}</p>
            <button className="detail-link" onClick={() => onOpen(job.id)}>Open full record <ChevronRight size={15} /></button>
          </div>
          <div className="job-row__evidence">
            <div className="tag-list">{job.match_reasons.map(reason => <span className="tag" key={reason}>{reason}</span>)}</div>
            <dl className="compact-facts"><div><dt>Arrangement</dt><dd>{job.work_arrangement || 'Not provided'}</dd></div><div><dt>Employment</dt><dd>{job.employment_type || 'Not provided'}</dd></div><div><dt>Salary</dt><dd>{salary(job)}</dd></div></dl>
          </div>
          <div className="job-row__source">
            <span className="source-stamp">{sourceLabel(job.source)}</span>
            <dl className="compact-facts compact-facts--stack"><div><dt>Date found</dt><dd>{localDate(job.date_found)}</dd></div><div><dt>Posted</dt><dd>{localDate(job.posted_at)}</dd></div></dl>
            <a className="external-link" href={job.source_url} target="_blank" rel="noopener noreferrer">Original listing <ExternalLink size={15} /></a>
          </div>
          <div className="job-row__tracking">
            <label className="field"><span>Status</span><select aria-label={`Status for ${job.title}`} value={job.status} disabled={pending.has(job.id)} onChange={event => onStatus(job, event.target.value as JobStatus)}>{statuses.map(status => <option key={status}>{status}</option>)}</select></label>
            <label className="field"><span>Notes</span><textarea aria-label={`Notes for ${job.title}`} rows={3} value={draft} disabled={pending.has(job.id)} onChange={event => onNoteDraft(job.id, event.target.value)} placeholder="Add a private note…" /></label>
            <button className="button button--compact button--save" disabled={!changed || pending.has(job.id)} onClick={() => onSaveNotes(job)}><Save size={15} /> {pending.has(job.id) ? 'Saving…' : 'Save notes'}</button>
          </div>
        </article>
      })}
    </div>
    <nav className="pagination" aria-label="Job list pages">
      <button className="button button--quiet" disabled={data.page <= 1} onClick={() => onFilters({ ...filters, page: filters.page - 1 })}><ChevronLeft size={17} /> Previous</button>
      <span>Page <strong>{data.page}</strong> of <strong>{Math.max(data.total_pages, 1)}</strong></span>
      <button className="button button--quiet" disabled={data.page >= data.total_pages} onClick={() => onFilters({ ...filters, page: filters.page + 1 })}>Next <ChevronRight size={17} /></button>
    </nav>
  </>
}
