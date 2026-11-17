import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { antdTheme } from '../theme/antd'
import { ModeProvider } from '../theme/context'
import { palette, type Mode } from '../theme/palette'

export type ThemePref = 'system' | Mode
const KEY = 'qs-theme'
const QUERY = '(prefers-color-scheme: light)'

const systemMode = (): Mode => (window.matchMedia?.(QUERY).matches ? 'light' : 'dark')
const storedPref = (): ThemePref => {
  const v = localStorage.getItem(KEY)
  return v === 'dark' || v === 'light' || v === 'system' ? v : 'system'
}

interface ThemeCtx { mode: Mode; pref: ThemePref; system: Mode; setPref(p: ThemePref): void; toggle(): void }
const Ctx = createContext<ThemeCtx | null>(null)

export function useTheme(): ThemeCtx {
  const c = useContext(Ctx)
  if (!c) throw new Error('useTheme 必须在 ThemeProvider 内使用')
  return c
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [pref, setPrefState] = useState<ThemePref>(storedPref)
  const [system, setSystem] = useState<Mode>(systemMode)
  const mode: Mode = pref === 'system' ? system : pref

  useEffect(() => {
    const m = window.matchMedia?.(QUERY)
    const f = () => setSystem(systemMode())
    m?.addEventListener?.('change', f)
    return () => m?.removeEventListener?.('change', f)
  }, [])

  useEffect(() => {
    const p = palette[mode]
    document.body.style.background = p.bg
    document.documentElement.dataset.mode = mode
    document.documentElement.style.setProperty('--qs-accent', p.accent)
    document.documentElement.style.setProperty('--qs-border', p.border)
  }, [mode])

  const value = useMemo<ThemeCtx>(() => {
    const setPref = (p: ThemePref) => { localStorage.setItem(KEY, p); setPrefState(p) }
    return { mode, pref, system, setPref, toggle: () => setPref(mode === 'dark' ? 'light' : 'dark') }
  }, [mode, pref, system])
  const cfg = useMemo(() => antdTheme(mode), [mode])

  return (
    <Ctx.Provider value={value}>
      <ModeProvider value={mode}>
        <ConfigProvider locale={zhCN} theme={cfg}>
          <AntApp>{children}</AntApp>
        </ConfigProvider>
      </ModeProvider>
    </Ctx.Provider>
  )
}
