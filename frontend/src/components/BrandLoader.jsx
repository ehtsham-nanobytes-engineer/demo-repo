/**
 * The waiting state, drawn with the brand mark itself.
 *
 * The mark is the square grid of shapes from the nanobyte logo, so it can turn
 * a full circle and land back exactly on itself. The motion is one rotation,
 * then a held pose, then another — a tumble rather than a spinner, which reads
 * as deliberate work instead of a generic wait.
 */
export function BrandLoader({ label, sub, size = 'md' }) {
  return (
    <div className={`brand-loader brand-loader-${size}`} role="status" aria-live="polite">
      <span className="brand-loader-mark" aria-hidden="true">
        <img src="/mark.png" alt="" />
      </span>
      {label && <span className="brand-loader-label">{label}</span>}
      {sub && <small className="brand-loader-sub">{sub}</small>}
    </div>
  )
}
