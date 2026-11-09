import { ThemeToggle } from './ThemeToggle'

/**
 * The product's own name, not a company logo: the brands this tool posts for
 * appear on their images and in the publish grid, never in the chrome.
 * The glyph is inline SVG on a gradient tile, so it follows the theme tokens
 * with no image files to keep in step.
 */
export function Header({ theme, onCycleTheme }) {
  return (
    <header className="header">
      <div className="header-inner">
        <span className="wordmark">
          <span className="wordmark-glyph" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="16" height="16">
              <path
                d="M21 3 3 10.5l7 2.5 2.5 7L21 3Z"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinejoin="round"
              />
              <path d="m10 13 4.5-4.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </span>
          <span className="wordmark-text">
            Post <strong>Studio</strong>
          </span>
        </span>
        <ThemeToggle theme={theme} onCycle={onCycleTheme} />
      </div>
    </header>
  )
}
