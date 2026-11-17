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
