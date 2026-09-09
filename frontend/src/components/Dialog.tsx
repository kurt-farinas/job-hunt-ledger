import { useRef, type ReactNode } from 'react'
import { useDialogFocus } from '../hooks/useDialogFocus'

interface DialogProps {
  children: ReactNode
  labelledBy: string
  onClose(): void
  alert?: boolean
}

export function Dialog({ children, labelledBy, onClose, alert = false }: DialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  useDialogFocus(dialogRef, onClose)
  return <div className="dialog-shell" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <div ref={dialogRef} className="small-dialog" role={alert ? 'alertdialog' : 'dialog'} aria-modal="true" aria-labelledby={labelledBy}>{children}</div>
  </div>
}
