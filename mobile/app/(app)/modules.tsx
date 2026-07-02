/**
 * Council of Hands — 模块议席. Per-module model assignment, treated as a long oaken
 * council where each AI sub-agent holds a named seat. The 主 GM sits at the head; the
 * other twelve hands serve specialized duties — context-weaving, instruction-parsing,
 * card-forging, extraction, embedding, image-conjuring. A seat can swear to a specific
 * model, or remain bound to the 主 GM's choice.
 *
 * Aesthetic direction: refined-minimalist register of Candlelit Grimoire. Each seat is a
 * heraldic shield row — left-margin sigil, role inscribed in display caps, the duty in
 * serif body, the sworn model as a brass plaque on the right. The 主 GM seat carries a
 * slightly heavier shield border and gilt corner accents. Tapping a seat slides up a
 * model-picker sheet (same shape as the gm-style picker, but provider-narrowed).
 *
 * Backend: all module models persist as user-preference keys (no dedicated endpoint),
 * matching the desktop's settings.jsx behavior. Empty value = inherit 主 GM.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { BlurView } from "expo-blur";
import Animated, { FadeIn, FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { settings, prefs, ProviderInfo } from "@/api";
import { ApiError } from "@/api/http";
import { theme, palette } from "@/theme/theme";

// Each module is a seat with its own preference key. The keys match the desktop's
// settings.jsx so the same selection follows the user across clients.
type Seat = {
  /** Preference key holding `${api_id}::${model_id}`, or empty = inherit 主GM. */
  apiKey: string;
  modelKey: string;
  /** Glyph carved in the seat's shield. */
  sigil: string;
  role: string;
  duty: string;
  /** If true, this seat doesn't inherit and must be explicitly set. */
  mandatory?: boolean;
  /** If "embedding", limit the picker to models with embedding capability hint. */
  kind?: "chat" | "embedding" | "image";
};

const HEAD_SEAT: Seat = {
  apiKey: "default_api_id",
  modelKey: "default_model_real_name",
  sigil: "✶",
  role: "主 GM",
  duty: "玩家对话的核心叙事模型。其他议席默认随它流转。",
};

const HALL: Seat[] = [
  { apiKey: "context_agent.api_id", modelKey: "context_agent.model_real_name", sigil: "❦", role: "上下文织者", duty: "整理玩家意图、检索计划，把模糊低语化为结构化指令。" },
  { apiKey: "instruction_parser.api_id", modelKey: "instruction_parser.model_real_name", sigil: "⌖", role: "指令解析使", duty: "解析 /set 命令的自然语言，转为结构化操作。" },
  { apiKey: "console_assistant.api_id", modelKey: "console_assistant.model_real_name", sigil: "✺", role: "控制台助手", duty: "侧栏管理员控制台、剧本编辑器的司笔灵专用。" },
  { apiKey: "extractor.api_id", modelKey: "extractor.model_real_name", sigil: "✦", role: "叙事提取者", duty: "GM 叙事二次解析，提取状态操作（两步式 GM 第二步）。" },
  { apiKey: "card_generator.api_id", modelKey: "card_generator.model_real_name", sigil: "❖", role: "角色卡铸者", duty: "侧栏创意工具：生成或微调角色卡。" },
  { apiKey: "card_field_parser.api_id", modelKey: "card_field_parser.model_real_name", sigil: "◈", role: "卡字段整理", duty: "导入酒馆卡时把自由文本整理成结构化字段。" },
  { apiKey: "consistency_judge.api_id", modelKey: "consistency_judge.model_real_name", sigil: "⚖", role: "一致性评者", duty: "对角色卡生成结果打一致性评分（0–1，阈值 0.6）。" },
  { apiKey: "acceptance_verifier.api_id", modelKey: "acceptance_verifier.model_real_name", sigil: "⚘", role: "受验之眼", duty: "校验 GM 输出是否满足 curator 的验收条件。" },
  { apiKey: "compact.api_id", modelKey: "compact.model_real_name", sigil: "⎈", role: "阶段浓缩者", duty: "把长局历史按阶段浓缩为摘要，供 GM 记忆远期剧情。" },
  { apiKey: "black_swan.api_id", modelKey: "black_swan.model_real_name", sigil: "🜂", role: "黑天鹅使", duty: "主动触发世界突发事件的子代理。" },
  { apiKey: "generic_agent.api_id", modelKey: "generic_agent.model_real_name", sigil: "⌬", role: "兜底通用", duty: "未单独配置模型的子代理统一使用它。" },
  { apiKey: "embed.api_id", modelKey: "embed.model_real_name", sigil: "𓂀", role: "向量嵌入", duty: "向量嵌入模型，用于记忆召回与拆书后的语义检索。", mandatory: true, kind: "embedding" },
  { apiKey: "image.api_id", modelKey: "image.model_real_name", sigil: "◎", role: "图像生成", duty: "聊天内 AI 生图、角色卡头像、剧本封面、人设图等全部走它。", mandatory: true, kind: "image" },
];

// Lightweight capability heuristic — matches whatever the catalog tags expose, with sane
// fallbacks. Embedding/image models often carry "embed" / "image" in their id when no
// capability metadata is present.
function modelMatchesKind(model: any, kind: Seat["kind"]): boolean {
  if (!kind || kind === "chat") return true;
  const caps: string[] = Array.isArray(model?.capabilities) ? model.capabilities.map((c: any) => String(c).toLowerCase()) : [];
  const id = String(model?.id || "").toLowerCase();
  if (kind === "embedding") return caps.includes("embedding") || /embed/.test(id);
  if (kind === "image") return caps.includes("image") || caps.includes("imagegen") || /image|imagen|dalle|sd|sdxl|flux/.test(id);
  return true;
}

export default function ModulesScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [prefVals, setPrefVals] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [pickerSeat, setPickerSeat] = useState<Seat | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [m, p] = await Promise.allSettled([settings.models(), prefs.get()]);
        if (m.status === "fulfilled") {
          const list: ProviderInfo[] = m.value?.models?.apis ?? [];
          for (const pp of list) if (!pp.api_id) pp.api_id = (pp as any).id;
          setProviders(list.filter((pp) => pp.has_credential));
        }
        if (p.status === "fulfilled") {
          const pv: Record<string, any> = p.value?.preferences ?? {};
          const out: Record<string, string> = {};
          for (const k of Object.keys(pv)) {
            if (typeof pv[k] === "string") out[k] = pv[k];
          }
          setPrefVals(out);
        }
      } catch (e) {
        if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // Lookup the display label for a sworn model. If the api_id+model isn't found in the
  // current catalog (e.g. a model that was removed after the user picked it), we still
  // show the raw model id — better than pretending nothing's set.
  const labelFor = useCallback(
    (seat: Seat): { label: string; sub: string; bound: boolean } => {
      const api = prefVals[seat.apiKey];
      const model = prefVals[seat.modelKey];
      if (!api || !model) {
        if (seat.mandatory) return { label: "席位空缺", sub: "需单独指定", bound: false };
        return { label: "随主 GM", sub: "继承默认", bound: false };
      }
      const provider = providers.find((p) => p.api_id === api);
      const models: any[] = provider?.models ?? [];
      const m = models.find((x) => x.id === model);
      const label = m?.name || m?.id || model;
      const sub = provider?.display_name || provider?.api_id || api;
      return { label, sub, bound: true };
    },
    [prefVals, providers],
  );

  const swearIn = useCallback(
    async (seat: Seat, api_id: string | null, model_id: string | null) => {
      setBusyKey(seat.apiKey);
      const patch: Record<string, unknown> =
        api_id && model_id
          ? { [seat.apiKey]: api_id, [seat.modelKey]: model_id }
          : { [seat.apiKey]: "", [seat.modelKey]: "" };
      setPrefVals((prev) => ({
        ...prev,
        [seat.apiKey]: typeof patch[seat.apiKey] === "string" ? (patch[seat.apiKey] as string) : "",
        [seat.modelKey]: typeof patch[seat.modelKey] === "string" ? (patch[seat.modelKey] as string) : "",
      }));
      try {
        await prefs.set(patch);
      } catch (e) {
        Alert.alert("无法落座", e instanceof ApiError ? e.message : "请重试。");
      } finally {
        setBusyKey(null);
        setPickerSeat(null);
      }
    },
    [],
  );

  const renderSeat = (seat: Seat, isHead = false, index = 0) => {
    const { label, sub, bound } = labelFor(seat);
    const busy = busyKey === seat.apiKey;
    return (
      <Animated.View
        key={seat.apiKey}
        entering={FadeInDown.delay(Math.min(index, 12) * 28).duration(360)}
      >
        <Pressable
          onPress={() => setPickerSeat(seat)}
          style={({ pressed }) => [
            styles.seat,
            isHead && styles.headSeat,
            pressed && { backgroundColor: theme.color.bgElevated },
          ]}
        >
          {/* Heraldic shield: sigil carved in stone. Heavier border for the head seat. */}
          <View style={[styles.shield, isHead && styles.shieldHead]}>
            <Text style={[styles.sigil, isHead && styles.sigilHead]}>{seat.sigil}</Text>
          </View>

          {/* Title & duty inscription. */}
          <View style={{ flex: 1, gap: 2 }}>
            <Text style={[styles.role, isHead && styles.roleHead]}>{seat.role}</Text>
            <Text style={styles.duty} numberOfLines={2}>{seat.duty}</Text>
          </View>

          {/* Brass plaque: sworn model + provider. Spacer width fixed so the right edge aligns. */}
          <View style={styles.plaqueWrap}>
            <View style={[styles.plaque, !bound && (seat.mandatory ? styles.plaqueEmpty : styles.plaqueInherit)]}>
              {busy ? (
                <ActivityIndicator color={theme.color.accent} size="small" />
              ) : (
                <>
                  <Text
                    style={[
                      styles.plaqueLabel,
                      !bound && (seat.mandatory ? styles.plaqueEmptyText : styles.plaqueInheritText),
                    ]}
                    numberOfLines={1}
                  >
                    {label}
                  </Text>
                  <Text style={styles.plaqueSub} numberOfLines={1}>{sub}</Text>
                </>
              )}
            </View>
          </View>
        </Pressable>
      </Animated.View>
    );
  };

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Council</Text>
          <Text style={styles.h1}>模块议席</Text>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <ScrollView
          contentContainerStyle={{
            paddingHorizontal: theme.space(5),
            paddingBottom: insets.bottom + 50,
            paddingTop: theme.space(2),
            gap: theme.space(2),
          }}
        >
          <Animated.View entering={FadeIn.duration(500)} style={styles.intro}>
            <Text style={styles.introText}>
              席间每位执笔者司其职。主 GM 决断叙事；其他议席若无单独受命，皆奉主 GM 为先。
            </Text>
          </Animated.View>

          {renderSeat(HEAD_SEAT, true, 0)}

          <View style={styles.tableRule} />

          {HALL.map((s, i) => renderSeat(s, false, i + 1))}
        </ScrollView>
      )}

      {pickerSeat ? (
        <SeatPicker
          seat={pickerSeat}
          providers={providers.filter((p) => (p.models || []).some((m) => modelMatchesKind(m, pickerSeat?.kind)))}
          currentApi={prefVals[pickerSeat.apiKey] || ""}
          currentModel={prefVals[pickerSeat.modelKey] || ""}
          onClose={() => setPickerSeat(null)}
          onSwear={(api, model) => swearIn(pickerSeat, api, model)}
        />
      ) : null}
    </GrimoireBackdrop>
  );
}

function SeatPicker({
  seat,
  providers,
  currentApi,
  currentModel,
  onClose,
  onSwear,
}: {
  seat: Seat;
  providers: ProviderInfo[];
  currentApi: string;
  currentModel: string;
  onClose: () => void;
  onSwear: (api_id: string | null, model_id: string | null) => void;
}) {
  const insets = useSafeAreaInsets();
  return (
    <Modal visible transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <View style={[styles.pickerSheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
        <BlurView intensity={32} tint="dark" style={styles.fill} />
        <View style={styles.grab} />
        <Text style={styles.pickerKicker}>{seat.role}</Text>
        <Text style={styles.pickerTitle}>授予该席位的模型</Text>

        <ScrollView style={styles.pickerBody}>
          {!seat.mandatory ? (
            <Pressable
              onPress={() => onSwear(null, null)}
              style={[styles.pickRow, !currentApi && styles.pickRowOn]}
            >
              <Text style={[styles.pickName, !currentApi && { color: theme.color.accentBright }]}>随主 GM</Text>
              <Text style={styles.pickSub}>继承默认</Text>
            </Pressable>
          ) : null}

          {providers.length === 0 ? (
            <Text style={styles.empty}>
              没有满足此席位的可用模型。{"\n"}
              请先在「设置 → 模型与密钥」配置 {seat.kind === "embedding" ? "嵌入" : seat.kind === "image" ? "生图" : "对话"}模型。
            </Text>
          ) : (
            providers.map((p) => (
              <View key={p.api_id} style={{ marginBottom: theme.space(3) }}>
                <Text style={styles.provider}>{p.display_name || p.api_id}</Text>
                {(p.models || [])
                  .filter((m: any) => modelMatchesKind(m, seat.kind))
                  .slice(0, 12)
                  .map((m: any) => {
                    const active = currentApi === p.api_id && currentModel === m.id;
                    return (
                      <Pressable
                        key={m.id}
                        onPress={() => onSwear(p.api_id, m.id)}
                        style={[styles.pickRow, active && styles.pickRowOn]}
                      >
                        <Text style={[styles.pickName, active && { color: theme.color.accentBright }]} numberOfLines={1}>
                          {m.name || m.id}
                        </Text>
                        {active ? <Text style={styles.pickCheck}>✦</Text> : null}
                      </Pressable>
                    );
                  })}
              </View>
            ))
          )}
        </ScrollView>
      </View>
    </Modal>
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

  intro: { paddingHorizontal: theme.space(1), paddingVertical: theme.space(2) },
  introText: {
    fontFamily: theme.font.proseItalic,
    fontSize: theme.size.sm,
    color: theme.color.textFaint,
    lineHeight: 22,
  },

  // A long oaken rule separating the head seat from the rest of the council.
  tableRule: {
    height: 1,
    backgroundColor: theme.color.accentSoft,
    marginVertical: theme.space(3),
    marginHorizontal: theme.space(2),
  },

  // A seat at the council table.
  seat: {
    flexDirection: "row",
    alignItems: "center",
    gap: theme.space(3),
    padding: theme.space(3),
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    backgroundColor: theme.color.bgCard,
  },
  headSeat: {
    borderWidth: 1.5,
    borderColor: theme.color.accentSoft,
    backgroundColor: "rgba(232,146,58,0.07)",
    shadowColor: theme.color.accent,
    shadowOpacity: 0.35,
    shadowRadius: 14,
    shadowOffset: { width: 0, height: 4 },
    elevation: 4,
  },

  shield: {
    width: 36,
    height: 36,
    borderRadius: theme.radius.sm,
    borderWidth: 1,
    borderColor: theme.color.surfaceLineStrong,
    backgroundColor: theme.color.bgInput,
    alignItems: "center",
    justifyContent: "center",
  },
  shieldHead: {
    borderColor: theme.color.accent,
    backgroundColor: theme.color.accentGhost,
    shadowColor: theme.color.accent,
    shadowOpacity: 0.5,
    shadowRadius: 8,
    elevation: 3,
  },
  sigil: { fontSize: 18, color: theme.color.accent },
  sigilHead: { color: theme.color.accentBright, fontSize: 20 },

  role: {
    fontFamily: theme.font.display,
    fontSize: theme.size.md,
    color: theme.color.text,
    letterSpacing: 0.4,
  },
  roleHead: { color: theme.color.accentBright, fontSize: theme.size.md + 1 },
  duty: { fontFamily: theme.font.prose, fontSize: theme.size.xs, color: theme.color.textFaint, lineHeight: 17 },

  // Brass plaque holding the sworn model — narrow column so the row keeps its rhythm.
  plaqueWrap: { width: 116, alignItems: "flex-end" },
  plaque: {
    paddingHorizontal: theme.space(2),
    paddingVertical: theme.space(2),
    borderRadius: theme.radius.sm,
    borderWidth: 1,
    borderColor: theme.color.accentSoft,
    backgroundColor: theme.color.accentGhost,
    minWidth: 100,
    maxWidth: 116,
    alignItems: "flex-end",
  },
  plaqueInherit: { borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgInput },
  plaqueEmpty: { borderColor: palette.blood + "66", backgroundColor: palette.blood + "12" },
  plaqueLabel: {
    fontFamily: theme.font.proseSemi,
    fontSize: theme.size.xs,
    color: theme.color.accentBright,
    letterSpacing: 0.3,
  },
  plaqueInheritText: { color: theme.color.textDim },
  plaqueEmptyText: { color: palette.blood },
  plaqueSub: {
    fontFamily: theme.font.mono,
    fontSize: 9,
    color: theme.color.textFaint,
    marginTop: 2,
    letterSpacing: 0.3,
  },

  // Slide-up picker sheet
  fill: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: theme.color.scrim },
  pickerSheet: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    maxHeight: "78%",
    backgroundColor: "rgba(18,14,10,0.94)",
    borderTopLeftRadius: theme.radius.xl,
    borderTopRightRadius: theme.radius.xl,
    borderWidth: 1,
    borderColor: theme.color.surfaceLineStrong,
    overflow: "hidden",
    paddingHorizontal: theme.space(5),
    paddingTop: theme.space(3),
  },
  grab: {
    alignSelf: "center",
    width: 44,
    height: 4,
    borderRadius: 2,
    backgroundColor: theme.color.surfaceLineStrong,
    marginBottom: theme.space(3),
  },
  pickerKicker: {
    fontFamily: theme.font.displaySemi,
    fontSize: theme.size.xs,
    letterSpacing: 3,
    textTransform: "uppercase",
    color: theme.color.accent,
  },
  pickerTitle: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.text, letterSpacing: 1, marginBottom: theme.space(3) },
  pickerBody: { maxHeight: 460 },
  provider: {
    fontFamily: theme.font.displaySemi,
    fontSize: theme.size.xs,
    letterSpacing: 2,
    textTransform: "uppercase",
    color: theme.color.accent,
    marginBottom: theme.space(2),
    marginTop: theme.space(1),
  },
  pickRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: theme.space(3),
    paddingHorizontal: theme.space(3),
    borderRadius: theme.radius.sm,
  },
  pickRowOn: { backgroundColor: theme.color.accentGhost },
  pickName: { flex: 1, fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.textDim },
  pickSub: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint, marginLeft: theme.space(2) },
  pickCheck: { color: theme.color.accentBright, fontSize: theme.size.md },
  empty: {
    fontFamily: theme.font.proseItalic,
    fontSize: theme.size.base,
    color: theme.color.textFaint,
    lineHeight: 22,
    paddingVertical: theme.space(8),
    textAlign: "center",
  },
});
