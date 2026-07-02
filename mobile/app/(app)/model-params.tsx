/**
 * Alchemist's Bench — 炼金参数. Sampling tuning for the chat completion call.
 *
 * Aesthetic direction: Candlelit Grimoire, leaned into the alchemical register. The page
 * reads as a workbench in a candlelit study:
 *  - Five glass cucurbits across the top: the presets (平衡 / 保守 / 创意 / 确定 / 自定义).
 *  - Banks of measuring vials below: sliders for temperature, top_p, top_k, repetition,
 *    frequency, presence, plus context_size and seed in a small register.
 *  - A warded cabinet for NSFW gating — closed for block, opened (with intensity dial and
 *    extra prompt) for the other three modes.
 *  - An inner chamber (collapsible) for Mirostat — only the obsessed enter here.
 *  - A brass scroll at the foot of the bench: the canonical JSON the engine sees, updated
 *    optimistically as the scribe twists the dials.
 *
 * Backend: every value persists as a user-preference key matching the desktop's
 * settings.jsx exactly so the same incantation follows the user across clients. No
 * dedicated endpoint — single fire-and-forget patch per change.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  LayoutChangeEvent,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Animated, { FadeIn, FadeInDown, runOnJS } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { prefs, settings } from "@/api";
import { ApiError } from "@/api/http";
import { theme, palette } from "@/theme/theme";

// Defaults mirror desktop MODEL_PARAM_DEFAULTS so the same first-render experience.
const DEFAULTS = {
  temperature: 0.78,
  top_p: 0.92,
  top_k: 40,
  repetition_penalty: 1.15,
  frequency_penalty: 0.2,
  presence_penalty: 0.1,
  max_tokens: 4096,
  context_size: 16384,
  seed: -1,
  mirostat_mode: "off" as "off" | "v1" | "v2",
  mirostat_tau: 5.0,
  mirostat_eta: 0.1,
  stop: "",
};

const PRESET_VALUES: Record<string, Partial<typeof DEFAULTS>> = {
  conservative: { temperature: 0.4, top_p: 0.85, repetition_penalty: 1.05, frequency_penalty: 0.1, presence_penalty: 0.0 },
  balanced: { temperature: 0.78, top_p: 0.92, repetition_penalty: 1.15, frequency_penalty: 0.2, presence_penalty: 0.1 },
  creative: { temperature: 1.0, top_p: 0.98, repetition_penalty: 1.2, frequency_penalty: 0.3, presence_penalty: 0.2 },
  deterministic: { temperature: 0.1, top_p: 0.5, repetition_penalty: 1.0, frequency_penalty: 0.0, presence_penalty: 0.0 },
};

const PRESETS: { key: string; label: string; tincture: string }[] = [
  { key: "balanced", label: "平衡", tincture: "等量草本与树脂——给大多数席位的稳态。" },
  { key: "conservative", label: "保守", tincture: "凝重的根茎汁液——少飘忽、少跑题。" },
  { key: "creative", label: "创意", tincture: "炽热的硫粉——燃得更跳跃。" },
  { key: "deterministic", label: "确定", tincture: "蒸馏盐——几乎重复同一句话。" },
  { key: "custom", label: "自定义", tincture: "亲手调配的方剂——你说了算。" },
];

const CONTEXT_OPTIONS = [
  { value: 4096, label: "4K" },
  { value: 8192, label: "8K" },
  { value: 16384, label: "16K" },
  { value: 32768, label: "32K" },
  { value: 65536, label: "64K" },
  { value: 131072, label: "128K" },
  { value: 200000, label: "200K" },
];

type SliderDef = {
  key: keyof typeof DEFAULTS;
  label: string;
  min: number;
  max: number;
  step: number;
  desc: string;
  format?: (n: number) => string;
};

const CORE_VIALS: SliderDef[] = [
  { key: "temperature", label: "Temperature", min: 0, max: 2, step: 0.05, desc: "越高越发散；0 最确定。常用 0.4–1.0。" },
  { key: "top_p", label: "Top-p", min: 0, max: 1, step: 0.01, desc: "累计概率截断；0.9–0.95 是常见值。" },
  { key: "top_k", label: "Top-k", min: 0, max: 200, step: 1, desc: "从前 K 个 token 中采样；0 = 关闭。", format: (n) => String(Math.round(n)) },
  { key: "repetition_penalty", label: "Repetition Penalty", min: 1, max: 2, step: 0.01, desc: "压制最近用过的 token。1.0 = 不压制。" },
  { key: "frequency_penalty", label: "Frequency Penalty", min: -2, max: 2, step: 0.05, desc: "按 token 出现频率调整。OpenAI 风。" },
  { key: "presence_penalty", label: "Presence Penalty", min: -2, max: 2, step: 0.05, desc: "按 token 是否曾出现调整。OpenAI 风。" },
];

const NSFW_TIERS: { key: "block" | "soft" | "open" | "explicit"; label: string; sub: string }[] = [
  { key: "block", label: "封缄", sub: "完全屏蔽。" },
  { key: "soft", label: "暗示", sub: "可以暗示，不直白。" },
  { key: "open", label: "开放", sub: "允许成人主题与场景。" },
  { key: "explicit", label: "炽烈", sub: "明确细节，谨慎使用。" },
];

const EFFORT_TIERS: { key: "low" | "medium" | "high"; label: string }[] = [
  { key: "low", label: "省力" },
  { key: "medium", label: "适中" },
  { key: "high", label: "竭力" },
];

function formatSlider(def: SliderDef, value: number) {
  if (def.format) return def.format(value);
  if (def.step >= 1) return String(Math.round(value));
  // Use the step's decimal places to format
  const decimals = String(def.step).split(".")[1]?.length || 2;
  return value.toFixed(decimals);
}

function ParamVial({
  def,
  value,
  onChange,
}: {
  def: SliderDef;
  value: number;
  onChange: (v: number) => void;
}) {
  const widthRef = useRef(1);
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);

  const setFromX = useCallback(
    (x: number) => {
      const frac = Math.max(0, Math.min(1, x / widthRef.current));
      const raw = def.min + frac * (def.max - def.min);
      const snapped = Math.round(raw / def.step) * def.step;
      const clamped = Math.max(def.min, Math.min(def.max, snapped));
      // Keep numeric noise out of the brass plaque
      const cleaned = Number(clamped.toFixed(6));
      setLocal(cleaned);
      onChange(cleaned);
    },
    [def, onChange],
  );

  const onLayout = (e: LayoutChangeEvent) => {
    widthRef.current = Math.max(1, e.nativeEvent.layout.width);
  };

  const pan = Gesture.Pan().onBegin((e) => runOnJS(setFromX)(e.x)).onUpdate((e) => runOnJS(setFromX)(e.x));
  const tap = Gesture.Tap().onEnd((e) => runOnJS(setFromX)(e.x));
  const gesture = Gesture.Simultaneous(pan, tap);

  const pct = ((local - def.min) / (def.max - def.min)) * 100;
  // Show a zero-marker on the trough for ±penalty sliders so the scribe sees neutrality.
  const zeroPct = def.min < 0 ? ((0 - def.min) / (def.max - def.min)) * 100 : null;

  return (
    <View style={styles.vial}>
      <View style={styles.vialHead}>
        <Text style={styles.vialLabel}>{def.label}</Text>
        <Text style={styles.brassNumeral}>{formatSlider(def, local)}</Text>
      </View>
      <Text style={styles.vialProse}>{def.desc}</Text>
      <GestureDetector gesture={gesture}>
        <View style={styles.troughWrap} onLayout={onLayout} hitSlop={{ top: 12, bottom: 12 }}>
          <View style={styles.trough}>
            {zeroPct != null ? <View style={[styles.zeroMark, { left: `${zeroPct}%` }]} /> : null}
            <View style={[styles.troughFill, { width: `${pct}%` }]} />
          </View>
        </View>
      </GestureDetector>
    </View>
  );
}

export default function ModelParamsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [loading, setLoading] = useState(true);
  const [params, setParams] = useState({ ...DEFAULTS });
  const [preset, setPreset] = useState<string>("balanced");
  const [nsfw, setNsfw] = useState<{ mode: "block" | "soft" | "open" | "explicit"; intensity: number; extra_prompt: string }>({
    mode: "soft",
    intensity: 0.5,
    extra_prompt: "",
  });
  const [reasoningEffort, setReasoningEffort] = useState<"low" | "medium" | "high">("medium");
  const [showReasoning, setShowReasoning] = useState(false);
  const [advanced, setAdvanced] = useState(false);

  // Stable patch — set local optimistically, then fire-and-forget the write. No toast on
  // success; a failed pref write here is too noisy to surface (the slider already moved).
  const write = useCallback((key: string, val: any) => {
    prefs.set({ [key]: val }).catch(() => {});
  }, []);

  // Update a numeric param and switch preset to custom if the value diverges from the
  // current preset's anchored values (matches desktop UX).
  const u = useCallback(
    (k: keyof typeof DEFAULTS, v: any) => {
      setParams((p) => ({ ...p, [k]: v }));
      write(k, v);
    },
    [write],
  );

  const applyPreset = useCallback(
    (name: string) => {
      setPreset(name);
      write("preset", name);
      const values = PRESET_VALUES[name];
      if (values) {
        setParams((p) => ({ ...p, ...values }));
        for (const k of Object.keys(values)) write(k, (values as any)[k]);
      }
    },
    [write],
  );

  const updateNsfw = useCallback(
    (patch: Partial<typeof nsfw>) => {
      setNsfw((n) => ({ ...n, ...patch }));
      if ("mode" in patch) write("nsfw_mode", patch.mode);
      if ("intensity" in patch) write("nsfw_intensity", patch.intensity);
      if ("extra_prompt" in patch) write("nsfw_extra_prompt", patch.extra_prompt);
    },
    [write],
  );

  // Load from /me/profile preferences. Same parsing as desktop: clamp/validate everything,
  // fall back to defaults on any malformed pref. Also figure out if the currently selected
  // chat model carries the "reasoning" capability hint so we know whether to surface the
  // effort tiers.
  useEffect(() => {
    (async () => {
      try {
        const r = await prefs.get();
        const p: Record<string, any> = r?.preferences ?? {};
        const next = { ...DEFAULTS };
        for (const k of Object.keys(next) as (keyof typeof DEFAULTS)[]) {
          if (k === "mirostat_mode") {
            const m = String(p[k] ?? "off");
            next[k] = (["off", "v1", "v2"].includes(m) ? m : "off") as any;
          } else if (k === "stop") {
            next[k] = typeof p[k] === "string" ? p[k] : "" as any;
          } else if (typeof p[k] === "number" && Number.isFinite(p[k])) {
            next[k] = p[k];
          }
        }
        setParams(next);
        setAdvanced(next.mirostat_mode !== "off");

        const pres = String(p["preset"] ?? "balanced");
        setPreset(PRESET_VALUES[pres] || pres === "custom" ? pres : "balanced");

        const legacyNsfw = (p["nsfw"] && typeof p["nsfw"] === "object") ? p["nsfw"] : {};
        const mode = String(p["nsfw_mode"] ?? legacyNsfw.mode ?? "soft");
        const intensity = Number(p["nsfw_intensity"] ?? legacyNsfw.intensity ?? 0.5);
        setNsfw({
          mode: (["block", "soft", "open", "explicit"].includes(mode) ? mode : "soft") as any,
          intensity: Number.isFinite(intensity) ? intensity : 0.5,
          extra_prompt: String(p["nsfw_extra_prompt"] ?? legacyNsfw.extra_prompt ?? legacyNsfw.extra ?? ""),
        });

        const eff = String(p["reasoning_effort"] ?? "medium");
        setReasoningEffort((["low", "medium", "high"].includes(eff) ? eff : "medium") as any);

        // Check capabilities on the currently selected chat model
        try {
          const m = await settings.models();
          const apis = m?.models?.apis ?? [];
          const selApi = m?.selected?.api_id;
          const selModel = m?.selected?.model_id;
          for (const a of apis) {
            const aid = a.api_id || (a as any).id;
            if (aid !== selApi) continue;
            for (const mod of a.models || []) {
              if (mod.id !== selModel) continue;
              const caps: string[] = (mod as any).capabilities || [];
              if (caps.some((c) => /reason/i.test(c))) setShowReasoning(true);
            }
          }
        } catch {
          /* ignore — reasoning hint is best-effort */
        }
      } catch (e) {
        if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // The canonical JSON shown in the brass scroll at the bottom — same shape the desktop
  // shows so the scribe can copy-paste between clients.
  const inscription = useMemo(
    () => JSON.stringify(
      {
        temperature: params.temperature,
        top_p: params.top_p,
        top_k: params.top_k,
        repetition_penalty: params.repetition_penalty,
        frequency_penalty: params.frequency_penalty,
        presence_penalty: params.presence_penalty,
        max_tokens: params.max_tokens,
        context_size: params.context_size,
        seed: params.seed,
        stop: params.stop.split("|").filter(Boolean),
        nsfw: nsfw.mode === "block" ? null : { mode: nsfw.mode, intensity: nsfw.intensity, extra: nsfw.extra_prompt },
        ...(advanced ? { mirostat_mode: params.mirostat_mode, mirostat_tau: params.mirostat_tau, mirostat_eta: params.mirostat_eta } : {}),
        ...(showReasoning ? { reasoning_effort: reasoningEffort } : {}),
      },
      null,
      2,
    ),
    [params, nsfw, advanced, showReasoning, reasoningEffort],
  );

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Sampling</Text>
          <Text style={styles.h1}>炼金参数</Text>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <ScrollView
          contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 60, gap: theme.space(7), paddingTop: theme.space(2) }}
        >
          <Animated.View entering={FadeIn.duration(500)}>
            <Text style={styles.introText}>
              五瓶蒸馏方剂置于工作台。择一瓶倾出，或亲手调配你自己的配比。
            </Text>
          </Animated.View>

          {/* Cucurbits — preset selectors arranged as labelled flasks */}
          <View style={styles.cucurbitRow}>
            {PRESETS.map((p, i) => {
              const active = preset === p.key;
              return (
                <Animated.View
                  key={p.key}
                  entering={FadeInDown.delay(i * 40).duration(360)}
                  style={{ flex: 1 }}
                >
                  <Pressable
                    onPress={() => applyPreset(p.key)}
                    style={({ pressed }) => [
                      styles.cucurbit,
                      active && styles.cucurbitActive,
                      pressed && { opacity: 0.85 },
                    ]}
                  >
                    <View style={[styles.cucurbitNeck, active && styles.cucurbitNeckActive]} />
                    <View style={[styles.cucurbitBody, active && styles.cucurbitBodyActive]}>
                      <Text style={[styles.cucurbitLabel, active && { color: theme.color.accentBright }]}>
                        {p.label}
                      </Text>
                    </View>
                  </Pressable>
                </Animated.View>
              );
            })}
          </View>
          <Text style={styles.tincture}>
            {PRESETS.find((p) => p.key === preset)?.tincture}
          </Text>

          {/* Core vials — the six measuring instruments */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>核心配比</Text>
            {CORE_VIALS.map((d, i) => (
              <Animated.View key={d.key} entering={FadeInDown.delay(i * 40).duration(360)}>
                <ParamVial
                  def={d}
                  value={(params as any)[d.key]}
                  onChange={(v) => {
                    // Any manual change switches preset → custom (mirrors desktop)
                    if (preset !== "custom") {
                      setPreset("custom");
                      write("preset", "custom");
                    }
                    u(d.key, v);
                  }}
                />
              </Animated.View>
            ))}
          </View>

          {showReasoning ? (
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>推理强度</Text>
              <Text style={styles.subProse}>当前模型支持推理深度切换。省力更快、竭力更深思熟虑。</Text>
              <View style={styles.tierRow}>
                {EFFORT_TIERS.map((e) => {
                  const active = reasoningEffort === e.key;
                  return (
                    <Pressable
                      key={e.key}
                      onPress={() => { setReasoningEffort(e.key); write("reasoning_effort", e.key); }}
                      style={[styles.tierChip, active && styles.tierChipActive]}
                    >
                      <Text style={[styles.tierChipLabel, active && { color: theme.color.accentBright }]}>{e.label}</Text>
                    </Pressable>
                  );
                })}
              </View>
            </View>
          ) : null}

          {/* Register — max_tokens / context_size / seed / stop */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>工作台账册</Text>

            <View style={styles.regRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.regLabel}>Max Tokens</Text>
                <Text style={styles.regProse}>单次回复上限。</Text>
              </View>
              <TextInput
                value={String(params.max_tokens)}
                onChangeText={(s) => {
                  const n = Number(s);
                  if (Number.isFinite(n)) u("max_tokens", n);
                }}
                keyboardType="number-pad"
                style={styles.regInput}
              />
            </View>

            <View style={styles.regRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.regLabel}>Context Size</Text>
                <Text style={styles.regProse}>注入到上下文的总 token 容量。</Text>
              </View>
              <View style={styles.contextSelect}>
                {CONTEXT_OPTIONS.map((c) => {
                  const active = params.context_size === c.value;
                  return (
                    <Pressable
                      key={c.value}
                      onPress={() => u("context_size", c.value)}
                      style={[styles.ctxChip, active && styles.ctxChipActive]}
                    >
                      <Text style={[styles.ctxChipLabel, active && { color: theme.color.accentBright }]}>{c.label}</Text>
                    </Pressable>
                  );
                })}
              </View>
            </View>

            <View style={styles.regRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.regLabel}>Seed</Text>
                <Text style={styles.regProse}>固定种子 = 复现同一笔迹；-1 = 随机。</Text>
              </View>
              <TextInput
                value={String(params.seed)}
                onChangeText={(s) => {
                  const n = Number(s);
                  if (Number.isFinite(n)) u("seed", n);
                }}
                keyboardType="numbers-and-punctuation"
                style={styles.regInput}
              />
            </View>

            <View style={[styles.regRow, { alignItems: "flex-start" }]}>
              <View style={{ flex: 1 }}>
                <Text style={styles.regLabel}>Stop Sequences</Text>
                <Text style={styles.regProse}>多个停字以 ｜ 分隔（如 player:|system:）。</Text>
              </View>
              <TextInput
                value={params.stop}
                onChangeText={(s) => u("stop", s)}
                placeholder="player:|system:"
                placeholderTextColor={theme.color.textFaint}
                style={[styles.regInput, { width: 160 }]}
                autoCapitalize="none"
              />
            </View>
          </View>

          {/* Warded cabinet — NSFW gating */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>结界橱柜 · NSFW</Text>
            <Text style={styles.subProse}>四道印封。封缄拒入，其余允许并按强度增减细节。</Text>
            <View style={styles.tierGrid}>
              {NSFW_TIERS.map((t) => {
                const active = nsfw.mode === t.key;
                return (
                  <Pressable
                    key={t.key}
                    onPress={() => updateNsfw({ mode: t.key })}
                    style={[styles.wardCard, active && styles.wardCardActive]}
                  >
                    <View style={[styles.wardSeal, active && styles.wardSealActive]} />
                    <Text style={[styles.wardLabel, active && { color: theme.color.accentBright }]}>{t.label}</Text>
                    <Text style={styles.wardSub}>{t.sub}</Text>
                  </Pressable>
                );
              })}
            </View>

            {nsfw.mode !== "block" ? (
              <Animated.View entering={FadeIn.duration(360)} style={{ gap: theme.space(4) }}>
                <ParamVial
                  def={{ key: "presence_penalty" as any, label: "Intensity", min: 0, max: 1, step: 0.05, desc: "细节强度。0 接近暗示，1 接近炽烈。" }}
                  value={nsfw.intensity}
                  onChange={(v) => updateNsfw({ intensity: v })}
                />
                <View>
                  <Text style={styles.regLabel}>额外咒文（可选）</Text>
                  <Text style={styles.regProse}>追加注入 GM 的具体偏好。可留空。</Text>
                  <TextInput
                    value={nsfw.extra_prompt}
                    onChangeText={(s) => updateNsfw({ extra_prompt: s })}
                    placeholder="例如：避免暴力描写，多用感官细节…"
                    placeholderTextColor={theme.color.textFaint}
                    style={[styles.regInput, { width: "100%", minHeight: 80, marginTop: theme.space(2) }]}
                    multiline
                  />
                </View>
              </Animated.View>
            ) : null}
          </View>

          {/* Inner chamber — Mirostat */}
          <View style={styles.section}>
            <View style={styles.chamberHead}>
              <View style={{ flex: 1 }}>
                <Text style={styles.sectionTitle}>密室 · Mirostat</Text>
                <Text style={styles.subProse}>实验性自适应采样。开启后将覆盖 Top-p / Top-k 行为。</Text>
              </View>
              <Pressable
                onPress={() => {
                  const next = !advanced;
                  setAdvanced(next);
                  if (!next) { u("mirostat_mode", "off"); }
                }}
                style={[styles.chamberToggle, advanced && styles.chamberToggleOn]}
              >
                <Text style={[styles.chamberToggleLabel, advanced && { color: theme.color.bg }]}>
                  {advanced ? "已开启" : "已封闭"}
                </Text>
              </Pressable>
            </View>

            {advanced ? (
              <Animated.View entering={FadeIn.duration(360)} style={{ gap: theme.space(4) }}>
                <View>
                  <Text style={styles.regLabel}>模式</Text>
                  <View style={styles.tierRow}>
                    {(["off", "v1", "v2"] as const).map((m) => {
                      const active = params.mirostat_mode === m;
                      return (
                        <Pressable
                          key={m}
                          onPress={() => u("mirostat_mode", m)}
                          style={[styles.tierChip, active && styles.tierChipActive]}
                        >
                          <Text style={[styles.tierChipLabel, active && { color: theme.color.accentBright }]}>
                            {m === "off" ? "停" : m}
                          </Text>
                        </Pressable>
                      );
                    })}
                  </View>
                </View>
                <ParamVial
                  def={{ key: "mirostat_tau", label: "τ (tau)", min: 0, max: 10, step: 0.1, desc: "目标 perplexity；常用 5。" }}
                  value={params.mirostat_tau}
                  onChange={(v) => u("mirostat_tau", v)}
                />
                <ParamVial
                  def={{ key: "mirostat_eta", label: "η (eta)", min: 0, max: 1, step: 0.01, desc: "学习率。" }}
                  value={params.mirostat_eta}
                  onChange={(v) => u("mirostat_eta", v)}
                />
              </Animated.View>
            ) : null}
          </View>

          {/* Brass scroll — canonical JSON */}
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>铭刻 · 引擎所见</Text>
            <Text style={styles.subProse}>同样的字句也会出现在桌面端预览中。</Text>
            <View style={styles.scroll}>
              <Text style={styles.scrollText}>{inscription}</Text>
            </View>
          </View>
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

  introText: { fontFamily: theme.font.proseItalic, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 22 },

  // Cucurbit row — five labelled flask silhouettes
  cucurbitRow: { flexDirection: "row", gap: theme.space(2), alignItems: "flex-end" },
  cucurbit: { alignItems: "center" },
  cucurbitNeck: {
    width: 14,
    height: 12,
    borderTopLeftRadius: 3,
    borderTopRightRadius: 3,
    backgroundColor: theme.color.bgCard,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    borderBottomWidth: 0,
  },
  cucurbitNeckActive: { borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  cucurbitBody: {
    width: "100%",
    paddingVertical: theme.space(3),
    paddingHorizontal: theme.space(2),
    borderRadius: theme.radius.md,
    borderTopLeftRadius: theme.radius.sm,
    borderTopRightRadius: theme.radius.sm,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    backgroundColor: theme.color.bgCard,
    alignItems: "center",
    minHeight: 64,
    justifyContent: "center",
  },
  cucurbitBodyActive: { borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost, shadowColor: theme.color.accent, shadowOpacity: 0.5, shadowRadius: 10 },
  cucurbitActive: {},
  cucurbitLabel: { fontFamily: theme.font.display, fontSize: theme.size.sm, color: theme.color.textDim, letterSpacing: 0.5 },
  tincture: { fontFamily: theme.font.proseItalic, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 21, marginTop: -theme.space(3) },

  // Section
  section: { gap: theme.space(4) },
  sectionTitle: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 3, textTransform: "uppercase", color: theme.color.accent },
  subProse: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 21 },

  // Measuring vials
  vial: { gap: theme.space(2) },
  vialHead: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between" },
  vialLabel: { fontFamily: theme.font.display, fontSize: theme.size.md, color: theme.color.text, letterSpacing: 0.5 },
  brassNumeral: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.accentBright, letterSpacing: 0.5 },
  vialProse: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 20 },
  troughWrap: { height: 22, justifyContent: "center" },
  trough: { height: 3, borderRadius: 2, backgroundColor: theme.color.bgInput, overflow: "hidden", borderTopWidth: 1, borderTopColor: theme.color.surfaceLine, position: "relative" },
  troughFill: { height: "100%", backgroundColor: theme.color.accent, shadowColor: theme.color.accent, shadowOpacity: 0.7, shadowRadius: 4 },
  zeroMark: { position: "absolute", top: -2, bottom: -2, width: 1, backgroundColor: theme.color.textFaint, opacity: 0.5 },

  // Tier chips (reasoning effort, mirostat mode)
  tierRow: { flexDirection: "row", gap: theme.space(2) },
  tierChip: { flex: 1, paddingVertical: theme.space(3), paddingHorizontal: theme.space(3), borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard, alignItems: "center" },
  tierChipActive: { borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  tierChipLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.textDim, letterSpacing: 0.5 },

  // Register rows
  regRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), paddingVertical: theme.space(2) },
  regLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.text },
  regProse: { fontFamily: theme.font.prose, fontSize: theme.size.xs, color: theme.color.textFaint, marginTop: 2 },
  regInput: { width: 110, backgroundColor: theme.color.bgInput, borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine, paddingHorizontal: theme.space(3), paddingVertical: theme.space(2), color: theme.color.text, fontFamily: theme.font.mono, fontSize: theme.size.sm, textAlign: "right" },
  contextSelect: { flexDirection: "row", flexWrap: "wrap", gap: 4, justifyContent: "flex-end", maxWidth: 200 },
  ctxChip: { paddingVertical: 4, paddingHorizontal: 8, borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine },
  ctxChipActive: { borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  ctxChipLabel: { fontFamily: theme.font.mono, fontSize: 11, color: theme.color.textDim },

  // Warded cabinet — NSFW tiers as four sealed cards
  tierGrid: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(3) },
  wardCard: { flexBasis: "47%", flexGrow: 1, paddingVertical: theme.space(4), paddingHorizontal: theme.space(3), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard, alignItems: "center", gap: theme.space(2) },
  wardCardActive: { borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  wardSeal: { width: 16, height: 16, borderRadius: 8, borderWidth: 1, borderColor: theme.color.textFaint, backgroundColor: "transparent" },
  wardSealActive: { borderColor: theme.color.accent, backgroundColor: theme.color.accent, shadowColor: theme.color.accent, shadowOpacity: 0.6, shadowRadius: 8 },
  wardLabel: { fontFamily: theme.font.display, fontSize: theme.size.md, color: theme.color.textDim, letterSpacing: 0.5 },
  wardSub: { fontFamily: theme.font.prose, fontSize: theme.size.xs, color: theme.color.textFaint, textAlign: "center" },

  // Inner chamber toggle
  chamberHead: { flexDirection: "row", alignItems: "center", gap: theme.space(3) },
  chamberToggle: { paddingVertical: theme.space(2), paddingHorizontal: theme.space(4), borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  chamberToggleOn: { backgroundColor: theme.color.accent, borderColor: theme.color.accent },
  chamberToggleLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.xs, letterSpacing: 1, color: theme.color.textDim, textTransform: "uppercase" },

  // Brass scroll — JSON preview
  scroll: { backgroundColor: palette.ink850, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, padding: theme.space(3) },
  scrollText: { fontFamily: theme.font.mono, fontSize: 11, lineHeight: 18, color: palette.parchmentDim },
});
