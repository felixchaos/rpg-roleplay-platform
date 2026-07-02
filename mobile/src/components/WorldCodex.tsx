/**
 * World Codex — the living state of the story, laid bare and editable. Three movements:
 * the World scalars (where/when/weather/mood the engine tracks), the web of Relationships
 * (each NPC's standing toward you), and Worldline flags (branching variables the story
 * remembers). Every edit dispatches to the engine's own tools and re-reads fresh state,
 * so the panel always mirrors what the GM actually believes about the world.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { BlurView } from "expo-blur";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeIn } from "react-native-reanimated";
import { game, world, relationships, worldline } from "@/api";
import { ApiError } from "@/api/http";
import { theme, palette } from "@/theme/theme";

type Tab = "world" | "bonds" | "flags";

// The scalar world keys the backend allows direct edits on, with display labels + glyphs.
const WORLD_KEYS: { key: string; label: string; glyph: string }[] = [
  { key: "location", label: "所在", glyph: "⌖" },
  { key: "time", label: "时刻", glyph: "☾" },
  { key: "weather", label: "天候", glyph: "❄" },
  { key: "atmosphere", label: "氛围", glyph: "☉" },
  { key: "phase", label: "阶段", glyph: "◑" },
  { key: "season", label: "时节", glyph: "❦" },
  { key: "region", label: "疆域", glyph: "⎈" },
];

type Bond = { name: string; status: string };
type Flag = { key: string; value: string };
type Entity = { name: string; type?: string; hp?: number; max_hp?: number; avatar?: string; card_id?: number };

// Relationship status → color, matching the documented palette (友好/信任 green, 戒备
// orange, 亲近 blue, 敌意 red, else neutral/uncolored).
function statusColor(status: string): string | null {
  const s = (status || "").toLowerCase();
  if (/友好|信任|同盟|友/.test(s)) return palette.jade;
  if (/戒备|警惕|提防/.test(s)) return palette.ember;
  if (/亲近|亲密|爱慕/.test(s)) return palette.arcane;
  if (/敌意|敌对|仇/.test(s)) return palette.blood;
  return null;
}

const ENTITY_TYPE_LABEL: Record<string, string> = { npc: "NPC", enemy: "敌人", ally: "盟友", unknown: "待确认" };

// Active entities present in the current room/encounter; defeated ones are dropped.
function readEntities(state: any): Entity[] {
  const raw = state?.active_entities || state?.scene?.active_entities || state?.encounter?.enemies || [];
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((e: any) => e && String(e.status || "").toLowerCase() !== "defeated")
    .map((e: any) => ({
      name: String(e.name || e.id || ""),
      type: String(e.type || e.kind || (e.is_enemy ? "enemy" : "npc")),
      hp: typeof e.hp === "number" ? e.hp : undefined,
      max_hp: typeof e.max_hp === "number" ? e.max_hp : undefined,
      avatar: e.avatar_path || e.avatar || undefined,
      card_id: e.card_id ?? undefined,
    }))
    .filter((e: Entity) => e.name);
}

function readWorld(state: any): Record<string, string> {
  const w = state?.state?.world || state?.world || {};
  const out: Record<string, string> = {};
  for (const { key } of WORLD_KEYS) {
    const v = w[key];
    if (v != null && typeof v !== "object") out[key] = String(v);
    else out[key] = "";
  }
  return out;
}

function readBonds(state: any): Bond[] {
  const rels = state?.state?.relationships || state?.relationships || {};
  if (Array.isArray(rels)) {
    return rels.map((r: any) => ({ name: String(r.character || r.name || ""), status: String(r.status || r.value || "") })).filter((b) => b.name);
  }
  return Object.entries(rels).map(([name, v]: [string, any]) => ({
    name,
    status: typeof v === "string" ? v : String(v?.status ?? v?.value ?? ""),
  })).filter((b) => b.name);
}

function readFlags(state: any): Flag[] {
  const wl = state?.state?.worldline || state?.worldline || state?.state?.worldline_variables || {};
  const vars = wl?.variables || wl;
  if (!vars || typeof vars !== "object") return [];
  return Object.entries(vars).map(([key, v]: [string, any]) => ({
    key,
    value: typeof v === "object" ? JSON.stringify(v) : String(v),
  }));
}

export function WorldCodex({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState<Tab>("world");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [worldVals, setWorldVals] = useState<Record<string, string>>({});
  const [bonds, setBonds] = useState<Bond[]>([]);
  const [flags, setFlags] = useState<Flag[]>([]);
  const [entities, setEntities] = useState<Entity[]>([]);
  const [rules, setRules] = useState<string[]>([]);
  const [keywords, setKeywords] = useState<string[]>([]);
  // draft inputs
  const [newBond, setNewBond] = useState<Bond>({ name: "", status: "" });
  const [newFlag, setNewFlag] = useState<Flag>({ key: "", value: "" });

  const hydrate = useCallback((state: any) => {
    setWorldVals(readWorld(state));
    setBonds(readBonds(state));
    setEntities(readEntities(state));
    setFlags(readFlags(state));
    const s = state?.state ?? state ?? {};
    const w = s.world || {};
    const rawRules = w.rules || w.constraints || s.world_rules || [];
    setRules(Array.isArray(rawRules) ? rawRules.map((r: any) => (typeof r === "string" ? r : r?.text || r?.rule || "")).filter(Boolean) : []);
    const rawKw = s.keywords || w.keywords || s.turn_keywords || [];
    setKeywords(Array.isArray(rawKw) ? rawKw.map((k: any) => (typeof k === "string" ? k : k?.text || k?.term || "")).filter(Boolean) : []);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      hydrate(await game.state());
    } catch {
      /* leave */
    } finally {
      setLoading(false);
    }
  }, [hydrate]);

  useEffect(() => {
    if (visible) {
      setTab("world");
      refresh();
    }
  }, [visible, refresh]);

  const applyResult = (res: any) => {
    if (res?.state) hydrate({ state: res.state });
    else refresh();
  };

  const saveWorldKey = async (key: string) => {
    const value = (worldVals[key] || "").trim();
    if (!value) return;
    setBusy(true);
    try {
      applyResult(await world.set(key, value));
    } catch (e) {
      Alert.alert("更新失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setBusy(false);
    }
  };

  const addBond = async () => {
    const { name, status } = newBond;
    if (!name.trim() || !status.trim()) return;
    setBusy(true);
    try {
      applyResult(await relationships.set(name.trim(), status.trim()));
      setNewBond({ name: "", status: "" });
    } catch (e) {
      Alert.alert("更新失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setBusy(false);
    }
  };

  const removeBond = (name: string) => {
    Alert.alert("断开羁绊", `移除与「${name}」的关系记录？`, [
      { text: "取消", style: "cancel" },
      { text: "移除", style: "destructive", onPress: async () => {
        try { applyResult(await relationships.remove(name)); } catch (e) { Alert.alert("失败", e instanceof ApiError ? e.message : "请重试"); }
      } },
    ]);
  };

  const addFlag = async () => {
    const { key, value } = newFlag;
    if (!key.trim()) return;
    setBusy(true);
    try {
      applyResult(await worldline.set(key.trim(), value.trim()));
      setNewFlag({ key: "", value: "" });
    } catch (e) {
      Alert.alert("更新失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setBusy(false);
    }
  };

  const removeFlag = (key: string) => {
    Alert.alert("抹除标记", `删除世界线变量「${key}」？`, [
      { text: "取消", style: "cancel" },
      { text: "删除", style: "destructive", onPress: async () => {
        try { applyResult(await worldline.remove(key)); } catch (e) { Alert.alert("失败", e instanceof ApiError ? e.message : "请重试"); }
      } },
    ]);
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
        <BlurView intensity={30} tint="dark" style={styles.fill} />
        <View style={styles.grabber} />
        <Text style={styles.title}>世界典藏</Text>

        <View style={styles.tabs}>
          {(["world", "bonds", "flags"] as Tab[]).map((t) => (
            <Pressable key={t} onPress={() => setTab(t)} style={[styles.tab, tab === t && styles.tabActive]}>
              <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
                {t === "world" ? "世相" : t === "bonds" ? "羁绊" : "世界线"}
              </Text>
            </Pressable>
          ))}
        </View>

        {loading ? (
          <ActivityIndicator color={theme.color.accent} style={{ marginVertical: theme.space(10) }} />
        ) : (
          <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
            <ScrollView style={styles.body} keyboardShouldPersistTaps="handled" contentContainerStyle={{ gap: theme.space(3), paddingVertical: theme.space(2) }}>
              {tab === "world" ? (
                <>
                  {WORLD_KEYS.map((wk, i) => (
                    <Animated.View key={wk.key} entering={FadeIn.delay(i * 30).duration(280)} style={styles.worldRow}>
                      <Text style={styles.worldGlyph}>{wk.glyph}</Text>
                      <Text style={styles.worldLabel}>{wk.label}</Text>
                      <TextInput
                        value={worldVals[wk.key] || ""}
                        onChangeText={(t) => setWorldVals((p) => ({ ...p, [wk.key]: t }))}
                        onSubmitEditing={() => saveWorldKey(wk.key)}
                        onBlur={() => saveWorldKey(wk.key)}
                        placeholder="—"
                        placeholderTextColor={theme.color.textFaint}
                        style={styles.worldInput}
                      />
                    </Animated.View>
                  ))}

                  <Text style={styles.layerLabel}>世界规则</Text>
                  {rules.length === 0 ? (
                    <Text style={styles.empty}>本剧本暂未配置世界规则。</Text>
                  ) : (
                    rules.map((r, i) => (
                      <View key={i} style={styles.ruleRow}>
                        <Text style={styles.ruleGlyph}>⟐</Text>
                        <Text style={styles.ruleText}>{r}</Text>
                      </View>
                    ))
                  )}

                  {keywords.length > 0 ? (
                    <>
                      <Text style={styles.layerLabel}>本轮重要词条</Text>
                      <View style={styles.kwWrap}>
                        {keywords.map((k, i) => (
                          <View key={i} style={styles.kwChip}><Text style={styles.kwText}>{k}</Text></View>
                        ))}
                      </View>
                    </>
                  ) : null}
                </>
              ) : null}

              {tab === "bonds" ? (
                <>
                  <Text style={styles.layerLabel}>当前在场</Text>
                  {entities.length === 0 ? (
                    <Text style={styles.empty}>本房间暂无在场人物。</Text>
                  ) : (
                    entities.map((e, i) => {
                      const initial = (e.name || "?").trim().charAt(0).toUpperCase();
                      const hpPct = e.max_hp && e.max_hp > 0 ? Math.max(0, Math.min(100, Math.round((100 * (e.hp ?? 0)) / e.max_hp))) : null;
                      return (
                        <Animated.View key={e.name + i} entering={FadeIn.delay(i * 25).duration(260)} style={styles.entityRow}>
                          <View style={styles.entityAvatar}><Text style={styles.entityInitial}>{initial}</Text></View>
                          <View style={{ flex: 1 }}>
                            <View style={styles.entityHead}>
                              <Text style={styles.bondName} numberOfLines={1}>{e.name}</Text>
                              <View style={styles.typeTag}><Text style={styles.typeTagText}>{ENTITY_TYPE_LABEL[(e.type || "").toLowerCase()] || e.type}</Text></View>
                              {e.card_id != null ? <Text style={styles.cardLinkTag}>卡</Text> : null}
                            </View>
                            {hpPct != null ? (
                              <View style={styles.entityHpTrack}><View style={[styles.entityHpFill, { width: `${hpPct}%`, backgroundColor: hpPct > 30 ? palette.jade : palette.blood }]} /></View>
                            ) : null}
                          </View>
                        </Animated.View>
                      );
                    })
                  )}

                  <Text style={styles.layerLabel}>关系</Text>
                  {bonds.length === 0 ? <Text style={styles.empty}>尚无羁绊。你与世界的联系将在此显形。</Text> : null}
                  {bonds.map((b, i) => {
                    const col = statusColor(b.status);
                    return (
                      <Animated.View key={b.name + i} entering={FadeIn.delay(i * 30).duration(280)} style={styles.bondRow}>
                        <View style={{ flex: 1 }}>
                          <Text style={styles.bondName}>{b.name}</Text>
                          {b.status ? (
                            <View style={[styles.statusTag, col ? { backgroundColor: col + "22", borderColor: col + "66" } : { borderColor: theme.color.surfaceLine }]}>
                              <Text style={[styles.statusTagText, col ? { color: col } : { color: theme.color.textDim }]}>{b.status}</Text>
                            </View>
                          ) : <Text style={styles.bondStatus}>—</Text>}
                        </View>
                        <Pressable onPress={() => removeBond(b.name)} hitSlop={8}>
                          <Text style={styles.removeGlyph}>✕</Text>
                        </Pressable>
                      </Animated.View>
                    );
                  })}
                  <View style={styles.addBlock}>
                    <TextInput value={newBond.name} onChangeText={(t) => setNewBond((p) => ({ ...p, name: t }))} placeholder="人物" placeholderTextColor={theme.color.textFaint} style={[styles.addInput, { flex: 1 }]} />
                    <TextInput value={newBond.status} onChangeText={(t) => setNewBond((p) => ({ ...p, status: t }))} placeholder="关系状态" placeholderTextColor={theme.color.textFaint} style={[styles.addInput, { flex: 1.4 }]} />
                    <Pressable onPress={addBond} disabled={busy} style={[styles.addBtn, busy && { opacity: 0.4 }]}><Text style={styles.addGlyph}>＋</Text></Pressable>
                  </View>
                </>
              ) : null}

              {tab === "flags" ? (
                <>
                  {flags.length === 0 ? <Text style={styles.empty}>世界线尚无变量。命运的开关待你拨动。</Text> : null}
                  {flags.map((f, i) => (
                    <Animated.View key={f.key + i} entering={FadeIn.delay(i * 30).duration(280)} style={styles.bondRow}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.flagKey}>{f.key}</Text>
                        <Text style={styles.bondStatus}>{f.value || "—"}</Text>
                      </View>
                      <Pressable onPress={() => removeFlag(f.key)} hitSlop={8}>
                        <Text style={styles.removeGlyph}>✕</Text>
                      </Pressable>
                    </Animated.View>
                  ))}
                  <View style={styles.addBlock}>
                    <TextInput value={newFlag.key} onChangeText={(t) => setNewFlag((p) => ({ ...p, key: t }))} placeholder="变量名" placeholderTextColor={theme.color.textFaint} autoCapitalize="none" style={[styles.addInput, { flex: 1 }]} />
                    <TextInput value={newFlag.value} onChangeText={(t) => setNewFlag((p) => ({ ...p, value: t }))} placeholder="值" placeholderTextColor={theme.color.textFaint} style={[styles.addInput, { flex: 1 }]} />
                    <Pressable onPress={addFlag} disabled={busy} style={[styles.addBtn, busy && { opacity: 0.4 }]}><Text style={styles.addGlyph}>＋</Text></Pressable>
                  </View>
                </>
              ) : null}
            </ScrollView>
          </KeyboardAvoidingView>
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  fill: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: theme.color.scrim },
  sheet: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    maxHeight: "82%",
    backgroundColor: "rgba(20,16,12,0.9)",
    borderTopLeftRadius: theme.radius.xl,
    borderTopRightRadius: theme.radius.xl,
    borderWidth: 1,
    borderColor: theme.color.surfaceLineStrong,
    overflow: "hidden",
    paddingHorizontal: theme.space(5),
    paddingTop: theme.space(3),
  },
  grabber: { alignSelf: "center", width: 44, height: 4, borderRadius: 2, backgroundColor: theme.color.surfaceLineStrong, marginBottom: theme.space(3) },
  title: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.text, letterSpacing: 1, marginBottom: theme.space(3) },
  tabs: { flexDirection: "row", gap: theme.space(2), marginBottom: theme.space(3) },
  tab: { flex: 1, paddingVertical: theme.space(2.5), alignItems: "center", borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine },
  tabActive: { backgroundColor: theme.color.accentGhost, borderColor: theme.color.accentSoft },
  tabText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1.5, textTransform: "uppercase", color: theme.color.textFaint },
  tabTextActive: { color: theme.color.accentBright },
  body: { maxHeight: 420 },
  worldRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3) },
  worldGlyph: { fontSize: 18, color: theme.color.accent, width: 24, textAlign: "center" },
  worldLabel: { fontFamily: theme.font.displaySemi, fontSize: theme.size.sm, color: theme.color.textDim, width: 52 },
  worldInput: { flex: 1, backgroundColor: theme.color.bgInput, borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine, paddingHorizontal: theme.space(3), paddingVertical: theme.space(2.5), color: theme.color.text, fontFamily: theme.font.prose, fontSize: theme.size.base },
  ruleRow: { flexDirection: "row", gap: theme.space(2), alignItems: "flex-start", paddingVertical: theme.space(1) },
  ruleGlyph: { fontSize: theme.size.sm, color: theme.color.magic, marginTop: 2 },
  ruleText: { flex: 1, fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim, lineHeight: 21 },
  kwWrap: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(2) },
  kwChip: { backgroundColor: theme.color.accentGhost, borderWidth: 1, borderColor: theme.color.accentSoft, paddingHorizontal: theme.space(3), paddingVertical: theme.space(1), borderRadius: theme.radius.pill },
  kwText: { fontFamily: theme.font.proseMedium, fontSize: theme.size.sm, color: theme.color.accentBright },
  bondRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), paddingVertical: theme.space(3), borderBottomWidth: 1, borderBottomColor: theme.color.surfaceLine },
  bondName: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  bondStatus: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim, marginTop: 2 },
  layerLabel: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 2, textTransform: "uppercase", color: theme.color.accent, marginTop: theme.space(2) },
  entityRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), paddingVertical: theme.space(2) },
  entityAvatar: { width: 36, height: 36, borderRadius: theme.radius.sm, backgroundColor: theme.color.bgInput, borderWidth: 1, borderColor: theme.color.surfaceLineStrong, alignItems: "center", justifyContent: "center" },
  entityInitial: { fontFamily: theme.font.display, fontSize: theme.size.md, color: theme.color.accent },
  entityHead: { flexDirection: "row", alignItems: "center", gap: theme.space(2) },
  typeTag: { paddingHorizontal: theme.space(2), paddingVertical: 1, borderRadius: theme.radius.sm, backgroundColor: theme.color.surfaceLine },
  typeTagText: { fontFamily: theme.font.mono, fontSize: 10, color: theme.color.textDim },
  cardLinkTag: { fontFamily: theme.font.mono, fontSize: 10, color: theme.color.accent, borderWidth: 1, borderColor: theme.color.accentSoft, borderRadius: theme.radius.sm, paddingHorizontal: theme.space(1.5) },
  entityHpTrack: { height: 3, borderRadius: 2, backgroundColor: theme.color.bgInput, overflow: "hidden", marginTop: 4 },
  entityHpFill: { height: "100%" },
  statusTag: { alignSelf: "flex-start", marginTop: 3, paddingHorizontal: theme.space(2), paddingVertical: 1, borderRadius: theme.radius.sm, borderWidth: 1 },
  statusTagText: { fontFamily: theme.font.proseMedium, fontSize: theme.size.xs },
  flagKey: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.magic },
  removeGlyph: { fontSize: 16, color: theme.color.danger, paddingHorizontal: theme.space(2) },
  empty: { fontFamily: theme.font.proseItalic, fontSize: theme.size.md, color: theme.color.textFaint, textAlign: "center", paddingVertical: theme.space(8) },
  addBlock: { flexDirection: "row", alignItems: "center", gap: theme.space(2), marginTop: theme.space(3), paddingTop: theme.space(3), borderTopWidth: 1, borderTopColor: theme.color.surfaceLine },
  addInput: { backgroundColor: theme.color.bgInput, borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine, paddingHorizontal: theme.space(3), paddingVertical: theme.space(2.5), color: theme.color.text, fontFamily: theme.font.prose, fontSize: theme.size.sm },
  addBtn: { width: 40, height: 40, borderRadius: theme.radius.pill, backgroundColor: theme.color.accent, alignItems: "center", justifyContent: "center" },
  addGlyph: { fontSize: 20, color: theme.color.bg },
});
