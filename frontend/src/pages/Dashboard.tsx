import { AlertTriangle, CheckCircle2, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { DashboardApi } from '../api/client'
import { api as defaultApi } from '../api/client'
import { Filters } from '../components/Filters'
import { Header } from '../components/Header'
import { JobDetail } from '../components/JobDetail'
import { JobList } from '../components/JobList'
import { Dialog } from '../components/Dialog'
import { SavedViews } from '../components/SavedViews'
import { ManualSources } from '../components/ManualSources'
import type { Job, JobFilters, JobsResponse, JobStatus, ManualJobCreate, RefreshRun, SavedView } from '../types'

const DEFAULT_FILTERS: JobFilters = { status: 'New', stale: false, sort_by: 'date_found', sort_order: 'desc', page: 1, page_size: 25 }
const EMPTY_DATA: JobsResponse = { items: [], total: 0, page: 1, page_size: 25, total_pages: 0,
  summary: { New: 0, Applied: 0, 'Not Interested': 0, Declined: 0, Stale: 0 } }

const wait = (milliseconds: number) => new Promise(resolve => window.setTimeout(resolve, milliseconds))
const savedFilters = (filters: JobFilters) => {
  const { page: _page, page_size: _pageSize, sort_by: _sortBy, sort_order: _sortOrder, ...rest } = filters
  return Object.fromEntries(Object.entries(rest).filter(([, value]) => value !== undefined && value !== '')) as Partial<JobFilters>
}

interface DashboardProps { client?: DashboardApi }

export function Dashboard({ client = defaultApi }: DashboardProps) {
  const [filters, setFilters] = useState<JobFilters>(DEFAULT_FILTERS)
  const [data, setData] = useState<JobsResponse>(EMPTY_DATA)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [runs, setRuns] = useState<RefreshRun[]>([])
  const [views, setViews] = useState<SavedView[]>([])
  const [selectedViewId, setSelectedViewId] = useState<number | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshResult, setRefreshResult] = useState<RefreshRun | null>(null)
  const [backupResult, setBackupResult] = useState('')
  const [announcement, setAnnouncement] = useState('')
  const [detail, setDetail] = useState<Job | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [noteDrafts, setNoteDrafts] = useState<Record<number, string>>({})
  const [pending, setPending] = useState<Set<number>>(new Set())
  const [viewDialog, setViewDialog] = useState<{ mode: 'create' | 'rename'; view?: SavedView } | null>(null)
  const [viewName, setViewName] = useState('')
  const [deleteView, setDeleteView] = useState<SavedView | null>(null)
  const mounted = useRef(true)
  const requestNumber = useRef(0)
  const filtersRef = useRef(filters)
  useEffect(() => { filtersRef.current = filters }, [filters])

  const loadJobs = useCallback(async (nextFilters: JobFilters) => {
    const request = ++requestNumber.current
    setLoading(true); setError('')
    try {
      const result = await client.jobs(nextFilters)
      if (mounted.current && request === requestNumber.current) {
        setData(result)
        setNoteDrafts(current => ({ ...Object.fromEntries(result.items.map(job => [job.id, job.notes])), ...current }))
      }
    } catch (reason) {
      if (mounted.current && request === requestNumber.current) setError(reason instanceof Error ? reason.message : 'Could not load jobs.')
    } finally {
      if (mounted.current && request === requestNumber.current) setLoading(false)
    }
  }, [client])

  const loadSupportingData = useCallback(async () => {
    const [runResult, viewResult] = await Promise.allSettled([client.syncRuns(), client.savedViews()])
    if (!mounted.current) return
    if (runResult.status === 'fulfilled') setRuns(runResult.value.items)
    if (viewResult.status === 'fulfilled') setViews(viewResult.value.items)
  }, [client])

  const followRefresh = useCallback(async (runId: number) => {
    setRefreshing(true)
    try {
      while (mounted.current) {
        const run = await client.refresh(runId)
        if (run.state !== 'running') {
          setRefreshResult(run)
          setAnnouncement(`Search complete: ${run.new_jobs_count} new jobs added, ${run.existing_jobs_count} existing jobs updated, ${run.stale_jobs_count} jobs marked stale. Showing fresh jobs that still need a decision.`)
          await Promise.all([loadJobs(filtersRef.current), loadSupportingData()])
          break
        }
        await wait(500)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not check search progress.')
      setAnnouncement('The job search could not be completed.')
    } finally { if (mounted.current) setRefreshing(false) }
  }, [client, loadJobs, loadSupportingData])

  useEffect(() => { loadJobs(filters) }, [filters, loadJobs])
  useEffect(() => {
    mounted.current = true
    loadSupportingData()
    client.health().then(health => {
      if (health.active_refresh_run_id && mounted.current) followRefresh(health.active_refresh_run_id)
    }).catch(() => { /* job list error already provides recovery */ })
    return () => { mounted.current = false }
  }, [client, followRefresh, loadSupportingData])

  const startRefresh = async () => {
    const inboxFilters: JobFilters = { ...filtersRef.current, status: 'New', stale: false, page: 1 }
    filtersRef.current = inboxFilters
    setSelectedViewId(null)
    setFilters(inboxFilters)
    setError(''); setRefreshResult(null); setRefreshing(true); setAnnouncement('Searching approved job sources now.')
    try { const started = await client.startRefresh(); await followRefresh(started.run_id) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not start the search.'); setRefreshing(false) }
  }
  const saveManualJob = async (job: ManualJobCreate) => {
    setError('')
    const created = await client.createManualJob(job)
    setAnnouncement(`${created.title} saved from ${created.source}.`)
    await loadJobs(filtersRef.current)
  }

  const setJobPending = (id: number, active: boolean) => setPending(current => {
    const next = new Set(current); active ? next.add(id) : next.delete(id); return next
  })
  const updateStatus = async (job: Job, status: JobStatus) => {
    setJobPending(job.id, true); setError('')
    try {
      const updated = await client.updateJob(job.id, { status })
      setDetail(current => current?.id === job.id && filtersRef.current.status === 'New' && status !== 'New' ? null : current?.id === job.id ? updated : current)
      setAnnouncement(`${job.title} status changed to ${status}.`)
      await loadJobs({ ...filtersRef.current, page: 1 })
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not update status.') }
    finally { setJobPending(job.id, false) }
  }
  const saveNotes = async (job: Job) => {
    setJobPending(job.id, true); setError('')
    try {
      const updated = await client.updateJob(job.id, { notes: noteDrafts[job.id] ?? job.notes })
      setDetail(current => current?.id === job.id ? updated : current)
      setNoteDrafts(current => ({ ...current, [job.id]: updated.notes }))
      setAnnouncement(`Notes saved for ${job.title}.`)
      await loadJobs(filters)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save notes.') }
    finally { setJobPending(job.id, false) }
  }
  const openDetail = async (id: number) => {
    setDetailLoading(true); setError('')
    try { const job = await client.job(id); setDetail(job); setNoteDrafts(current => ({ ...current, [id]: current[id] ?? job.notes })) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not open the job record.') }
    finally { setDetailLoading(false) }
  }
  const closeDetail = useCallback(() => setDetail(null), [])

  const submitView = async () => {
    const name = viewName.trim(); if (!name || !viewDialog) return
    try {
      if (viewDialog.mode === 'create') {
        const created = await client.createSavedView({ name, filters: savedFilters(filters), sort_by: filters.sort_by, sort_order: filters.sort_order })
        setViews(current => [...current, created].sort((a, b) => a.name.localeCompare(b.name))); setSelectedViewId(created.id)
        setAnnouncement(`Saved view “${created.name}” created.`)
      } else if (viewDialog.view) {
        const updated = await client.updateSavedView(viewDialog.view.id, { name })
        setViews(current => current.map(view => view.id === updated.id ? updated : view).sort((a,b) => a.name.localeCompare(b.name)))
        setAnnouncement(`Saved view renamed to “${updated.name}”.`)
      }
      setViewDialog(null); setViewName('')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save the view.') }
  }
  const removeView = async () => {
    if (!deleteView) return
    try {
      await client.deleteSavedView(deleteView.id)
      setViews(current => current.filter(view => view.id !== deleteView.id))
      if (selectedViewId === deleteView.id) setSelectedViewId(null)
      setAnnouncement(`Saved view “${deleteView.name}” deleted.`); setDeleteView(null)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not delete the view.') }
  }
  const createBackup = async () => {
    try { const result = await client.backup(); const message = `Backup created locally: ${result.path}`; setBackupResult(message); setAnnouncement(message) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not create the backup.') }
  }
  return <div className="app-shell">
    {/*
      THESIS: A field survey ledger makes every lead traceable and refuses the generic tiled SaaS dashboard.
      OWN-WORLD: Pale technical paper, navy survey ink, amber stamps, ruled evidence bands, square controls.
      STORY: Search approved sources, inspect match evidence, then deliberately record an application decision.
      FIRST VIEWPORT: Identity and actions lead; status tally follows; filters and the first evidence rows remain visible.
      FORM: Field Survey Ledger, evidence-transect staging, grounded direction 4, seed 90dc6695.
    */}
    <a className="skip-link" href="#dashboard-main">Skip to job search controls</a>
    <Header summary={data.summary} lastRun={runs[0]} refreshing={refreshing} onRefresh={startRefresh} onExport={() => client.exportJobs(filters)} onBackup={createBackup} />
    <main id="dashboard-main">
      <div className="announcement sr-only" aria-live="polite" aria-atomic="true">{announcement}</div>
      {error && <div className="notice notice--error" role="alert"><AlertTriangle size={19} /><div><strong>Something needs attention</strong><p>{error}</p></div><button className="icon-button" onClick={() => setError('')} aria-label="Dismiss error"><X size={17} /></button></div>}
      {refreshResult && <div className={`notice notice--${refreshResult.status === 'failed' ? 'error' : 'success'}`} role="status">
        {refreshResult.status === 'failed' ? <AlertTriangle size={19} /> : <CheckCircle2 size={19} />}
        <div><strong>Search complete</strong><p>{refreshResult.new_jobs_count} new jobs added, {refreshResult.existing_jobs_count} existing jobs updated, {refreshResult.stale_jobs_count} jobs marked stale. Showing fresh jobs that still need a decision.</p>
          {refreshResult.errors.length > 0 && <ul className="source-errors">{refreshResult.errors.map(item => <li key={`${item.source}-${item.code}`}><strong>{item.source}</strong>: {item.message}</li>)}</ul>}
        </div><button className="icon-button" onClick={() => setRefreshResult(null)} aria-label="Dismiss search result"><X size={17} /></button>
      </div>}
      {backupResult && <div className="notice notice--success" role="status"><CheckCircle2 size={19} /><div><strong>Private backup ready</strong><p>{backupResult}</p></div><button className="icon-button" onClick={() => setBackupResult('')} aria-label="Dismiss backup result"><X size={17} /></button></div>}
      <ManualSources onSave={saveManualJob} />
      <Filters filters={filters} onChange={next => { setSelectedViewId(null); setFilters(next) }} total={data.total} />
      <SavedViews views={views} selectedId={selectedViewId} onLoad={view => { setSelectedViewId(view.id); setFilters({ ...DEFAULT_FILTERS, ...view.filters, sort_by: view.sort_by, sort_order: view.sort_order, page: 1 }) }} onCreate={() => { setViewName(''); setViewDialog({ mode: 'create' }) }} onRename={view => { setViewName(view.name); setViewDialog({ mode: 'rename', view }) }} onDelete={setDeleteView} />
      <section className="results" aria-busy={loading}>
        <div className="results__heading"><div><p className="kicker">Observed opportunities</p><h2>Lead register</h2></div><span>{data.total} total · {data.page_size} per page</span></div>
        {loading ? <div className="loading-state" role="status"><span className="loading-line" /><span className="loading-line" /><span className="loading-line" />Loading job records…</div> : <JobList data={data} filters={filters} noteDrafts={noteDrafts} pending={pending} onFilters={setFilters} onOpen={openDetail} onStatus={updateStatus} onNoteDraft={(id, notes) => setNoteDrafts(current => ({ ...current, [id]: notes }))} onSaveNotes={saveNotes} />}
      </section>
    </main>
    {detailLoading && <div className="drawer-loading" role="status">Opening job record…</div>}
    {detail && <JobDetail job={detail} notes={noteDrafts[detail.id] ?? detail.notes} pending={pending.has(detail.id)} onClose={closeDetail} onStatus={status => updateStatus(detail, status)} onNotes={notes => setNoteDrafts(current => ({ ...current, [detail.id]: notes }))} onSaveNotes={() => saveNotes(detail)} />}
    {viewDialog && <Dialog labelledBy="view-dialog-title" onClose={() => setViewDialog(null)}><h2 id="view-dialog-title">{viewDialog.mode === 'create' ? 'Save current view' : 'Rename saved view'}</h2><p>{viewDialog.mode === 'create' ? 'Keep these filters and this sort order for another session.' : 'Choose a clear name for this filter set.'}</p><label className="field"><span>View name</span><input autoFocus value={viewName} maxLength={100} onChange={event => setViewName(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') submitView() }} /></label><div className="dialog-actions"><button className="button button--quiet" onClick={() => setViewDialog(null)}>Cancel</button><button className="button button--primary" disabled={!viewName.trim()} onClick={submitView}>{viewDialog.mode === 'create' ? 'Save view' : 'Save name'}</button></div></Dialog>}
    {deleteView && <Dialog labelledBy="delete-title" onClose={() => setDeleteView(null)} alert><h2 id="delete-title">Delete “{deleteView.name}”?</h2><p>This removes only the saved filter setup. It does not remove jobs, notes, or application history.</p><div className="dialog-actions"><button autoFocus className="button button--quiet" onClick={() => setDeleteView(null)}>Keep view</button><button className="button button--danger" onClick={removeView}>Delete view</button></div></Dialog>}
  </div>
}
