import type { DashboardApi } from './api/client'
import { Dashboard } from './pages/Dashboard'

export default function App({ api }: { api?: DashboardApi }) {
  return <Dashboard client={api} />
}
