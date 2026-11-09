import { useEffect, useRef, useState } from 'react'

/**
 * Which brands this post is for: a dropdown of checkboxes.
 *
 * Only the picked brands get a branded image and can be published to. A brand
 * whose logo file is missing cannot be picked — its image could not be
 * branded. Disabled while the post is locked (images exist) or a call is
 * running; the hint underneath says how to unlock it.
 */
export function BrandPicker({ brands, selected, onToggle, disabled, locked }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  // Closes on a click outside or Escape, like a native select.
  useEffect(() => {
    if (!open) return undefined
    const outside = (e) => {
      if (!ref.current?.contains(e.target)) setOpen(false)
    }
    const escape = (e) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  useEffect(() => {
    if (disabled) setOpen(false)
  }, [disabled])

  const names = brands.filter((b) => selected.includes(b.id)).map((b) => b.name)
  const label = !names.length
    ? 'Pick brands'
    : names.length === brands.length
      ? `All brands (${names.length})`
      : names.join(', ')

  return (
    <div className="brand-picker" ref={ref}>
      <button
        type="button"
        className={`brand-picker-button ${names.length ? '' : 'is-empty'}`}
        aria-haspopup="true"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        title={locked ? 'Locked to this post. Click New post to pick different brands.' : undefined}
      >
        <span className="brand-picker-value">{label}</span>
        <span className="brand-picker-caret" aria-hidden="true">
          {locked ? '🔒' : '▾'}
        </span>
      </button>

      {open && (
        <ul className="brand-picker-menu" role="group" aria-label="Brands for this post">
          {brands.map((b) => {
            const noLogo = !b.logo_base64
            return (
              <li key={b.id}>
                <label
                  className={`brand-option ${noLogo ? 'is-blocked' : ''}`}
                  title={noLogo ? 'Logo file missing, so its image cannot be branded' : undefined}
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(b.id)}
                    disabled={noLogo}
                    onChange={() => onToggle(b.id)}
                  />
                  <span className="pick-box" aria-hidden="true" />
                  <span className="brand-option-name">{b.name}</span>
                  {b.logo_base64 && (
                    <img
                      className="brand-option-logo"
                      src={`data:image/png;base64,${b.logo_base64}`}
                      alt=""
                    />
                  )}
                </label>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
