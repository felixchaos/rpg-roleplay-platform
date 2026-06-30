/**
 * Candlelit Grimoire — the visual language of the app.
 * Deep ink canvas, candle-ember warmth, arcane-violet highlights.
 */

export const palette = {
  ink900: "#0b0907",
  ink850: "#110d0a",
  ink800: "#17120d",
  ink750: "#1e1813",
  ink700: "#262019",
  ink600: "#332a20",
  parchment: "#e9dcc2",
  parchmentDim: "#b6a384",
  parchmentFaint: "#7d6f59",

  ember: "#e8923a",
  emberBright: "#ffb55c",
  emberDeep: "#a85a1c",
  ember15: "rgba(232,146,58,0.15)",
  ember08: "rgba(232,146,58,0.08)",

  arcane: "#9d7bd8",
  arcaneDim: "#6f57a0",
  arcane12: "rgba(157,123,216,0.12)",

  blood: "#b8453a",
  jade: "#6fae87",

  hairline: "rgba(233,220,194,0.10)",
  hairlineStrong: "rgba(233,220,194,0.18)",
  scrim: "rgba(5,4,3,0.72)",

  // Shared semi-transparent sheet/drawer backgrounds
  scrimSheet: "rgba(20,16,12,0.86)",
  scrimCard: "rgba(20,16,12,0.96)",
  scrimCard90: "rgba(20,16,12,0.90)",
  scrimDeep: "rgba(18,14,10,0.94)",

  // Blood-tinted translucent layers (conditions, danger borders)
  blood15: "rgba(184,69,58,0.15)",
  blood40: "rgba(184,69,58,0.4)",
};

export const theme = {
  color: {
    bg: palette.ink900,
    bgElevated: palette.ink800,
    bgCard: palette.ink750,
    bgInput: palette.ink700,
    surfaceLine: palette.hairline,
    surfaceLineStrong: palette.hairlineStrong,

    text: palette.parchment,
    textDim: palette.parchmentDim,
    textFaint: palette.parchmentFaint,

    accent: palette.ember,
    accentBright: palette.emberBright,
    accentDeep: palette.emberDeep,
    accentSoft: palette.ember15,
    accentGhost: palette.ember08,

    magic: palette.arcane,
    magicDim: palette.arcaneDim,
    magicSoft: palette.arcane12,

    danger: palette.blood,
    success: palette.jade,
    scrim: palette.scrim,
  },
  font: {
    display: "Cinzel_700Bold",
    displaySemi: "Cinzel_600SemiBold",
    prose: "Spectral_400Regular",
    proseItalic: "Spectral_400Regular_Italic",
    proseMedium: "Spectral_500Medium",
    proseSemi: "Spectral_600SemiBold",
    mono: "JetBrainsMono_400Regular",
  },
  size: {
    xs: 11,
    sm: 13,
    base: 15,
    md: 16,
    lg: 19,
    xl: 24,
    xxl: 32,
    display: 40,
  },
  space: (n: number) => n * 4,
  radius: {
    sm: 8,
    md: 12,
    lg: 18,
    xl: 26,
    pill: 999,
  },
} as const;

export type Theme = typeof theme;
