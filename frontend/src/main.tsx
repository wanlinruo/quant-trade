import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { antdTheme } from './theme/antd'
import { ModeProvider } from './theme/context'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ModeProvider value="dark">
      <ConfigProvider locale={zhCN} theme={antdTheme('dark')}>
        <AntApp>qsys</AntApp>
      </ConfigProvider>
    </ModeProvider>
  </StrictMode>,
)
