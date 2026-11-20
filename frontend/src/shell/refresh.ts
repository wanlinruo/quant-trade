const fns = new Set<() => void>()

/** 页面登记自己的刷新函数，供 R 快捷键调用；返回注销函数。 */
export function registerPageRefresh(fn: () => void): () => void {
  fns.add(fn)
  return () => { fns.delete(fn) }
}

export function refreshPage(): void {
  for (const f of [...fns]) f()
}
