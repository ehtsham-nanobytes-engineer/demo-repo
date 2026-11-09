import { useCallback, useEffect, useState } from 'react'

const KEY = 'post-studio-theme'

/**
 * Light / dark / system, remembered per browser.
 *
 * "system" is the default and writes no attribute, which lets the CSS media
 * query decide. Picking light or dark stamps `data-theme` on <html>, and those
 * rules are written to win over the media query in both directions.
 */
export function useTheme() {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem(KEY) || 'system'
    } catch {
      return 'system' // private windows and blocked site data both throw here
    }
  })

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      localStorage.setItem(KEY, theme)
    } catch {
      /* not being able to remember the choice is not worth breaking the page */
    }
  }, [theme])

  /** Cycle light → dark → system, which is the order the icons read in. */
  const cycle = useCallback(
    () => setTheme((t) => (t === 'light' ? 'dark' : t === 'dark' ? 'system' : 'light')),
    [],
  )

  return { theme, setTheme, cycle }
}
