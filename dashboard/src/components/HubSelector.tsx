interface HubSelectorProps {
  hubId: string
  onChange: (hubId: string) => void
}

/**
 * There's no "list hubs" endpoint on the backend yet (Hub rows exist in
 * Postgres, but nothing exposes them), so this is a plain text input
 * rather than a dropdown. Swap this out once such an endpoint exists -
 * tracked as a natural fast-follow in docs/NEXT_STEPS.md.
 */
export function HubSelector({ hubId, onChange }: HubSelectorProps) {
  return (
    <div className="flex items-center gap-2">
      <label htmlFor="hub-id" className="text-sm text-slate-400">
        Hub ID
      </label>
      <input
        id="hub-id"
        type="text"
        value={hubId}
        onChange={(e) => onChange(e.target.value)}
        placeholder="paste a hub UUID"
        className="w-80 rounded border border-slate-700 bg-slate-800 px-3 py-1.5 text-sm text-slate-100 placeholder:text-slate-500 focus:border-purple-500 focus:outline-none"
      />
    </div>
  )
}
