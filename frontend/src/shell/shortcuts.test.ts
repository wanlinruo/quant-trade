import { createShortcutHandler, type ShortcutActions } from './shortcuts'

function setup() {
  let t = 1000
  const calls: string[] = []
  const actions: ShortcutActions = {
    navigate: (k) => calls.push(`go:${k}`), openSearch: () => calls.push('search'), openHelp: () => calls.push('help'),
    toggleSider: () => calls.push('sider'), toggleTheme: () => calls.push('theme'), refresh: () => calls.push('refresh'),
  }
  const handle = createShortcutHandler(actions, () => t)
  const press = (key: string, target: EventTarget = document.body, mods: KeyboardEventInit = {}) => {
    const e = new KeyboardEvent('keydown', { key, cancelable: true, ...mods })
    Object.defineProperty(e, 'target', { value: target })
    handle(e)
    return e
  }
  return { calls, press, advance: (ms: number) => { t += ms } }
}

test('G then O navigates to /app/orders; ? opens help; shortcuts ignored inside inputs', () => {
  const { calls, press } = setup()
  press('g'); press('o')
  expect(calls).toEqual(['go:orders'])
  press('?')
  expect(calls).toContain('help')
  const input = document.createElement('input')
  document.body.appendChild(input)
  calls.length = 0
  press('g', input); press('o', input); press('?', input); press('t', input)
  expect(calls).toEqual([])
})

test('G sequence expires after one second', () => {
  const { calls, press, advance } = setup()
  press('g'); advance(1500); press('o')
  expect(calls).toEqual([])
})

test('cmd/ctrl+K and / open search, other single keys map to actions', () => {
  const { calls, press } = setup()
  const e = press('k', document.body, { metaKey: true })
  expect(e.defaultPrevented).toBe(true)
  press('/'); press('['); press('t'); press('r')
  expect(calls).toEqual(['search', 'search', 'sider', 'theme', 'refresh'])
})

test('modifier combos other than cmd/ctrl+K are ignored', () => {
  const { calls, press } = setup()
  press('r', document.body, { metaKey: true })
  press('t', document.body, { ctrlKey: true })
  expect(calls).toEqual([])
})
