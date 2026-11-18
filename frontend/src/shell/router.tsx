import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent } from 'react'
import { DEFAULT_PAGE } from './nav'

export const BASE = '/app'

/** /app/<key>[/...] → key；未知或空路径给默认页。 */
export function keyFromPath(pathname: string): string {
  const rest = pathname.startsWith(BASE) ? pathname.slice(BASE.length) : ''
  const key = rest.split('/').filter(Boolean)[0]
  return key || DEFAULT_PAGE
}

export function hrefFor(key: string): string {
  return `${BASE}/${key}`
}

export function navigate(key: string): void {
  const href = hrefFor(key)
  if (location.pathname !== href) history.pushState(null, '', href)
  dispatchEvent(new PopStateEvent('popstate'))
}

export function useRoute(): string {
  const [key, setKey] = useState(() => keyFromPath(location.pathname))
  useEffect(() => {
    const f = () => setKey(keyFromPath(location.pathname))
    addEventListener('popstate', f)
    return () => removeEventListener('popstate', f)
  }, [])
  return key
}

/** 站内链接：普通点击走前端路由；带修饰键或中键时交给浏览器（新标签打开）。 */
export function Link({ to, ...rest }: { to: string } & AnchorHTMLAttributes<HTMLAnchorElement>) {
  const onClick = (e: MouseEvent<HTMLAnchorElement>) => {
    rest.onClick?.(e)
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    navigate(to)
  }
  return <a {...rest} href={hrefFor(to)} onClick={onClick} />
}

/** 当前页面的查询参数（页面内状态写进 URL，便于分享与后退）。 */
export function useSearch(): URLSearchParams {
  const [s, setS] = useState(() => location.search)
  useEffect(() => {
    const f = () => setS(location.search)
    addEventListener('popstate', f)
    return () => removeEventListener('popstate', f)
  }, [])
  return new URLSearchParams(s)
}

/** 改写查询参数（值为 null 删除该参数），保留路径，进入浏览器历史。 */
export function setSearch(params: Record<string, string | null>): void {
  const q = new URLSearchParams(location.search)
  for (const [k, v] of Object.entries(params)) {
    if (v === null) q.delete(k)
    else q.set(k, v)
  }
  const qs = q.toString()
  const href = `${location.pathname}${qs ? `?${qs}` : ''}`
  if (href !== `${location.pathname}${location.search}`) history.pushState(null, '', href)
  dispatchEvent(new PopStateEvent('popstate'))
}

/** 跳到另一页并带上查询参数（如从策略页打开某次回测）。 */
export function goTo(key: string, params: Record<string, string> = {}): void {
  const qs = new URLSearchParams(params).toString()
  history.pushState(null, '', `${hrefFor(key)}${qs ? `?${qs}` : ''}`)
  dispatchEvent(new PopStateEvent('popstate'))
}
