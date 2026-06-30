import React from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  TextInputProps,
  View,
  ViewStyle,
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { useRouter } from "expo-router";
import { theme } from "@/theme/theme";

/** Candle-ember primary button with a gradient face and soft glow. */
export function EmberButton({
  label,
  onPress,
  loading,
  disabled,
  variant = "solid",
  style,
}: {
  label: string;
  onPress: () => void;
  loading?: boolean;
  disabled?: boolean;
  variant?: "solid" | "ghost";
  style?: ViewStyle;
}) {
  const off = disabled || loading;
  if (variant === "ghost") {
    return (
      <Pressable
        onPress={onPress}
        disabled={off}
        accessibilityRole="button"
        accessibilityLabel={label}
        style={({ pressed }) => [
          styles.ghost,
          pressed && { opacity: 0.6 },
          off && { opacity: 0.4 },
          style,
        ]}
      >
        <Text style={styles.ghostLabel}>{label}</Text>
      </Pressable>
    );
  }
  return (
    <Pressable
      onPress={onPress}
      disabled={off}
      accessibilityRole="button"
      accessibilityLabel={label}
      style={({ pressed }) => [{ opacity: off ? 0.5 : pressed ? 0.92 : 1 }, style]}
    >
      <LinearGradient
        colors={[theme.color.accentBright, theme.color.accent, theme.color.accentDeep]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={styles.solid}
      >
        {loading ? (
          <ActivityIndicator color={theme.color.bg} />
        ) : (
          <Text style={styles.solidLabel}>{label}</Text>
        )}
      </LinearGradient>
    </Pressable>
  );
}

export function Field({
  label,
  style,
  ...props
}: TextInputProps & { label?: string }) {
  return (
    <View style={{ gap: theme.space(2) }}>
      {label ? <Text style={styles.fieldLabel}>{label}</Text> : null}
      <TextInput
        placeholderTextColor={theme.color.textFaint}
        style={[styles.input, style]}
        accessibilityLabel={label || "text-input"}
        {...props}
      />
    </View>
  );
}

/** Hairline divider with an arcane diamond center — a small grimoire flourish. */
export function RuneDivider() {
  return (
    <View style={styles.dividerRow}>
      <View style={styles.dividerLine} />
      <View style={styles.diamond} />
      <View style={styles.dividerLine} />
    </View>
  );
}

/**
 * SafeBackButton — a chevron-back tap target that handles the edge case of an empty
 * history (e.g. tab navigation cleared the stack, or deep link landed cold). Falls back
 * to a sensible home route instead of throwing or no-op'ing into a confused state.
 */
export function SafeBackButton({ fallback = "/(app)/chats", style }: { fallback?: string; style?: ViewStyle }) {
  const router = useRouter();
  const onBack = () => {
    try {
      if (typeof (router as any).canGoBack === "function" ? (router as any).canGoBack() : true) {
        router.back();
        return;
      }
    } catch {
      /* swallow — fall through to replace */
    }
    router.replace(fallback as any);
  };
  return (
    <Pressable onPress={onBack} hitSlop={12} style={[styles.safeBackBtn, style]} accessibilityRole="button" accessibilityLabel={"返回"}>
      <Text style={styles.safeBackGlyph}>‹</Text>
    </Pressable>
  );
}

/**
 * IconLabelButton — a small two-row tap target: glyph above, label beneath. Use anywhere a
 * naked icon would otherwise leave the user guessing what it does. Active state lights the
 * glyph ember-bright and tints the label accent.
 */
export function IconLabelButton({
  glyph,
  label,
  onPress,
  active,
  disabled,
  hitSlop = 8,
  style,
}: {
  glyph: string;
  label: string;
  onPress: () => void;
  active?: boolean;
  disabled?: boolean;
  hitSlop?: number;
  style?: ViewStyle;
}) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      hitSlop={hitSlop}
      accessibilityRole="button"
      accessibilityLabel={label}
      style={({ pressed }) => [styles.iconLabelSlot, pressed && { opacity: 0.6 }, disabled && { opacity: 0.35 }, style]}
    >
      <Text style={[styles.iconLabelGlyph, active && styles.iconLabelGlyphActive]}>{glyph}</Text>
      <Text style={[styles.iconLabelText, active && styles.iconLabelTextActive]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  solid: {
    height: 52,
    borderRadius: theme.radius.md,
    alignItems: "center",
    justifyContent: "center",
    shadowColor: theme.color.accent,
    shadowOpacity: 0.45,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 6 },
    elevation: 8,
  },
  solidLabel: {
    fontFamily: theme.font.displaySemi,
    fontSize: theme.size.md,
    letterSpacing: 1.5,
    color: theme.color.bg,
    textTransform: "uppercase",
  },
  ghost: {
    height: 50,
    borderRadius: theme.radius.md,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: theme.color.surfaceLineStrong,
  },
  ghostLabel: {
    fontFamily: theme.font.proseSemi,
    fontSize: theme.size.base,
    color: theme.color.textDim,
    letterSpacing: 0.5,
  },
  fieldLabel: {
    fontFamily: theme.font.displaySemi,
    fontSize: theme.size.xs,
    letterSpacing: 2,
    textTransform: "uppercase",
    color: theme.color.textFaint,
  },
  input: {
    backgroundColor: theme.color.bgInput,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    paddingHorizontal: theme.space(4),
    paddingVertical: theme.space(3.5),
    color: theme.color.text,
    fontFamily: theme.font.prose,
    fontSize: theme.size.md,
  },
  dividerRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: theme.space(3),
  },
  dividerLine: { flex: 1, height: 1, backgroundColor: theme.color.surfaceLine },
  diamond: {
    width: 7,
    height: 7,
    backgroundColor: theme.color.accent,
    transform: [{ rotate: "45deg" }],
    opacity: 0.7,
  },
  iconLabelSlot: {
    alignItems: "center",
    justifyContent: "center",
    minWidth: 48,
    paddingHorizontal: 6,
    paddingVertical: 4,
    gap: 2,
  },
  iconLabelGlyph: {
    fontSize: 19,
    color: theme.color.textDim,
    lineHeight: 22,
  },
  iconLabelGlyphActive: {
    color: theme.color.accentBright,
    textShadowColor: theme.color.accent,
    textShadowRadius: 6,
  },
  iconLabelText: {
    fontFamily: theme.font.proseSemi,
    fontSize: 9.5,
    letterSpacing: 1,
    color: theme.color.textFaint,
  },
  iconLabelTextActive: {
    color: theme.color.accent,
  },
  safeBackBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  safeBackGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
});

