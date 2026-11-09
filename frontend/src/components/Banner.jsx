export function Banner({ tone = 'info', children, onDismiss }) {
  if (!children) return null
  return (
    <div className={`banner banner-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <span>{children}</span>
      {onDismiss && (
        <button className="banner-close" onClick={onDismiss} aria-label="Dismiss">
          ×
        </button>
      )}
    </div>
  )
}
