/**
 * One button, three weights.
 *
 * `primary` is the indigo call to action and there is only ever one on screen.
 * `solid` is the quieter filled weight for the panel actions. `ghost` is the
 * default outline — used for the regenerate controls, which each cost an API
 * call and should not invite idle clicking.
 */
export function Button({
  variant = 'ghost',
  loading = false,
  loadingLabel,
  children,
  className = '',
  ...props
}) {
  return (
    <button
      className={`btn btn-${variant} ${loading ? 'is-loading' : ''} ${className}`}
      disabled={props.disabled || loading}
      {...props}
    >
      {loading && <span className="spinner" aria-hidden="true" />}
      {loading ? loadingLabel || children : children}
    </button>
  )
}
