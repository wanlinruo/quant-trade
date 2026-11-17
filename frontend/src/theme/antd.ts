import { theme, type ThemeConfig } from 'antd'
import { palette, type Mode } from './palette'

export function antdTheme(mode: Mode): ThemeConfig {
  const p = palette[mode]
  const dark = mode === 'dark'
  return {
    algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      colorPrimary: p.accent, colorSuccess: p.ok, colorWarning: p.warn, colorError: p.bad, colorInfo: p.info,
      colorBgLayout: p.bg, colorBgContainer: p.panel, colorBgElevated: p.panel2,
      colorBorder: p.border, colorBorderSecondary: p.border,
      colorText: p.text, colorTextHeading: p.head, colorTextSecondary: p.muted,
      borderRadius: 4, fontSize: 13,
      fontFamily: '-apple-system, "Inter", "PingFang SC", "Microsoft YaHei", sans-serif',
    },
    components: {
      Layout: { siderBg: p.bg, headerBg: p.bg, bodyBg: p.bg, headerHeight: 44, headerPadding: '0 16px', triggerBg: p.panel, triggerColor: p.muted, triggerHeight: 36 },
      Menu: {
        itemBg: p.bg, subMenuItemBg: p.bg, itemHeight: 32, itemMarginBlock: 1, groupTitleFontSize: 11,
        itemSelectedBg: dark ? '#ffffff12' : '#0000000a', itemSelectedColor: p.head, itemColor: p.text,
        itemHoverBg: dark ? '#ffffff0a' : '#00000006', groupTitleColor: p.muted, itemBorderRadius: 3,
      },
      Table: { cellPaddingBlockSM: 6, headerBg: p.panel2, rowHoverBg: dark ? '#ffffff08' : '#00000005' },
      Card: { headerHeightSM: 38, paddingSM: 12 },
    },
  }
}
