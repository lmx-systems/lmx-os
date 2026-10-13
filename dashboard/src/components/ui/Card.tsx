import type { ReactNode } from 'react'

interface CardProps {
  title: string
  meta?: ReactNode
  children: ReactNode
  className?: string
}

/**
 * The one tile every console section uses (docs/DESIGN_SYSTEM.md): white on the
 * grey ground, a large radius, no border and no rule under the title - the
 * ground is what separates one tile from the next.
 */
export function Card({ title, meta, children, className = '' }: CardProps) {
  return (
    <section className={`rounded-[var(--radius-lg)] bg-[var(--surface)] p-6 ${className}`}>
      <div className="mb-4 flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <h2 className="text-base font-bold tracking-[-0.01em] text-[var(--text-primary)]">{title}</h2>
        {meta && <span className="text-[13px] font-medium text-[var(--text-muted)]">{meta}</span>}
      </div>
      {children}
    </section>
  )
}
