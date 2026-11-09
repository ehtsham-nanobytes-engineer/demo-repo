const LABEL = { light: 'Light', dark: 'Dark', system: 'System' }

/** Three-state theme control. One click moves to the next state. */
export function ThemeToggle({ theme, onCycle }) {
  return (
    <button
      className="theme-toggle"
      onClick={onCycle}
      title={`Theme: ${LABEL[theme]} — click to change`}
      aria-label={`Theme: ${LABEL[theme]}. Click to change.`}
    >
      <Icon theme={theme} />
      <span className="theme-label">{LABEL[theme]}</span>
    </button>
  )
}

function Icon({ theme }) {
  const common = { width: 15, height: 15, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round',
    strokeLinejoin: 'round', 'aria-hidden': true }

  if (theme === 'dark') {
    return (
      <svg {...common}>
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
      </svg>
    )
  }
  if (theme === 'light') {
    return (
      <svg {...common}>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
      </svg>
    )
  }
  return (
    <svg {...common}>
      <rect x="2" y="4" width="20" height="14" rx="2" />
      <path d="M8 21h8M12 18v3" />
    </svg>
  )
}
