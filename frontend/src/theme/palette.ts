export type Mode = 'dark' | 'light'

export interface Palette {
  bg: string; panel: string; panel2: string; border: string; grid: string
  text: string; muted: string; head: string
  up: string; down: string
  ok: string; warn: string; bad: string; idle: string; info: string; running: string
  accent: string; line: string; line2: string
}

/** 石墨风格（设计文档 §6）。状态靠“符号+文字”区分，A 股惯例涨红跌绿。 */
export const palette: Record<Mode, Palette> = {
  dark: {
    bg: '#111217', panel: '#181b1f', panel2: '#1f2227', border: '#2c3235', grid: '#2a2e33',
    text: '#ccccdc', muted: '#8e8e9e', head: '#ffffff',
    up: '#f2495c', down: '#73bf69',
    ok: '#73bf69', warn: '#fade2a', bad: '#f2495c', idle: '#3a3f46', info: '#5794f2', running: '#5794f2',
    accent: '#ff9830', line: '#5794f2', line2: '#ff9830',
  },
  light: {
    bg: '#f4f5f5', panel: '#ffffff', panel2: '#fafbfc', border: '#dcdee1', grid: '#eceef0',
    text: '#24292e', muted: '#6c737a', head: '#000000',
    up: '#d1242f', down: '#1a7f37',
    ok: '#1a7f37', warn: '#b78800', bad: '#d1242f', idle: '#d5d8dc', info: '#1f60c4', running: '#1f60c4',
    accent: '#e2711d', line: '#1f60c4', line2: '#e2711d',
  },
}
