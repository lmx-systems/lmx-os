// The two buttons of the design system (docs/DESIGN_SYSTEM.md), as class
// strings so a component can put them on a <button> or an <a> alike. Ink for
// the thing you came to do, outlined for everything else; both are pills.
export const primaryButton =
  'rounded-full bg-[var(--accent)] px-4 py-2 text-[13px] font-bold text-white transition-colors hover:bg-[var(--accent-hover)] disabled:opacity-40'

export const pillButton =
  'rounded-full bg-[var(--surface)] px-3 py-1.5 text-[12.5px] font-semibold text-[var(--text-primary)] shadow-[inset_0_0_0_1.5px_var(--border-strong)] transition-shadow hover:shadow-[inset_0_0_0_1.5px_var(--accent)] disabled:opacity-40'
