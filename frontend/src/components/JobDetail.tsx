import { Clock3, ExternalLink, Save, X } from 'lucide-react'
import { useRef } from 'react'
import type { Job, JobStatus } from '../types'
import { useDialogFocus } from '../hooks/useDialogFocus'
import { localDate, salary } from './JobList'

interface DetailProps {
  job: Job
  notes: string
  pending: boolean
  onClose(): void
  onStatus(status: JobStatus): void
  onNotes(value: string): void
  onSaveNotes(): void
}

export function JobDetail({ job, notes, pending, onClose, onStatus, onNotes, onSaveNotes }: DetailProps) {
  const dialogRef = useRef<HTMLElement>(null)
  useDialogFocus(dialogRef, onClose)
  return <div className="drawer-shell" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <aside ref={dialogRef} className="detail-drawer" role="dialog" aria-modal="true" aria-labelledby="detail-title">
      <div className="detail-drawer__top"><p className="kicker">Full source record</p><button autoFocus className="icon-button" onClick={onClose} aria-label="Close job details"><X /></button></div>
      <header className="detail-heading"><div className="job-row__flags">{job.preferred_company && <span className="tag tag--preferred">Preferred company</span>}{job.is_stale && <span className="tag tag--stale">Stale listing</span>}</div><h2 id="detail-title">{job.title}</h2><p>{job.company || 'Company not provided'} · {job.location || 'Location not provided'}</p><a className="button button--primary" href={job.source_url} target="_blank" rel="noopener noreferrer">Open original listing <ExternalLink size={16} /></a></header>
      <section className="detail-section"><h3>Why it matched</h3><div className="tag-list">{job.match_reasons.map(reason => <span className="tag" key={reason}>{reason}</span>)}</div></section>
      <section className="detail-section"><h3>Role facts</h3><dl className="detail-facts"><div><dt>Source</dt><dd>{job.source.replaceAll('_',' ')}</dd></div><div><dt>Posted</dt><dd>{localDate(job.posted_at)}</dd></div><div><dt>Date found</dt><dd>{localDate(job.date_found)}</dd></div><div><dt>First seen</dt><dd>{localDate(job.first_seen_at)}</dd></div><div><dt>Last seen</dt><dd>{localDate(job.last_seen_at)}</dd></div><div><dt>Arrangement</dt><dd>{job.work_arrangement || 'Not provided'}</dd></div><div><dt>Employment</dt><dd>{job.employment_type || 'Not provided'}</dd></div><div><dt>Salary</dt><dd>{salary(job)}</dd></div></dl></section>
      {job.job_description && <section className="detail-section"><h3>Source description</h3><p className="description">{job.job_description}</p></section>}
      <section className="detail-section detail-section--tracking"><h3>Manual tracking</h3><label className="field"><span>Status</span><select value={job.status} disabled={pending} onChange={event => onStatus(event.target.value as JobStatus)}>{(['New','Applied','Not Interested','Declined'] as JobStatus[]).map(status => <option key={status}>{status}</option>)}</select></label><label className="field"><span>Private notes</span><textarea rows={5} value={notes} disabled={pending} onChange={event => onNotes(event.target.value)} /></label><button className="button button--primary" disabled={notes === job.notes || pending} onClick={onSaveNotes}><Save size={16} />{pending ? 'Saving…' : 'Save notes'}</button></section>
      <section className="detail-section"><h3><Clock3 size={17} /> Status history</h3>{job.status_history?.length ? <ol className="history">{job.status_history.map(entry => <li key={entry.id}><span className="history__mark" aria-hidden="true" /><div><strong>{entry.previous_status} → {entry.new_status}</strong><span>{localDate(entry.changed_at)} · {entry.change_source === 'manual_dashboard' ? 'Manual dashboard change' : 'Confirmed Gmail suggestion'}</span></div></li>)}</ol> : <p className="muted">No status changes recorded yet.</p>}</section>
    </aside>
  </div>
}
