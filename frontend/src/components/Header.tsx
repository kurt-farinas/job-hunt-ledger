import { DatabaseBackup, Download, RefreshCw, ShieldCheck } from 'lucide-react'
import type { JobSummary, RefreshRun } from '../types'

interface HeaderProps {
  summary: JobSummary
  lastRun?: RefreshRun
  refreshing: boolean
  onRefresh(): void
  onExport(): void
  onBackup(): void
}

const counters: Array<{ key: keyof JobSummary; label: string }> = [
  { key: 'New', label: 'New' }, { key: 'Applied', label: 'Applied' },
  { key: 'Not Interested', label: 'Not interested' }, { key: 'Declined', label: 'Declined' },
  { key: 'Stale', label: 'Stale' },
]

function localTime(value?: string | null) {
  if (!value) return 'No searches yet'
  return new Intl.DateTimeFormat('en-PH', {
    timeZone: 'Asia/Manila', dateStyle: 'medium', timeStyle: 'short',
  }).format(new Date(value))
}

export function Header({ summary, lastRun, refreshing, onRefresh, onExport, onBackup }: HeaderProps) {
  return <header className="masthead">
    <div className="masthead__identity">
      <div className="product-mark" aria-hidden="true"><span>JH</span></div>
      <div>
        <p className="kicker"><ShieldCheck size={14} /> Local workspace · Manila time</p>
        <h1>Job Hunt Ledger</h1>
        <p className="masthead__subtitle">Traceable leads for frontend and full-stack work.</p>
      </div>
    </div>
    <div className="masthead__actions">
      <button className="button button--primary" onClick={onRefresh} disabled={refreshing}>
        <RefreshCw size={18} className={refreshing ? 'spin' : ''} />
        {refreshing ? 'Finding jobs…' : 'Find jobs now'}
      </button>
      <details className="masthead__tools">
        <summary>More tools</summary>
        <div className="masthead__tools-menu">
          <button className="button button--quiet" onClick={onExport}><Download size={17} /> Export CSV</button>
          <button className="button button--quiet" onClick={onBackup}><DatabaseBackup size={17} /> Backup database</button>
        </div>
      </details>
    </div>
    <dl className="counter-strip" aria-label="Application summary">
      {counters.map(({ key, label }) => <div className={`counter counter--${key.toLowerCase().replace(' ', '-')}`} key={key}>
        <dt>{label}</dt><dd>{summary[key]}</dd>
      </div>)}
      <div className="counter counter--last">
        <dt>Last refresh</dt>
        <dd className="counter__time">{localTime(lastRun?.completed_at || lastRun?.started_at)}</dd>
        {lastRun && <span className={`run-state run-state--${lastRun.status}`}>{lastRun.status.replace('_', ' ')}</span>}
      </div>
    </dl>
  </header>
}
