import { Bookmark, BookmarkPlus, Pencil, Trash2 } from 'lucide-react'
import type { SavedView } from '../types'

interface SavedViewsProps {
  views: SavedView[]
  selectedId: number | null
  onLoad(view: SavedView): void
  onCreate(): void
  onRename(view: SavedView): void
  onDelete(view: SavedView): void
}

export function SavedViews({ views, selectedId, onLoad, onCreate, onRename, onDelete }: SavedViewsProps) {
  const selected = views.find(view => view.id === selectedId)
  return <section className="saved-views" aria-labelledby="saved-title">
    <div className="saved-views__label"><Bookmark size={16} /><span id="saved-title">Saved views</span></div>
    <label className="sr-only" htmlFor="saved-view-select">Load a saved view</label>
    <select id="saved-view-select" value={selectedId || ''} onChange={event => {
      const view = views.find(item => item.id === Number(event.target.value)); if (view) onLoad(view)
    }}><option value="">Choose a view…</option>{views.map(view => <option key={view.id} value={view.id}>{view.name}</option>)}</select>
    <button className="button button--compact" onClick={onCreate}><BookmarkPlus size={16} /> Save current</button>
    <button className="icon-button" aria-label="Rename selected saved view" disabled={!selected} onClick={() => selected && onRename(selected)}><Pencil size={16} /></button>
    <button className="icon-button icon-button--danger" aria-label="Delete selected saved view" disabled={!selected} onClick={() => selected && onDelete(selected)}><Trash2 size={16} /></button>
  </section>
}
