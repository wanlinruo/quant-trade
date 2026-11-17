import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { AppShell } from './shell/AppShell'
import { ThemeProvider } from './shell/ThemeProvider'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <AppShell />
    </ThemeProvider>
  </StrictMode>,
)
