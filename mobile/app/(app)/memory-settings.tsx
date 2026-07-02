/**
 * Memory Tuning — 记忆调律. The scribe's chamber for setting how the engine remembers.
 *
 * Aesthetic direction: Candlelit Grimoire, refined-minimalist register (not the playful
 * energy of the gm-style dials). These are operational parameters, so the visual leans
 * contemplative: each row reads as a measuring vial in the grimoire — runic glyph at left,
 * one-line prose explanation, current value in brass-numeral display weight, thin ember
 * trough beneath. The three memory buckets are rendered as 三盏香 (three censers) — wax
 * tablets bearing a flame glyph that burns or smolders based on the bucket's state.
 *
 * All values persist as user-preference keys (no dedicated endpoint), so changes are
 * optimistically applied locally and patched to /api/me/preference. Saved values follow
 * across clients, matching desktop behavior.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  LayoutChangeEvent,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Animated, { FadeIn, FadeInDown, runOnJS } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { prefs } from "@/api";
import { ApiError } from "@/api/http";
import { theme, palette } from "@/theme/theme";

// Each parameter is a measuring vial: a glyph, a prose explanation, and a numeric range
// keyed to a user-preference. Steps are chosen to match the documented ranges exactly.
type Vial = {
  key: string;
  glyph: string;
  label: string;
  prose: string;
  min: number;
  max: number;
  step: number;
  fallback: number;
  unit?: string;
};

const VIALS_RECALL: Vial[] = [
  {
    key: "recall_depth",
    glyph: "⌬",
    label: "召回深度",
    prose: "每轮从原文抽取的最大段数。越深，GM 记得越远；越浅，回合越轻。",
    min: 2,
    max: 20,
    step: 1,
    fallback: 6,
    unit: "段",
  },
  {
    key: "summary_window",
    glyph: "❦",
    label: "摘要窗口",
    prose: "最近 N 个回合压缩为一段长期记忆，喂入下一次召唤。",
    min: 3,
    max: 20,
    step: 1,
    fallback: 8,
    unit: "回合",
  },
  {
    key: "token_budget",
    glyph: "✺",
    label: "注入预算",
    prose: "每轮注入到 GM 上下文的记忆 token 上限。预算越高，记得越多，调用更贵。",
    min: 200,
    max: 2000,
    step: 50,
    fallback: 800,
    unit: "tokens",
  },
];

const VIALS_ARCHIVE: Vial[] = [
  {
    key: "auto_archive_after_turns",
    glyph: "⎈",
    label: "自动归档",
    prose: "超过此回合数的记忆，自动从「每轮注入」降级为「按相关性召回」。",
    min: 10,
    max: 200,
    step: 5,
    fallback: 50,
    unit: "回合后",
  },
  {
    key: "pinned_max",
    glyph: "✦",
    label: "固定上限",
    prose: "固定记忆桶最多容纳的条目。超出时最旧的固定记忆自动转入事实库。",
    min: 5,
    max: 100,
    step: 1,
    fallback: 20,
    unit: "条",
  },
];

const CENSERS: { key: string; label: string; whisper: string }[] = [
  {
    key: "bucket_pinned_enabled",
    label: "固定记忆",
    whisper: "每轮必然注入。绝不能忘的事，留在此处。",
  },
  {
    key: "bucket_world_enabled",
    label: "世界记忆",
    whisper: "按相关性召回。世界观、地点、派系的背景知识。",
  },
  {
    key: "bucket_character_enabled",
    label: "角色记忆",
    whisper: "按相关性召回。人物关系、NPC 状态、性格细节。",
  },
];

function MemoryVial({
  vial,
  value,
  onChange,
}: {
  vial: Vial;
  value: number;
  onChange: (v: number) => void;
}) {
  const widthRef = useRef(1);
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);

  const setFromX = useCallback(
    (x: number) => {
      const frac = Math.max(0, Math.min(1, x / widthRef.current));
      const raw = vial.min + frac * (vial.max - vial.min);
      const snapped = Math.round(raw / vial.step) * vial.step;
      const clamped = Math.max(vial.min, Math.min(vial.max, snapped));
      setLocal(clamped);
      onChange(clamped);
    },
    [vial, onChange],
  );

  const onLayout = (e: LayoutChangeEvent) => {
    widthRef.current = Math.max(1, e.nativeEvent.layout.width);
  };

  const pan = Gesture.Pan()
    .onBegin((e) => runOnJS(setFromX)(e.x))
    .onUpdate((e) => runOnJS(setFromX)(e.x));
  const tap = Gesture.Tap().onEnd((e) => runOnJS(setFromX)(e.x));
  const gesture = Gesture.Simultaneous(pan, tap);

  const pct = ((local - vial.min) / (vial.max - vial.min)) * 100;
  // Numerals are scribed in brass: thousands get a comma for the budget vial.
  const display = local.toLocaleString("en-US");

  return (
    <View style={styles.vial}>
      <View style={styles.vialHead}>
        <Text style={styles.vialGlyph}>{vial.glyph}</Text>
        <View style={{ flex: 1 }}>
          <View style={styles.vialNameRow}>
            <Text style={styles.vialLabel}>{vial.label}</Text>
            <View style={styles.brassRow}>
              <Text style={styles.brassNumeral}>{display}</Text>
              {vial.unit ? <Text style={styles.brassUnit}> {vial.unit}</Text> : null}
            </View>
          </View>
          <Text style={styles.vialProse}>{vial.prose}</Text>
        </View>
      </View>
      <GestureDetector gesture={gesture}>
        <View style={styles.troughWrap} onLayout={onLayout} hitSlop={{ top: 12, bottom: 12 }}>
          <View style={styles.trough}>
            <View style={[styles.troughFill, { width: `${pct}%` }]} />
          </View>
        </View>
      </GestureDetector>
    </View>
  );
}

function Censer({
  label,
  whisper,
  lit,
  onToggle,
}: {
  label: string;
  whisper: string;
  lit: boolean;
  onToggle: () => void;
}) {
  return (
    <Pressable onPress={onToggle} style={({ pressed }) => [styles.censer, lit && styles.censerLit, pressed && { opacity: 0.85 }]}>
      <View style={styles.censerHead}>
        <Text style={[styles.censerFlame, lit && styles.censerFlameLit]}>{lit ? "🜂" : "○"}</Text>
        <Text style={[styles.censerLabel, lit && { color: theme.color.accentBright }]}>{label}</Text>
        <Text style={[styles.censerState, lit ? { color: palette.jade } : { color: theme.color.textFaint }]}>
          {lit ? "燃" : "熄"}
        </Text>
      </View>
      <Text style={styles.censerWhisper}>{whisper}</Text>
    </Pressable>
  );
}

export default function MemorySettingsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [values, setValues] = useState<Record<string, number | boolean>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const r = await prefs.get();
        const pv: Record<string, any> = r?.preferences ?? {};
        const seed: Record<string, number | boolean> = {};
        for (const v of [...VIALS_RECALL, ...VIALS_ARCHIVE]) {
          seed[v.key] = typeof pv[v.key] === "number" ? pv[v.key] : v.fallback;
        }
        for (const c of CENSERS) {
          seed[c.key] = typeof pv[c.key] === "boolean" ? pv[c.key] : true;
        }
        setValues(seed);
      } catch (e) {
        if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // Optimistic patch — flip locally, fire-and-forget the write. The scribe should never
  // feel the wax cool between strokes.
  const patch = useCallback((key: string, val: number | boolean) => {
    setValues((v) => ({ ...v, [key]: val }));
    prefs.set({ [key]: val }).catch(() => {
      /* if the write fails we leave the optimistic value; toast would be too noisy here */
    });
  }, []);

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Memory</Text>
          <Text style={styles.h1}>记忆调律</Text>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <ScrollView
          contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 50, gap: theme.space(7), paddingTop: theme.space(2) }}
        >
          <Animated.View entering={FadeIn.duration(500)} style={styles.intro}>
            <Text style={styles.introText}>
              调节 GM 回忆的深度、容量与衰减。改动自即时生效，无须确认。
            </Text>
          </Animated.View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>召回与摘要</Text>
            {VIALS_RECALL.map((v, i) => (
              <Animated.View key={v.key} entering={FadeInDown.delay(i * 60).duration(400)}>
                <MemoryVial
                  vial={v}
                  value={typeof values[v.key] === "number" ? (values[v.key] as number) : v.fallback}
                  onChange={(n) => patch(v.key, n)}
                />
              </Animated.View>
            ))}
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>归档与上限</Text>
            {VIALS_ARCHIVE.map((v, i) => (
              <Animated.View key={v.key} entering={FadeInDown.delay(i * 60).duration(400)}>
                <MemoryVial
                  vial={v}
                  value={typeof values[v.key] === "number" ? (values[v.key] as number) : v.fallback}
                  onChange={(n) => patch(v.key, n)}
                />
              </Animated.View>
            ))}
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>三盏香 · 桶开关</Text>
            <Text style={styles.subProse}>
              熄灭一盏香，GM 不再从该桶检索。条目本身保留，重新点燃即恢复。
            </Text>
            <View style={styles.censerRack}>
              {CENSERS.map((c, i) => (
                <Animated.View
                  key={c.key}
                  entering={FadeInDown.delay(i * 70 + 100).duration(420)}
                  style={{ flex: 1 }}
                >
                  <Censer
                    label={c.label}
                    whisper={c.whisper}
                    lit={values[c.key] !== false}
                    onToggle={() => patch(c.key, !(values[c.key] !== false))}
                  />
                </Animated.View>
              ))}
            </View>
          </View>
        </ScrollView>
      )}
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: theme.space(4),
    paddingBottom: theme.space(3),
    gap: theme.space(1),
  },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  kicker: {
    fontFamily: theme.font.displaySemi,
    fontSize: theme.size.xs,
    letterSpacing: 4,
    textTransform: "uppercase",
    color: theme.color.accent,
  },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },

  intro: {
    paddingHorizontal: theme.space(1),
  },
  introText: {
    fontFamily: theme.font.proseItalic,
    fontSize: theme.size.sm,
    color: theme.color.textFaint,
    lineHeight: 22,
  },

  section: { gap: theme.space(4) },
  sectionTitle: {
    fontFamily: theme.font.displaySemi,
    fontSize: theme.size.xs,
    letterSpacing: 3,
    textTransform: "uppercase",
    color: theme.color.accent,
    marginBottom: theme.space(1),
  },
  subProse: {
    fontFamily: theme.font.prose,
    fontSize: theme.size.sm,
    color: theme.color.textFaint,
    lineHeight: 21,
    marginBottom: theme.space(1),
  },

  // Measuring vial: glyph + prose + brass numeral + ember trough.
  vial: { gap: theme.space(3) },
  vialHead: { flexDirection: "row", gap: theme.space(3), alignItems: "flex-start" },
  vialGlyph: {
    fontSize: 22,
    color: theme.color.accent,
    width: 28,
    textAlign: "center",
    marginTop: 2,
    textShadowColor: theme.color.accentSoft,
    textShadowRadius: 10,
  },
  vialNameRow: {
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
    gap: theme.space(2),
  },
  vialLabel: {
    fontFamily: theme.font.display,
    fontSize: theme.size.md,
    color: theme.color.text,
    letterSpacing: 0.5,
  },
  brassRow: { flexDirection: "row", alignItems: "baseline" },
  brassNumeral: {
    fontFamily: theme.font.display,
    fontSize: theme.size.lg,
    color: theme.color.accentBright,
    letterSpacing: 0.5,
  },
  brassUnit: {
    fontFamily: theme.font.mono,
    fontSize: theme.size.xs,
    color: theme.color.textFaint,
  },
  vialProse: {
    fontFamily: theme.font.prose,
    fontSize: theme.size.sm,
    color: theme.color.textFaint,
    lineHeight: 20,
    marginTop: 4,
  },
  // Ember-fill trough — thinner and more refined than the gm-style rail.
  troughWrap: {
    height: 22,
    justifyContent: "center",
    paddingLeft: theme.space(4) + 28 - 4, // align with the prose column under the glyph
  },
  trough: {
    height: 3,
    borderRadius: 2,
    backgroundColor: theme.color.bgInput,
    overflow: "hidden",
    borderTopWidth: 1,
    borderTopColor: theme.color.surfaceLine,
  },
  troughFill: {
    height: "100%",
    backgroundColor: theme.color.accent,
    shadowColor: theme.color.accent,
    shadowOpacity: 0.7,
    shadowRadius: 4,
  },

  // Censers — three side-by-side wax tablets carrying a flame glyph.
  censerRack: { flexDirection: "row", gap: theme.space(3) },
  censer: {
    paddingVertical: theme.space(4),
    paddingHorizontal: theme.space(3),
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    backgroundColor: theme.color.bgCard,
    gap: theme.space(2),
    minHeight: 138,
  },
  censerLit: {
    borderColor: theme.color.accentSoft,
    backgroundColor: theme.color.accentGhost,
  },
  censerHead: { alignItems: "center", gap: theme.space(1) },
  censerFlame: {
    fontSize: 24,
    color: theme.color.textFaint,
  },
  censerFlameLit: {
    color: theme.color.accentBright,
    textShadowColor: theme.color.accent,
    textShadowRadius: 12,
  },
  censerLabel: {
    fontFamily: theme.font.proseSemi,
    fontSize: theme.size.sm,
    color: theme.color.textDim,
    letterSpacing: 0.3,
  },
  censerState: {
    fontFamily: theme.font.mono,
    fontSize: theme.size.xs,
    letterSpacing: 1,
  },
  censerWhisper: {
    fontFamily: theme.font.proseItalic,
    fontSize: theme.size.xs,
    color: theme.color.textFaint,
    lineHeight: 17,
    textAlign: "center",
  },
});
