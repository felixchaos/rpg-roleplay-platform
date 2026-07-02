/**
 * KindlingSplash — boot/loading screen for the app. Shown both while fonts are loading
 * (in _layout.tsx as the very first frame) and as the index route while the auth guard
 * decides where to send the user.
 *
 * Aesthetic: a slow-pulsing ember at the heart of the screen, ringed by a rotating
 * arcane sigil (six runic glyphs on a circle), with the brand mark beneath. Conveys
 * "the grimoire is being lit". Pure animated.View — no images, no asset dependencies,
 * so the same component renders fine even before fonts have loaded (it falls back to
 * the platform default font in that case).
 */
import React, { useEffect } from "react";
import { StyleSheet, Text, View } from "react-native";
import Animated, {
  cancelAnimation,
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withTiming,
} from "react-native-reanimated";
import { theme, palette } from "@/theme/theme";

const RUNES = ["✶", "❖", "✧", "❦", "✦", "❀"];

export function KindlingSplash({ tagline }: { tagline?: string }) {
  // Slow rotation of the rune ring — 18s per revolution, never stops, sets the cadence
  // of the whole screen.
  const rot = useSharedValue(0);
  // Ember pulse — opacity + scale on a 1.6s sine, asymmetric so it never feels mechanical.
  const pulse = useSharedValue(0);

  useEffect(() => {
    rot.value = withRepeat(
      withTiming(360, { duration: 18000, easing: Easing.linear }),
      -1,
      false,
    );
    pulse.value = withRepeat(
      withTiming(1, { duration: 1700, easing: Easing.inOut(Easing.quad) }),
      -1,
      true,
    );
    return () => {
      cancelAnimation(rot);
      cancelAnimation(pulse);
    };
  }, [pulse, rot]);

  const ringStyle = useAnimatedStyle(() => ({ transform: [{ rotate: `${rot.value}deg` }] }));
  const emberStyle = useAnimatedStyle(() => ({
    opacity: 0.55 + pulse.value * 0.45,
    transform: [{ scale: 0.92 + pulse.value * 0.12 }],
  }));

  return (
    <View style={styles.root}>
      <View style={styles.center}>
        <Animated.View style={[styles.ring, ringStyle]} pointerEvents="none">
          {RUNES.map((g, i) => {
            const angle = (i / RUNES.length) * 2 * Math.PI;
            const r = 76;
            const x = Math.cos(angle) * r;
            const y = Math.sin(angle) * r;
            return (
              <Text
                key={i}
                style={[
                  styles.rune,
                  {
                    position: "absolute",
                    transform: [{ translateX: x }, { translateY: y }, { rotate: `${(angle * 180) / Math.PI + 90}deg` }],
                  },
                ]}
              >
                {g}
              </Text>
            );
          })}
        </Animated.View>

        <Animated.View style={[styles.ember, emberStyle]}>
          <View style={styles.emberCore} />
          <View style={styles.emberHalo} />
        </Animated.View>
      </View>

      <View style={styles.brand}>
        <Text style={styles.kicker}>Candlelit Grimoire</Text>
        <Text style={styles.title}>RPG Roleplay</Text>
        {tagline ? <Text style={styles.tagline}>{tagline}</Text> : <Text style={styles.tagline}>正在点燃…</Text>}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: theme.color.bg,
    alignItems: "center",
    justifyContent: "center",
    gap: 56,
  },
  center: {
    width: 200,
    height: 200,
    alignItems: "center",
    justifyContent: "center",
  },
  ring: {
    position: "absolute",
    width: 200,
    height: 200,
    alignItems: "center",
    justifyContent: "center",
  },
  rune: {
    fontSize: 18,
    color: theme.color.accent,
    opacity: 0.85,
    textShadowColor: palette.ember15,
    textShadowRadius: 10,
  },
  ember: { width: 56, height: 56, alignItems: "center", justifyContent: "center" },
  emberCore: {
    position: "absolute",
    width: 14,
    height: 14,
    borderRadius: 7,
    backgroundColor: palette.emberBright,
    shadowColor: palette.ember,
    shadowOpacity: 1,
    shadowRadius: 24,
    shadowOffset: { width: 0, height: 0 },
    elevation: 8,
  },
  emberHalo: {
    position: "absolute",
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: palette.ember15,
  },
  brand: { alignItems: "center", gap: 6, paddingHorizontal: 24 },
  kicker: {
    fontSize: 11,
    letterSpacing: 5,
    color: theme.color.accent,
    textTransform: "uppercase",
    opacity: 0.85,
  },
  title: { fontSize: 28, color: theme.color.text, letterSpacing: 3, fontWeight: "300" },
  tagline: {
    fontSize: 13,
    color: theme.color.textFaint,
    fontStyle: "italic",
    letterSpacing: 0.5,
    marginTop: 8,
  },
});
