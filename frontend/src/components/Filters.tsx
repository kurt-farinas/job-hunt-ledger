import { RotateCcw, Search, SlidersHorizontal } from 'lucide-react'
import type { JobFilters, JobStatus, SortField } from '../types'

interface FiltersProps { filters: JobFilters; onChange(next: JobFilters): void; total: number }
const statuses: JobStatus[] = ['New', 'Applied', 'Not Interested', 'Declined']
const sources = ['linkedin', 'jobstreet', 'indeed', 'prosple', 'glassdoor', 'greenhouse', 'lever']

export function Filters({ filters, onChange, total }: FiltersProps) {
  const set = <K extends keyof JobFilters>(key: K, value: JobFilters[K]) => onChange({ ...filters, [key]: value, page: 1 })
  const clear = () => onChange({ status: 'New', stale: false, sort_by: 'date_found', sort_order: 'desc', page: 1, page_size: 25 })
  return <section className="filter-panel" aria-labelledby="filter-title">
    <div className="filter-panel__heading">
      <div><h2 id="filter-title"><SlidersHorizontal size={18} /> Survey controls</h2><p>{total} matching {total === 1 ? 'lead' : 'leads'}</p></div>
      <button className="text-button" onClick={clear}><RotateCcw size={15} /> Reset filters</button>
    </div>
    <div className="filter-grid">
      <label className="field field--search"><span>Search title, company, or location</span><span className="input-with-icon"><Search size={17} /><input value={filters.search || ''} onChange={event => set('search', event.target.value || undefined)} placeholder="React developer…" /></span></label>
      <label className="field"><span>Status</span><select value={filters.status || ''} onChange={event => set('status', (event.target.value || undefined) as JobStatus | undefined)}><option value="">All statuses (history)</option>{statuses.map(status => <option key={status}>{status}</option>)}</select></label>
      <label className="field"><span>Source</span><select value={filters.source || ''} onChange={event => set('source', event.target.value || undefined)}><option value="">All sources</option>{sources.map(source => <option key={source} value={source}>{source.replaceAll('_', ' ')}</option>)}</select></label>
      <label className="field"><span>Freshness</span><select value={filters.stale === undefined ? '' : String(filters.stale)} onChange={event => set('stale', event.target.value === '' ? undefined : event.target.value === 'true')}><option value="">Fresh and stale</option><option value="false">Fresh only</option><option value="true">Stale only</option></select></label>
      <label className="field"><span>Work arrangement</span><select value={filters.work_arrangement || ''} onChange={event => set('work_arrangement', event.target.value || undefined)}><option value="">Any arrangement</option><option>Remote</option><option>Hybrid</option><option>On-site</option></select></label>
      <label className="field"><span>Employment type</span><select value={filters.employment_type || ''} onChange={event => set('employment_type', event.target.value || undefined)}><option value="">Any type</option>{['Full-time', 'Contract', 'Freelance', 'Internship', 'Part-time'].map(type => <option key={type}>{type}</option>)}</select></label>
      <label className="field"><span>Company preference</span><select value={filters.preferred_company === undefined ? '' : String(filters.preferred_company)} onChange={event => set('preferred_company', event.target.value === '' ? undefined : event.target.value === 'true')}><option value="">All companies</option><option value="true">Preferred only</option><option value="false">Other companies</option></select></label>
      <label className="field"><span>Found from</span><input type="date" value={filters.date_from || ''} onChange={event => set('date_from', event.target.value || undefined)} /></label>
      <label className="field"><span>Found through</span><input type="date" value={filters.date_to || ''} onChange={event => set('date_to', event.target.value || undefined)} /></label>
      <label className="field"><span>Sort by</span><select value={filters.sort_by} onChange={event => set('sort_by', event.target.value as SortField)}>{[['date_found','Date found'],['posted_at','Posting date'],['company','Company'],['title','Title'],['source','Source'],['status','Status']].map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="field"><span>Order</span><select value={filters.sort_order} onChange={event => set('sort_order', event.target.value as 'asc' | 'desc')}><option value="desc">Descending</option><option value="asc">Ascending</option></select></label>
    </div>
  </section>
}
