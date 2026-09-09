import { ExternalLink, Plus, SearchCheck } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import type { ManualJobCreate } from '../types'

const sites = [
  { name: 'LinkedIn Jobs', url: 'https://www.linkedin.com/jobs/', note: 'Search and company alerts' },
  { name: 'JobStreet', url: 'https://ph.jobstreet.com/', note: 'Philippines job listings' },
  { name: 'Indeed', url: 'https://ph.indeed.com/', note: 'Broad manual search' },
  { name: 'Prosple', url: 'https://au.prosple.com/', note: 'Graduate and early-career roles' },
  { name: 'Glassdoor', url: 'https://www.glassdoor.com/Job/index.htm', note: 'Roles and company research' },
]

const sourceForUrl = (value: string) => {
  try {
    const host = new URL(value).hostname.toLowerCase().replace(/\.$/, '')
    const sources = [['linkedin.com', 'LinkedIn'], ['jobstreet.com', 'JobStreet'], ['indeed.com', 'Indeed'], ['prosple.com', 'Prosple'], ['glassdoor.com', 'Glassdoor']] as const
    return sources.find(([domain]) => host === domain || host.endsWith(`.${domain}`))?.[1] || null
  } catch { return null }
}

interface ManualSourcesProps { onSave(job: ManualJobCreate): Promise<void> }

export function ManualSources({ onSave }: ManualSourcesProps) {
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('')
  const [company, setCompany] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const source = sourceForUrl(url.trim())
  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (!source || !title.trim() || !company.trim()) return
    setSaving(true); setError('')
    try {
      await onSave({ source_url: url.trim(), title: title.trim(), company: company.trim() })
      setUrl(''); setTitle(''); setCompany('')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save this job.') }
    finally { setSaving(false) }
  }
  return <section className="manual-sources" aria-labelledby="manual-sources-title">
    <details className="manual-sources__details">
      <summary className="manual-sources__heading">
      <div>
        <p className="kicker"><SearchCheck size={14} /> Search beyond the feeds</p>
        <h2 id="manual-sources-title">Manual search desk</h2>
      </div>
      <p>Open trusted boards or save a listing. The dashboard does not read, scrape, sign in, or apply.</p>
      </summary>
      <div className="manual-sources__body">
      <nav className="manual-sources__links" aria-label="Manual job sites">
        {sites.map(site => <a key={site.name} href={site.url} target="_blank" rel="noopener noreferrer">
          <span><strong>{site.name}</strong><small>{site.note}</small></span><ExternalLink size={16} aria-hidden="true" />
        </a>)}
      </nav>
      <form className="manual-save" onSubmit={save}>
        <div><p className="kicker"><Plus size={14} /> Save a trusted listing</p><h3>Keep a lead you found</h3><p>Paste its link, then confirm the title and company. We never read the listing page.</p></div>
        <label className="field"><span>Listing URL</span><input type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://…" required /></label>
        {url.trim() && <p className={`manual-save__source${source ? '' : ' manual-save__source--error'}`}>{source ? `Trusted source detected: ${source}` : 'Use a LinkedIn, JobStreet, Indeed, Prosple, or Glassdoor listing URL.'}</p>}
        <div className="manual-save__fields">
          <label className="field"><span>Role title</span><input value={title} maxLength={500} onChange={event => setTitle(event.target.value)} required /></label>
          <label className="field"><span>Company</span><input value={company} maxLength={500} onChange={event => setCompany(event.target.value)} required /></label>
        </div>
        {error && <p className="manual-save__source manual-save__source--error" role="alert">{error}</p>}
        <button className="button button--primary" disabled={!source || !title.trim() || !company.trim() || saving}>{saving ? 'Saving lead…' : 'Save job'}</button>
      </form>
      </div>
    </details>
  </section>
}
