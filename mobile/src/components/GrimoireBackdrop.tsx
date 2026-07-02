import React, { useEffect } from "react";
import { StyleSheet, View, ViewProps } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
  cancelAnimation,
} from "react-native-reanimated";
import { theme, palette } from "@/theme/theme";

const AnimatedGradient = Animated.createAnimatedComponent(LinearGradient);

/**
 * A living candle glow. Two shared values drive an organic flicker: a fast, shallow
 * jitter (the flame guttering) layered over a slow swell (the room breathing). Using
 * irregular timing steps keeps it from reading as a clean sine loop.
 */
function CandleGlow() {
  const flicker = useSharedValue(0.82);
  const swell = useSharedValue(1);

  useEffect(() => {
    // Irregular, asymmetric steps → reads as a real flame, not a metronome.
    flicker.value = withRepeat(
      withSequence(
        withTiming(0.95, { duration: 140, easing: Easing.out(Easing.quad) }),
        withTiming(0.7, { duration: 90, easing: Easing.in(Easing.quad) }),
        withTiming(0.88, { duration: 200, easing: Easing.inOut(Easing.quad) }),
        withTiming(0.78, { duration: 110 }),
        withTiming(1, { duration: 170, easing: Easing.out(Easing.cubic) }),
        withTiming(0.83, { duration: 130 }),
      ),
      -1,
      false,
    );
    swell.value = withRepeat(
      withSequence(
        withTiming(1.08, { duration: 2600, easing: Easing.inOut(Easing.sin) }),
        withTiming(0.96, { duration: 3100, easing: Easing.inOut(Easing.sin) }),
      ),
      -1,
      true,
    );
    return () => {
      cancelAnimation(flicker);
      cancelAnimation(swell);
    };
  }, [flicker, swell]);

  const style = useAnimatedStyle(() => ({
    opacity: flicker.value,
    transform: [{ scale: swell.value }],
  }));

  return (
    <Animated.View style={[styles.glowWrap, style]} pointerEvents="none">
      <AnimatedGradient
        colors={[theme.color.accentSoft, "rgba(232,146,58,0.05)", "transparent"]}
        start={{ x: 0.5, y: 0.1 }}
        end={{ x: 0.5, y: 0.9 }}
        style={styles.glow}
      />
    </Animated.View>
  );
}

/**
 * Atmospheric backdrop: layered ink + a flickering candle glow + arcane wash + vignette.
 * No image assets — pure gradients so it stays crisp at any density.
 */
export function GrimoireBackdrop({ children, style, ...rest }: ViewProps) {
  return (
    <View style={[styles.root, style]} {...rest}>
      <LinearGradient
        colors={[palette.ink850, palette.ink900, "#080604"]}
        start={{ x: 0.2, y: 0 }}
        end={{ x: 0.9, y: 1 }}
        style={styles.fill}
      />
      <CandleGlow />
      {/* lower-right arcane wash */}
      <LinearGradient
        colors={["transparent", theme.color.magicSoft]}
        start={{ x: 0.4, y: 0.5 }}
        end={{ x: 1, y: 1 }}
        style={styles.fill}
      />
      <View style={styles.vignette} pointerEvents="none" />
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.color.bg },
  fill: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  glowWrap: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  glow: { position: "absolute", top: "-12%", left: "-10%", right: "-10%", height: "70%" },
  vignette: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    borderWidth: 90,
    borderColor: "rgba(5,4,3,0.55)",
    borderRadius: 1,
  },
});
