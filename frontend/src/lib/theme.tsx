import { useEffect, useState } from 'react'

export type Theme = 'system' | 'light' | 'dark'
const KEY = 'pv_theme'

function read(): Theme {
  try { const v = localStorage.getItem(KEY); return v === 'light' || v === 'dark' ? v : 'system' } catch { return 'system' }
}

/** System preference by default; an explicit choice sets data-theme on <html> (the tokens in index.css do the rest). */
export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(read)
  useEffect(() => {
    const el = document.documentElement
    if (theme === 'system') el.removeAttribute('data-theme'); else el.setAttribute('data-theme', theme)
    try { theme === 'system' ? localStorage.removeItem(KEY) : localStorage.setItem(KEY, theme) } catch { /* private mode */ }
  }, [theme])
  return [theme, setTheme]
}
