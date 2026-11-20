import { theme as antdTheme, type ThemeConfig } from 'antd'

export type Mode = 'dark' | 'light'

/** 风格 ②：Grafana 石墨。状态沿用绿/黄/红（Grafana 阈值色），靠“符号+文字”与涨跌区分。 */
export const palette = {
  dark: {
    bg: '#111217', panel: '#181b1f', panel2: '#1f2227', border: '#2c3235', text: '#ccccdc', head: '#ffffff', muted: '#8e8e9e',
    grid: '#2a2e33', accent: '#ff9830', ok: '#73bf69', warn: '#fade2a', bad: '#f2495c', info: '#5794f2', idle: '#3a3f46',
    up: '#f2495c', down: '#73bf69', line: '#5794f2', line2: '#ff9830',
  },
  light: {
    bg: '#f4f5f5', panel: '#ffffff', panel2: '#fafbfc', border: '#dcdee1', text: '#24292e', head: '#000000', muted: '#6c737a',
    grid: '#eceef0', accent: '#e2711d', ok: '#1a7f37', warn: '#b78800', bad: '#d1242f', info: '#1f60c4', idle: '#d5d8dc',
    up: '#d1242f', down: '#1a7f37', line: '#1f60c4', line2: '#e2711d',
  },
} as const

export type Palette = (typeof palette)[Mode]

export function antdConfig(mode: Mode): ThemeConfig {
  const p = palette[mode]
  return {
    algorithm: mode === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      colorPrimary: p.accent,
      colorSuccess: p.ok,
      colorWarning: p.warn,
      colorError: p.bad,
      colorInfo: p.info,
      colorBgLayout: p.bg,
      colorBgContainer: p.panel,
      colorBgElevated: p.panel2,
      colorBorder: p.border,
      colorBorderSecondary: p.border,
      colorText: p.text,
      colorTextHeading: p.head,
      colorTextSecondary: p.muted,
      borderRadius: 4,
      fontSize: 13,
      fontFamily: '-apple-system, "Inter", "PingFang SC", "Microsoft YaHei", sans-serif',
    },
    components: {
      Layout: { siderBg: p.bg, headerBg: p.bg, bodyBg: p.bg, headerHeight: 44, headerPadding: '0 16px', triggerBg: p.panel, triggerColor: p.muted, triggerHeight: 36 },
      Menu: { itemBg: p.bg, subMenuItemBg: p.bg, itemHeight: 32, itemMarginBlock: 1, groupTitleFontSize: 11, itemSelectedBg: mode === 'dark' ? '#ffffff12' : '#0000000a', itemSelectedColor: p.head, itemColor: p.text, itemHoverBg: mode === 'dark' ? '#ffffff0a' : '#00000006', groupTitleColor: p.muted, itemBorderRadius: 3 },
      Table: { cellPaddingBlockSM: 6, headerBg: p.panel2, rowHoverBg: mode === 'dark' ? '#ffffff08' : '#00000005' },
      Card: { headerHeightSM: 38, paddingSM: 12 },
    },
  }
}
