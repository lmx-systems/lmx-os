import type { ReactNode } from 'react'

interface CardProps {
  title: string
  meta?: ReactNode
  children: ReactNode
  className?: string
}

/**
 * The one card shell every dashboard section uses - replaces each
 * component hand-rolling its own `rounded-lg border ...` string (the
 * "no design system" gap flagged in the pre-redesign dashboard).
 */
export function Card({ title, meta, children, className = '' }: CardProps) {
  return (
    <section
      className={`overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] ${className}`}
    >
      <div className="flex items-center gap-2.5 border-b border-[var(--border)] px-4 py-3">
        <h2 className="text-[14.5px] font-medium text-[var(--text-primary)]">{title}</h2>
        {meta && <span className="text-xs font-medium text-[var(--text-muted)]">{meta}</span>}
      </div>
      <div className="p-4">{children}</div>
    </section>
  )
}
