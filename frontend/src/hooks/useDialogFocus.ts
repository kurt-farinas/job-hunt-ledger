import { useEffect, type RefObject } from 'react'

const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function useDialogFocus(ref: RefObject<HTMLElement | null>, onClose: () => void) {
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const dialog = ref.current
    const initial = dialog?.querySelector<HTMLElement>('[autofocus]') ?? dialog?.querySelector<HTMLElement>(FOCUSABLE)
    initial?.focus()

    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      if (event.key !== 'Tab' || !dialog) return
      const focusable = [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(element => !element.hidden)
      if (!focusable.length) { event.preventDefault(); return }
      const first = focusable[0]
      const last = focusable.at(-1)!
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', handleKey)
    return () => { document.removeEventListener('keydown', handleKey); previous?.focus() }
  }, [onClose, ref])
}
