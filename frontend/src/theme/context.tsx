import { createContext, useContext } from 'react'
import { palette, type Mode, type Palette } from './palette'

const ModeContext = createContext<Mode>('dark')

export const ModeProvider = ModeContext.Provider

export function useMode(): Mode {
  return useContext(ModeContext)
}

export function usePalette(): Palette {
  return palette[useContext(ModeContext)]
}
