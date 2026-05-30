// Cloudscape 暖色暗主题 —— 把项目 tokens.css 的暖灰暗色板映射到 Cloudscape 设计令牌。
// 强制 Dark visual mode,再用 applyTheme 覆盖颜色/字体/圆角令牌为暖色。
// 这样得到「AWS 控制台架构 + 你的暖色主题」。
import { applyMode, Mode, applyDensity, Density } from '@cloudscape-design/global-styles';
import { applyTheme } from '@cloudscape-design/components/theming';

// 暖色板(对齐 src/tokens.css)
const C = {
  bg: '#1a1817',
  bgDeep: '#131211',
  panel: '#211f1d',
  panel2: '#282623',
  panel3: '#2f2c28',
  line: '#36322d',
  lineSoft: '#2a2724',
  lineStrong: '#4a4540',
  text: '#ebe7df',
  textQuiet: '#c8c2b7',
  muted: '#968f85',
  accent: '#c96442',
  accentHover: '#b6593b',
  accentActive: '#a44f34',
  accentSoft: 'rgba(201, 100, 66, 0.16)',
};

// 颜色令牌:同时给 light/dark(我们恒为 dark,但双写更稳)
const dual = (v) => ({ light: v, dark: v });

const theme = {
  tokens: {
    // 字体:沿用项目现有中文字体 + serif 叙事标题
    fontFamilyBase: "'Noto Sans SC', system-ui, -apple-system, sans-serif",
    fontFamilyHeading: "'Noto Serif SC', 'Noto Sans SC', serif",

    // ── 字号阶梯整体调小(中文 + Cloudscape 默认偏大,挤压密度)──────────
    fontSizeBodyM: '13px', lineHeightBodyM: '19px',
    fontSizeBodyS: '12px', lineHeightBodyS: '16px',
    fontSizeHeadingXl: '19px', lineHeightHeadingXl: '25px',   // 页面标题 H1
    fontSizeHeadingL: '16px', lineHeightHeadingL: '21px',     // 容器标题 H2
    fontSizeHeadingM: '14px', lineHeightHeadingM: '19px',     // 卡片/区块 H3
    fontSizeHeadingS: '13px', lineHeightHeadingS: '18px',
    fontSizeHeadingXs: '12.5px', lineHeightHeadingXs: '17px',
    fontSizeDisplayL: '28px', lineHeightDisplayL: '34px',
    fontSizeFormLabel: '12.5px', lineHeightFormLabel: '17px',
    fontSizeTabs: '13px', lineHeightTabs: '18px',
    fontSizeKeyValuePairsLabel: '11.5px', lineHeightKeyValuePairsLabel: '16px',

    // ── 背景层 ──────────────────────────────────────────────
    colorBackgroundLayoutMain: dual(C.bg),
    colorBackgroundContainerContent: dual(C.panel),
    colorBackgroundContainerHeader: dual(C.panel),
    colorBackgroundLayoutPanelContent: dual(C.bg),
    colorBackgroundHomeHeader: dual(C.bgDeep),
    colorBackgroundInputDefault: dual(C.bgDeep),
    colorBackgroundDropdownItemDefault: dual(C.panel),
    colorBackgroundDropdownItemHover: dual(C.panel2),
    colorBackgroundDropdownItemSelected: dual(C.accentSoft),
    colorBackgroundItemSelected: dual(C.accentSoft),
    colorBackgroundCellShaded: dual(C.panel2),
    colorBackgroundButtonNormalDefault: dual(C.panel2),
    colorBackgroundButtonNormalHover: dual(C.panel3),
    colorBackgroundButtonNormalActive: dual(C.panel3),
    // 普通按钮文字/图标:用暖色文字,别留 AWS 蓝
    colorTextButtonNormalDefault: dual(C.text),
    colorTextButtonNormalHover: dual(C.text),
    colorTextButtonNormalActive: dual(C.text),
    colorTextButtonLinkDefault: dual(C.accent),
    colorTextButtonLinkHover: dual(C.accentHover),
    colorTextButtonLinkActive: dual(C.accentActive),
    colorBackgroundControlChecked: dual(C.accent),
    colorBackgroundControlDefault: dual(C.bgDeep),
    colorBackgroundLayoutToggleDefault: dual(C.panel2),
    colorBackgroundLayoutToggleHover: dual(C.panel3),

    // ── 文本 ────────────────────────────────────────────────
    colorTextBodyDefault: dual(C.text),
    colorTextHeadingDefault: dual(C.text),
    colorTextBodySecondary: dual(C.muted),
    colorTextLabel: dual(C.textQuiet),
    colorTextFormLabel: dual(C.textQuiet),
    colorTextFormSecondary: dual(C.muted),
    colorTextGroupLabel: dual(C.muted),
    colorTextDropdownItemDefault: dual(C.text),
    colorTextAccent: dual(C.accent),
    colorTextInteractiveDefault: dual(C.textQuiet),
    colorTextInteractiveHover: dual(C.text),
    colorTextInteractiveActive: dual(C.text),
    colorTextLinkDefault: dual(C.accent),
    colorTextLinkHover: dual(C.accentHover),

    // ── 主按钮(强调色) ────────────────────────────────────
    colorBackgroundButtonPrimaryDefault: dual(C.accent),
    colorBackgroundButtonPrimaryHover: dual(C.accentHover),
    colorBackgroundButtonPrimaryActive: dual(C.accentActive),
    colorTextButtonPrimaryDefault: dual('#ffffff'),
    colorTextButtonPrimaryHover: dual('#ffffff'),
    colorTextButtonPrimaryActive: dual('#ffffff'),
    colorBorderButtonPrimaryDefault: dual(C.accent),
    colorBorderButtonPrimaryHover: dual(C.accentHover),

    // ── 边框 ────────────────────────────────────────────────
    colorBorderDividerDefault: dual(C.line),
    colorBorderInputDefault: dual(C.lineStrong),
    colorBorderButtonNormalDefault: dual(C.lineStrong),
    colorBorderButtonNormalHover: dual(C.muted),
    colorBorderDropdownItemDefault: dual(C.line),
    colorBorderItemFocused: dual(C.accent),
    colorBorderItemSelected: dual(C.accent),          // 表格/卡片选中行描边 → 暖色(原 AWS 蓝)
    colorBorderItemCardHighlighted: dual(C.accent),
    // 分段控件(SegmentedControl)选中态 → 暖色(原 AWS 蓝)
    colorBackgroundSegmentActive: dual(C.accent),
    colorBorderSegmentActive: dual(C.accent),
    colorTextSegmentActive: dual('#ffffff'),
    colorBackgroundToggleButtonNormalPressed: dual(C.accentSoft),

    // ── 圆角 ────────────────────────────────────────────────
    borderRadiusContainer: '12px',
    borderRadiusButton: '8px',
    borderRadiusInput: '8px',
    borderRadiusDropdown: '10px',
    borderRadiusItem: '6px',
    borderRadiusBadge: '999px',
  },
  // 顶栏(TopNavigation)走独立 context,默认是 AWS 冷灰深色 —— 单独染暖
  contexts: {
    'top-navigation': {
      tokens: {
        colorBackgroundContainerContent: C.bgDeep,
        colorBackgroundContainerHeader: C.bgDeep,
        colorBackgroundDropdownItemDefault: C.panel,
        colorBackgroundDropdownItemHover: C.panel2,
        // 下拉选中项 / 搜索框 → 暖色(原 AWS 冷蓝默认)
        colorBackgroundDropdownItemSelected: C.accentSoft,
        colorBackgroundItemSelected: C.accentSoft,
        colorBackgroundInputDefault: C.bgDeep,
        colorBackgroundButtonNormalDefault: C.panel2,
        colorBackgroundButtonNormalHover: C.panel3,
        colorBackgroundButtonNormalActive: C.panel3,
        colorBorderDividerDefault: C.line,
        colorBorderInputDefault: C.lineStrong,
        colorBorderInputFocused: C.accent,
        colorBorderItemFocused: C.accent,
        colorBorderButtonNormalDefault: C.lineStrong,
        colorTextBodyDefault: C.text,
        colorTextInteractiveDefault: C.textQuiet,
        colorTextInteractiveHover: C.text,
        colorTextAccent: C.accent,
        colorTextLinkDefault: C.accent,
        colorTextDropdownItemDefault: C.text,
        colorTextButtonNormalDefault: C.text,
        colorTextButtonNormalHover: C.text,
      },
    },
  },
};

let _reset = null;

export function installWarmTheme() {
  applyMode(Mode.Dark);
  applyDensity(Density.Compact); // 紧凑密度:更小行高/内边距,提升信息密度
  try {
    const r = applyTheme({ theme });
    _reset = r && r.reset;
  } catch (e) {
    // 令牌名漂移时不致命:打日志,保留 Cloudscape 默认暗色
    console.error('[cloudscape-theme] applyTheme failed:', e);
  }
}

export function resetWarmTheme() {
  if (_reset) _reset();
}
