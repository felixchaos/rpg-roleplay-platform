/**
 * GM Style — six narrative dials that shape how the Game Master writes. Each knob is
 * 0–100; the backend interpolates them into the prompt harness. We render custom
 * gesture sliders (RN ships none) as ember-filled tracks with a draggable bead, so the
 * screen feels like tuning an arcane instrument rather than filling a form.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Alert, LayoutChangeEvent, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Animated, { FadeInDown, runOnJS } from "react-native-reanimated";
import * as Haptics from "expo-haptics";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { EmberButton } from "@/components/ui";
import { gmStyle } from "@/api";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

// Human labels + the two poles each dial swings between (from the backend KNOBS).
const KNOB_META: Record<string, { label: string; lo: string; hi: string }> = {
  reply_length: { label: "篇幅", lo: "简短", hi: "丰盈" },
  player_action_focus: { label: "镜头焦点", lo: "对方反应", hi: "玩家动作" },
  drama_density: { label: "戏剧密度", lo: "镜像克制", hi: "放大渲染" },
  interiority: { label: "内心戏", lo: "字面处理", hi: "补写潜台词" },
  cliffhanger: { label: "结尾悬念", lo: "平稳收束", hi: "强钩留白" },
  guidance_force: { label: "引导力度", lo: "高自由", hi: "强收束" },
};
const ORDER = ["reply_length", "player_action_focus", "drama_density", "interiority", "cliffhanger", "guidance_force"];

function Dial({ label, lo, hi, value, onChange }: { label: string; lo: string; hi: string; value: number; onChange: (v: number) => void }) {
  const widthRef = useRef(1);
  const [local, setLocal] = useState(value);

  useEffect(() => setLocal(value), [value]);

  const onLayout = (e: LayoutChangeEvent) => {
    widthRef.current = Math.max(1, e.nativeEvent.layout.width);
  };

  const setFromX = useCallback(
    (x: number) => {
      const pct = Math.max(0, Math.min(100, Math.round((x / widthRef.current) * 100)));
      setLocal(pct);
      onChange(pct);
    },
    [onChange],
  );

  const pan = Gesture.Pan()
    .onBegin((e) => runOnJS(setFromX)(e.x))
    .onUpdate((e) => runOnJS(setFromX)(e.x));
  const tap = Gesture.Tap().onEnd((e) => runOnJS(setFromX)(e.x));
  const gesture = Gesture.Simultaneous(pan, tap);

  return (
    <View style={styles.dial}>
      <View style={styles.dialHead}>
        <Text style={styles.dialLabel}>{label}</Text>
        <Text style={styles.dialValue}>{local}</Text>
      </View>
      <GestureDetector gesture={gesture}>
        <View style={styles.trackWrap} onLayout={onLayout} hitSlop={{ top: 14, bottom: 14 }}>
          <View style={styles.track}>
            <View style={[styles.fill, { width: `${local}%` }]} />
          </View>
          <View style={[styles.bead, { left: `${local}%` }]} />
        </View>
      </GestureDetector>
      <View style={styles.poles}>
        <Text style={styles.pole}>{lo}</Text>
        <Text style={styles.pole}>{hi}</Text>
      </View>
    </View>
  );
}

export default function GmStyleScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [values, setValues] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [schema, current] = await Promise.all([gmStyle.schema(), gmStyle.get().catch(() => null)]);
        const defaults = schema?.defaults ?? {};
        setValues({ ...defaults, ...(current?.gm_style ?? {}) });
      } catch (e) {
        if (e instanceof ApiError) Alert.alert("加载失败", e.message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await gmStyle.set(values);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      Alert.alert("已保存", "GM 叙事风格已更新，下次对话即生效。", [{ text: "好", onPress: () => router.back() }]);
    } catch (e) {
      Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setSaving(false);
    }
  };

  const keys = ORDER.filter((k) => k in values).concat(Object.keys(values).filter((k) => !ORDER.includes(k)));

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>GM Style</Text>
          <Text style={styles.h1}>叙事调律</Text>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <ScrollView contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 50, gap: theme.space(5), paddingTop: theme.space(3) }}>
          {keys.map((k, i) => {
            const meta = KNOB_META[k] || { label: k, lo: "低", hi: "高" };
            return (
              <Animated.View key={k} entering={FadeInDown.delay(i * 60).duration(380)}>
                <Dial
                  label={meta.label}
                  lo={meta.lo}
                  hi={meta.hi}
                  value={values[k]}
                  onChange={(v) => setValues((prev) => ({ ...prev, [k]: v }))}
                />
              </Animated.View>
            );
          })}
          <EmberButton label={saving ? "校准中…" : "保存风格"} onPress={save} loading={saving} style={{ marginTop: theme.space(2) }} />
        </ScrollView>
      )}
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  dial: { gap: theme.space(2) },
  dialHead: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between" },
  dialLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  dialValue: { fontFamily: theme.font.mono, fontSize: theme.size.md, color: theme.color.accentBright },
  trackWrap: { height: 28, justifyContent: "center" },
  track: { height: 5, borderRadius: 3, backgroundColor: theme.color.bgInput, borderWidth: 1, borderColor: theme.color.surfaceLine, overflow: "hidden" },
  fill: { height: "100%", backgroundColor: theme.color.accent },
  bead: { position: "absolute", width: 20, height: 20, borderRadius: 10, backgroundColor: theme.color.accentBright, borderWidth: 2, borderColor: theme.color.bg, marginLeft: -10, shadowColor: theme.color.accent, shadowOpacity: 0.7, shadowRadius: 8, elevation: 6 },
  poles: { flexDirection: "row", justifyContent: "space-between" },
  pole: { fontFamily: theme.font.prose, fontSize: theme.size.xs, color: theme.color.textFaint },
});
