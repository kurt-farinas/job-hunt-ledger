import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import App from './App'
import type { JobFilters, JobsResponse, RefreshRun } from './types'
import { completedRun, data, job, makeApi, savedView } from './test/fixtures'

async function ready(api = makeApi()) {
  const user = userEvent.setup()
  render(<App api={api} />)
  await screen.findByRole('button', { name: 'Junior React Developer' })
  return { api, user }
}

test('renders the job ledger, match evidence, salary, and safe external link', async () => {
  await ready()
  expect(screen.getByText('Northstar Labs')).toBeInTheDocument()
  expect(screen.getByText('Title: react developer')).toBeInTheDocument()
  expect(screen.getByText('PHP 35,000–50,000 / month')).toBeInTheDocument()
  expect(screen.getByText('Preferred company')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /original listing/i })).toHaveAttribute('target', '_blank')
  expect(screen.getByRole('link', { name: /original listing/i })).toHaveAttribute('rel', 'noopener noreferrer')
})

test('manual search desk opens trusted sites safely without automating them', async () => {
  await ready()
  const desk = screen.getByRole('region', { name: 'Manual search desk' })
  expect(within(desk).getByText(/does not read, scrape, sign in, or apply/i)).toBeInTheDocument()
  for (const name of ['LinkedIn Jobs', 'JobStreet', 'Indeed', 'Prosple', 'Glassdoor']) {
    const link = within(desk).getByRole('link', { name: new RegExp(name, 'i') })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  }
})

test('trusted listing save requires a supported URL and confirms the record details', async () => {
  const { api, user } = await ready()
  const desk = screen.getByRole('region', { name: 'Manual search desk' })
  const save = within(desk).getByRole('button', { name: 'Save job' })
  await user.type(within(desk).getByLabelText('Listing URL'), 'https://example.com/jobs/1')
  expect(within(desk).getByText(/use a linkedin, jobstreet/i)).toBeInTheDocument()
  expect(save).toBeDisabled()
  await user.clear(within(desk).getByLabelText('Listing URL'))
  await user.type(within(desk).getByLabelText('Listing URL'), 'https://www.linkedin.com/jobs/view/1')
  expect(within(desk).getByText('Trusted source detected: LinkedIn')).toBeInTheDocument()
  await user.type(within(desk).getByLabelText('Role title'), 'Frontend Developer')
  await user.type(within(desk).getByLabelText('Company'), 'Example Co')
  await user.click(save)
  await waitFor(() => expect(api.createManualJob).toHaveBeenCalledWith({ source_url: 'https://www.linkedin.com/jobs/view/1', title: 'Frontend Developer', company: 'Example Co' }))
})

test('shows loading, empty, and recoverable error states', async () => {
  let resolveJobs!: (value: ReturnType<typeof data>) => void
  const slow = makeApi({ jobs: vi.fn((_filters: JobFilters) => new Promise<JobsResponse>(resolve => { resolveJobs = resolve })) })
  const view = render(<App api={slow} />)
  expect(screen.getByText(/loading job records/i)).toBeInTheDocument()
  await act(async () => resolveJobs(data([])))
  expect(await screen.findByText('No leads in this view')).toBeInTheDocument()
  view.unmount()

  const broken = makeApi({ jobs: vi.fn().mockRejectedValue(new Error('Backend is offline. Start FastAPI and try again.')) })
  render(<App api={broken} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Backend is offline')
})

test('status, source, date, stale, arrangement, employment, company, search and sort controls refetch', async () => {
  const { api, user } = await ready()
  const jobs = vi.mocked(api.jobs); jobs.mockClear()
  const controls = within(screen.getByRole('region', { name: 'Survey controls' }))
  await user.selectOptions(controls.getByLabelText('Status'), 'Applied')
  await waitFor(() => expect(jobs).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'Applied', page: 1 })))
  await user.selectOptions(controls.getByLabelText('Source'), 'linkedin')
  await user.selectOptions(controls.getByLabelText('Freshness'), 'true')
  await user.selectOptions(controls.getByLabelText('Work arrangement'), 'Remote')
  await user.selectOptions(controls.getByLabelText('Employment type'), 'Contract')
  await user.selectOptions(controls.getByLabelText('Company preference'), 'true')
  await user.type(controls.getByLabelText('Found from'), '2026-09-01')
  await user.type(controls.getByLabelText('Found through'), '2026-09-07')
  await user.type(controls.getByLabelText(/search title/i), 'React')
  await user.selectOptions(controls.getByLabelText('Sort by'), 'company')
  await user.selectOptions(controls.getByLabelText('Order'), 'asc')
  await waitFor(() => expect(jobs).toHaveBeenLastCalledWith(expect.objectContaining({ source: 'linkedin', stale: true, work_arrangement: 'Remote', employment_type: 'Contract', preferred_company: true, date_from: '2026-09-01', date_to: '2026-09-07', search: 'React', sort_by: 'company', sort_order: 'asc' })))
})

test('manual status selection and explicit note save use restricted job patches', async () => {
  const { api, user } = await ready()
  await user.selectOptions(screen.getByLabelText('Status for Junior React Developer'), 'Applied')
  await waitFor(() => expect(api.updateJob).toHaveBeenCalledWith(1, { status: 'Applied' }))
  const notes = screen.getByLabelText('Notes for Junior React Developer')
  await user.type(notes, 'Portfolio shared on September 7.')
  expect(api.updateJob).toHaveBeenCalledTimes(1)
  await user.click(screen.getByRole('button', { name: 'Save notes' }))
  await waitFor(() => expect(api.updateJob).toHaveBeenLastCalledWith(1, { notes: 'Portfolio shared on September 7.' }))
  expect(await screen.findByText(/notes saved for junior react developer/i)).toBeInTheDocument()
})

test('detail drawer shows description, role facts, notes, and full status history', async () => {
  const { user } = await ready()
  await user.click(screen.getByRole('button', { name: 'Junior React Developer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Junior React Developer' })
  expect(within(dialog).getByText(/build accessible react interfaces/i)).toBeInTheDocument()
  expect(within(dialog).getByText('New → Applied')).toBeInTheDocument()
  expect(within(dialog).getByText(/manual dashboard change/i)).toBeInTheDocument()
  expect(within(dialog).getByRole('link', { name: /open original listing/i })).toHaveAttribute('rel', 'noopener noreferrer')
  await user.keyboard('{Escape}')
  expect(screen.queryByRole('dialog', { name: 'Junior React Developer' })).not.toBeInTheDocument()
})

test('saved views load, create, rename, and delete without touching jobs', async () => {
  const { api, user } = await ready()
  await user.selectOptions(screen.getByLabelText('Load a saved view'), String(savedView.id))
  await waitFor(() => expect(api.jobs).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'New', work_arrangement: 'Remote' })))
  await user.click(screen.getByRole('button', { name: /save current/i }))
  await user.type(screen.getByLabelText('View name'), 'Recent Manila roles')
  await user.click(screen.getByRole('button', { name: 'Save view' }))
  await waitFor(() => expect(api.createSavedView).toHaveBeenCalledWith(expect.objectContaining({ name: 'Recent Manila roles', filters: expect.any(Object) })))
  expect(await screen.findByRole('option', { name: 'Recent Manila roles' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: /rename selected/i }))
  const name = screen.getByLabelText('View name'); await user.clear(name); await user.type(name, 'Remote shortlist')
  await user.click(screen.getByRole('button', { name: 'Save name' }))
  await waitFor(() => expect(api.updateSavedView).toHaveBeenCalledWith(9, { name: 'Remote shortlist' }))
  await user.click(screen.getByRole('button', { name: /delete selected/i }))
  await user.click(screen.getByRole('button', { name: 'Delete view' }))
  await waitFor(() => expect(api.deleteSavedView).toHaveBeenCalledWith(9))
  expect(api.updateJob).not.toHaveBeenCalled()
})

test('export uses current filters and backup announces its local path', async () => {
  const { api, user } = await ready()
  await user.type(screen.getByLabelText(/search title/i), 'Laravel')
  await user.click(screen.getByRole('button', { name: 'Export CSV' }))
  expect(api.exportJobs).toHaveBeenCalledWith(expect.objectContaining({ search: 'Laravel' }))
  await user.click(screen.getByRole('button', { name: 'Backup database' }))
  await waitFor(() => expect(api.backup).toHaveBeenCalled())
  expect(within(await screen.findByRole('status')).getByText(/backup created locally: C:\\Private\\backup.sqlite3/i)).toBeInTheDocument()
})

test('Find jobs now disables while running then shows counts and source failures separately', async () => {
  let resolveRefresh!: (run: RefreshRun) => void
  const partial = { ...completedRun, status: 'partial_failure' as const, state: 'partial_failure' as const,
    errors: [{ source: 'RemoteOK', code: 'request_timeout', message: 'Request timed out.' }] }
  const api = makeApi({ refresh: vi.fn((_runId: number) => new Promise<RefreshRun>(resolve => { resolveRefresh = resolve })) })
  const { user } = await ready(api)
  const button = screen.getByRole('button', { name: 'Find jobs now' })
  await user.click(button)
  await waitFor(() => expect(screen.getByRole('button', { name: /finding jobs/i })).toBeDisabled())
  await act(async () => resolveRefresh(partial))
  const result = await screen.findByRole('status')
  expect(within(result).getByText(/12 new jobs added, 36 existing jobs updated, 2 jobs marked stale/i)).toBeInTheDocument()
  expect(within(result).getByText(/RemoteOK/).closest('li')).toHaveTextContent('Request timed out')
  expect(screen.getByRole('button', { name: 'Find jobs now' })).toBeEnabled()
})

test('refresh failure and backup failure provide clear error recovery', async () => {
  const api = makeApi({ startRefresh: vi.fn().mockRejectedValue(new Error('A search is already running.')), backup: vi.fn().mockRejectedValue(new Error('Backup folder is unavailable.')) })
  const { user } = await ready(api)
  await user.click(screen.getByRole('button', { name: 'Find jobs now' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('A search is already running')
  await user.click(screen.getByLabelText('Dismiss error'))
  await user.click(screen.getByRole('button', { name: 'Backup database' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Backup folder is unavailable')
})
