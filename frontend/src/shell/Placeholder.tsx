import { Button, Result } from 'antd'
import type { NavPage } from './nav'

/** 新页面上线前的占位：说明期次，并能回到现有页面。 */
export function Placeholder({ page }: { page: NavPage }) {
  return (
    <Result status="info" title={`「${page.label}」在第 ${page.phase} 期上线`}
      subTitle={page.legacy ? '新版尚未完成，可先使用现有页面。' : '这是新增页面，现有系统没有对应页面。'}
      extra={page.legacy ? <Button type="primary" href={page.legacy}>打开现有页面 →</Button> : undefined} />
  )
}
