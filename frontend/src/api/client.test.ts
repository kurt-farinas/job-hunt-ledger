import { beforeEach, expect, test, vi } from 'vitest'
import { API_BASE_URL, api, queryString } from './client'

beforeEach(() => { document.body.innerHTML = ''; vi.restoreAllMocks() })

test('queryString excludes pagination for filtered exports and keeps false values', () => {
  const filters = { sort_by: 'date_found' as const, sort_order: 'desc' as const, page: 3, page_size: 25, stale: false, search: 'React & PHP' }
  expect(queryString(filters, false)).toContain('stale=false')
  expect(queryString(filters, false)).toContain('search=React+%26+PHP')
  expect(queryString(filters, false)).not.toContain('page=')
})

test('export creates and removes a browser download anchor', () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  api.exportJobs({ sort_by: 'company', sort_order: 'asc', page: 1, page_size: 25, status: 'New' })
  expect(click).toHaveBeenCalledOnce()
  expect(document.querySelector('a')).toBeNull()
  const anchor = click.mock.instances[0] as HTMLAnchorElement
  expect(anchor.href).toBe(`${API_BASE_URL}/api/export/jobs.csv?sort_by=company&sort_order=asc&status=New`)
})
