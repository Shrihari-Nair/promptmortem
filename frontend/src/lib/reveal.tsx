import { useEffect, useRef, type ReactNode } from 'react'
import { cn } from './utils'

/** Fade-up-and-unblur when scrolled into view (IntersectionObserver, no scroll listeners). `delay` staggers siblings. */
export function Reveal({ children, delay = 0, className, as: Tag = 'div' }: { children: ReactNode; delay?: number; className?: string; as?: 'div' | 'li' | 'section' }) {
  const ref = useRef<HTMLElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (typeof IntersectionObserver === 'undefined') { el.classList.add('in'); return }
    const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); io.disconnect() } }), { threshold: 0.12, rootMargin: '0px 0px -6% 0px' })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return <Tag ref={ref as never} style={{ '--d': `${delay}ms` } as React.CSSProperties} className={cn('reveal', className)}>{children}</Tag>
}
