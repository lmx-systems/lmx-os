import qrcode from 'qrcode-generator'
import { useMemo } from 'react'
import type { DriverSignInCode } from '../lib/types'

/**
 * A driver's sign-in code, shown once to the ops user who issued it.
 *
 * Replaces the texted code: the driver scans this QR code in the app, or types
 * the ten characters. The server keeps only a keyed hash, so this screen is the
 * only place the code exists. Lost before it is used, the answer is a new one,
 * which retires this.
 *
 * Whoever holds the code signs in as the driver, which is why the note asks
 * for it to be handed over in person.
 */
export function SignInCode({ name, code }: { name: string; code: DriverSignInCode }) {
  const image = useMemo(() => {
    const qr = qrcode(0, 'M')
    qr.addData(code.qr_payload)
    qr.make()
    return qr.createDataURL(5, 2)
  }, [code.qr_payload])

  return (
    <div className="mt-2 flex gap-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] p-2.5">
      <img
        src={image}
        alt={`Sign-in QR code for ${name}`}
        className="h-32 w-32 shrink-0 bg-white [image-rendering:pixelated]"
      />
      <div className="min-w-0 text-[12px] text-[var(--text-secondary)]">
        <p className="text-[var(--text-primary)]">Sign-in code for {name}</p>
        <p className="my-1 font-mono text-[18px] tracking-wider text-[var(--text-primary)]">{code.display}</p>
        <p>
          Works once, until {new Date(code.expires_at).toLocaleString()}. Shown only now, so
          have {name} scan it in the driver app before you leave this screen.
        </p>
        <p className="mt-1 text-[var(--text-muted)]">
          Hand it over in person. Whoever has the code signs in as this driver.
        </p>
      </div>
    </div>
  )
}
