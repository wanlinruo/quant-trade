import { useCallback, useRef, useState, type ReactNode } from 'react'
import { Modal } from 'antd'

export const NOT_LIVE = '不影响实盘：只改动研究侧（策略工作台、回测队列或研究任务），不改变实盘与模拟交易状态。'

/**
 * 写操作确认框（§6.3）：写明影响范围，并固定注明“不影响实盘”。
 * 用法：const { confirm, holder } = useConfirmAction(); if (await confirm('标题', '影响')) …；把 holder 放进页面。
 */
export function useConfirmAction(): { confirm(title: string, impact: ReactNode): Promise<boolean>; holder: ReactNode } {
  const [state, setState] = useState<{ title: string; impact: ReactNode } | null>(null)
  const resolver = useRef<((ok: boolean) => void) | null>(null)
  const confirm = useCallback((title: string, impact: ReactNode) => new Promise<boolean>((resolve) => {
    resolver.current = resolve
    setState({ title, impact })
  }), [])
  const close = (ok: boolean) => { resolver.current?.(ok); resolver.current = null; setState(null) }
  const holder = (
    <Modal open={!!state} title={state?.title} okText="确认" cancelText="取消" onOk={() => close(true)} onCancel={() => close(false)} destroyOnHidden>
      <div>{state?.impact}</div>
      <div style={{ marginTop: 8, fontSize: 12, opacity: 0.8 }}>{NOT_LIVE}</div>
    </Modal>
  )
  return { confirm, holder }
}
