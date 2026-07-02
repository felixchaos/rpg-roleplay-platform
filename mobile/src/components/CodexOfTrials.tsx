/**
 * Codex of Trials — the scripted-game instrument panel. Three faces of a living campaign:
 *  · 状态 (Status) — the player_character sheet: an HP gauge that bleeds from jade to blood,
 *    level, attributes, conditions, and the satchel of inventory.
 *  · 规则 (Rules) — the 5E combat bench: current room + exits to move through, an encounter
 *    turn loop (start / next / enemy strike), and a scrolling dice ledger.
 *  · 纪年 (Timeline) — the story's anchors and worldline progress.
 * Reads GET /api/state (returned directly, no {state} wrapper) and re-reads after each
 * rules action so the sheet always mirrors the engine's truth.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { BlurView } from "expo-blur";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeIn } from "react-native-reanimated";
import { game, rules } from "@/api";
import { ApiError } from "@/api/http";
import { theme, palette } from "@/theme/theme";

type Tab = "status" | "rules" | "timeline";

function hpColor(pct: number): string {
  if (pct > 60) return palette.jade;
  if (pct > 30) return theme.color.accent;
  return theme.color.danger;
}

export function CodexOfTrials({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState<Tab>("status");
  const [state, setState] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await game.state();
      setState(res?.state ?? res);
    } catch {
      /* leave */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    setTab("status");
    // Inline the fetch so we can guard setState against unmount during async resolution.
    setLoading(true);
    game.state()
      .then((res) => { if (alive) setState(res?.state ?? res); })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [visible]);

  const act = async (fn: () => Promise<any>) => {
    if (acting) return;
    setActing(true);
    try {
      const r = await fn();
      setState(r?.state ?? (await game.state()));
    } catch (e) {
      Alert.alert("行动失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setActing(false);
    }
  };

  const pc = state?.player_character || {};
  const scene = state?.scene || {};
  const encounter = state?.encounter || {};
  const ruleset = state?.ruleset || {};
  const diceLog: any[] = Array.isArray(state?.dice_log) ? state.dice_log : [];
  const timeline: any[] = Array.isArray(state?.timeline) ? state.timeline : (state?.worldline?.anchors || []);

  const hp = Number(pc.hp ?? 0);
  const maxHp = Number(pc.max_hp ?? 0);
  const hpPct = maxHp > 0 ? Math.max(0, Math.min(100, Math.round((100 * hp) / maxHp))) : 0;
  const inventory: any[] = Array.isArray(pc.inventory) ? pc.inventory : [];
  const conditions: string[] = Array.isArray(pc.conditions) ? pc.conditions : [];
  const attrs: Record<string, any> = pc.attributes || pc.abilities || {};
  const room = scene.current_room || {};
  const exits: any[] = Array.isArray(room.exits) ? room.exits : Array.isArray(scene.exits) ? scene.exits : [];
  const enemies: any[] = Array.isArray(encounter.enemies) ? encounter.enemies : [];
  const inCombat = !!encounter.active || enemies.length > 0;

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
        <BlurView intensity={30} tint="dark" style={styles.fill} />
        <View style={styles.grabber} />
        <Text style={styles.title}>试炼之书</Text>

        <View style={styles.tabs}>
          {(["status", "rules", "timeline"] as Tab[]).map((t) => (
            <Pressable key={t} onPress={() => setTab(t)} style={[styles.tab, tab === t && styles.tabActive]}>
              <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
                {t === "status" ? "状态" : t === "rules" ? "规则" : "纪年"}
              </Text>
            </Pressable>
          ))}
        </View>

        {loading && !state ? (
          <ActivityIndicator color={theme.color.accent} style={{ marginVertical: theme.space(10) }} />
        ) : (
          <ScrollView style={styles.body} contentContainerStyle={{ paddingVertical: theme.space(2), gap: theme.space(3) }}>
            {tab === "status" ? (
              <>
                <View style={styles.nameRow}>
                  <Text style={styles.pcName}>{pc.name || "无名旅人"}</Text>
                  {pc.level ? <Text style={styles.level}>Lv {pc.level}</Text> : null}
                </View>
                {maxHp > 0 ? (
                  <View style={{ gap: theme.space(1) }}>
                    <View style={styles.hpHead}>
                      <Text style={styles.hpLabel}>生命</Text>
                      <Text style={styles.hpVal}>{hp} / {maxHp}</Text>
                    </View>
                    <View style={styles.hpTrack}>
                      <View style={[styles.hpFill, { width: `${hpPct}%`, backgroundColor: hpColor(hpPct) }]} />
                    </View>
                  </View>
                ) : null}
                {conditions.length > 0 ? (
                  <View style={styles.chipRow}>
                    {conditions.map((c, i) => <View key={i} style={styles.condChip}><Text style={styles.condText}>{c}</Text></View>)}
                  </View>
                ) : null}
                {Object.keys(attrs).length > 0 ? (
                  <View style={styles.attrGrid}>
                    {Object.entries(attrs).map(([k, v]) => (
                      <View key={k} style={styles.attrCard}>
                        <Text style={styles.attrVal}>{typeof v === "object" ? (v as any)?.score ?? "—" : String(v)}</Text>
                        <Text style={styles.attrKey}>{k.slice(0, 3).toUpperCase()}</Text>
                      </View>
                    ))}
                  </View>
                ) : null}
                <Text style={styles.sectionLabel}>行囊 · {inventory.length}</Text>
                {inventory.length === 0 ? (
                  <Text style={styles.empty}>空空如也。</Text>
                ) : (
                  inventory.map((it, i) => (
                    <View key={i} style={styles.invRow}>
                      <Text style={styles.invName}>{it.name || it.item || String(it)}</Text>
                      {it.qty || it.quantity ? <Text style={styles.invQty}>×{it.qty || it.quantity}</Text> : null}
                    </View>
                  ))
                )}
                {scene.location || scene.name ? <Text style={styles.locLine}>⌖ {scene.location || scene.name}</Text> : null}
              </>
            ) : null}

            {tab === "rules" ? (
              !scene.module_id && !ruleset.id && !inCombat ? (
                <Text style={styles.empty}>本场景未加载战斗模组。规则引擎在 5E 兼容模组中生效。</Text>
              ) : (
                <>
                  {room.name || room.title ? (
                    <View style={styles.roomCard}>
                      <Text style={styles.roomName}>{room.name || room.title}</Text>
                      {room.description ? <Text style={styles.roomDesc} numberOfLines={4}>{room.description}</Text> : null}
                    </View>
                  ) : null}
                  {exits.length > 0 ? (
                    <>
                      <Text style={styles.sectionLabel}>出口</Text>
                      <View style={styles.chipRow}>
                        {exits.map((ex, i) => {
                          const to = typeof ex === "string" ? ex : ex.to || ex.id || ex.name;
                          return (
                            <Pressable key={i} onPress={() => act(() => rules.move(to))} disabled={acting} style={styles.moveChip}>
                              <Text style={styles.moveText}>→ {typeof ex === "string" ? ex : ex.label || to}</Text>
                            </Pressable>
                          );
                        })}
                      </View>
                    </>
                  ) : null}
                  {inCombat ? (
                    <>
                      <Text style={styles.sectionLabel}>遭遇 · {enemies.length} 敌</Text>
                      {enemies.map((en, i) => {
                        const ehp = Number(en.hp ?? 0), emax = Number(en.max_hp ?? 0);
                        const epct = emax > 0 ? Math.max(0, Math.min(100, Math.round((100 * ehp) / emax))) : 0;
                        return (
                          <View key={i} style={styles.enemyRow}>
                            <View style={{ flex: 1 }}>
                              <Text style={styles.enemyName}>{en.name || en.id}</Text>
                              <View style={styles.hpTrackSm}><View style={[styles.hpFill, { width: `${epct}%`, backgroundColor: theme.color.danger }]} /></View>
                            </View>
                            <Pressable onPress={() => act(() => rules.encounterEnemy(en.id || en.name))} disabled={acting} style={styles.strikeBtn}>
                              <Text style={styles.strikeText}>结算</Text>
                            </Pressable>
                          </View>
                        );
                      })}
                      <Pressable onPress={() => act(() => rules.encounterNext())} disabled={acting} style={styles.nextBtn}>
                        <Text style={styles.nextText}>{acting ? "结算中…" : "下一回合 ▷"}</Text>
                      </Pressable>
                    </>
                  ) : null}
                  {diceLog.length > 0 ? (
                    <>
                      <Text style={styles.sectionLabel}>骰枢</Text>
                      {diceLog.slice(-8).reverse().map((d, i) => (
                        <View key={i} style={styles.diceRow}>
                          <Text style={styles.diceExpr}>{d.expr || d.notation || d.formula || "?"}</Text>
                          <Text style={styles.diceResult}>{d.total ?? d.result ?? "?"}</Text>
                        </View>
                      ))}
                    </>
                  ) : null}
                </>
              )
            ) : null}

            {tab === "timeline" ? (
              timeline.length === 0 ? (
                <Text style={styles.empty}>尚无纪年锚点。故事推进时，关键时刻会在此刻下印记。</Text>
              ) : (
                timeline.map((a, i) => (
                  <Animated.View key={i} entering={FadeIn.delay(i * 30).duration(280)} style={styles.tlRow}>
                    <View style={styles.tlDot} />
                    <View style={{ flex: 1 }}>
                      <Text style={styles.tlTitle}>{a.title || a.label || a.name || `锚点 ${i + 1}`}</Text>
                      {a.time || a.story_time ? <Text style={styles.tlTime}>{a.time || a.story_time}</Text> : null}
                      {a.description || a.summary ? <Text style={styles.tlDesc} numberOfLines={3}>{a.description || a.summary}</Text> : null}
                    </View>
                  </Animated.View>
                ))
              )
            ) : null}
          </ScrollView>
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  fill: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: theme.color.scrim },
  sheet: { position: "absolute", left: 0, right: 0, bottom: 0, maxHeight: "84%", backgroundColor: palette.scrimCard90, borderTopLeftRadius: theme.radius.xl, borderTopRightRadius: theme.radius.xl, borderWidth: 1, borderColor: theme.color.surfaceLineStrong, overflow: "hidden", paddingHorizontal: theme.space(5), paddingTop: theme.space(3) },
  grabber: { alignSelf: "center", width: 44, height: 4, borderRadius: 2, backgroundColor: theme.color.surfaceLineStrong, marginBottom: theme.space(3) },
  title: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.text, letterSpacing: 1, marginBottom: theme.space(3) },
  tabs: { flexDirection: "row", gap: theme.space(2), marginBottom: theme.space(3) },
  tab: { flex: 1, paddingVertical: theme.space(2.5), alignItems: "center", borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine },
  tabActive: { backgroundColor: theme.color.accentGhost, borderColor: theme.color.accentSoft },
  tabText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1.5, textTransform: "uppercase", color: theme.color.textFaint },
  tabTextActive: { color: theme.color.accentBright },
  body: { maxHeight: 460 },
  nameRow: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between" },
  pcName: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text },
  level: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.accentBright },
  hpHead: { flexDirection: "row", justifyContent: "space-between" },
  hpLabel: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 2, textTransform: "uppercase", color: theme.color.textFaint },
  hpVal: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.text },
  hpTrack: { height: 8, borderRadius: 4, backgroundColor: theme.color.bgInput, borderWidth: 1, borderColor: theme.color.surfaceLine, overflow: "hidden" },
  hpTrackSm: { height: 4, borderRadius: 2, backgroundColor: theme.color.bgInput, overflow: "hidden", marginTop: 3 },
  hpFill: { height: "100%" },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(2) },
  condChip: { backgroundColor: palette.blood15, paddingHorizontal: theme.space(3), paddingVertical: theme.space(1), borderRadius: theme.radius.pill, borderWidth: 1, borderColor: palette.blood40 },
  condText: { fontFamily: theme.font.proseMedium, fontSize: theme.size.sm, color: theme.color.danger },
  attrGrid: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(2) },
  attrCard: { width: "30%", flexGrow: 1, alignItems: "center", paddingVertical: theme.space(3), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  attrVal: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.accentBright },
  attrKey: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint, letterSpacing: 1 },
  sectionLabel: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 2, textTransform: "uppercase", color: theme.color.accent, marginTop: theme.space(2) },
  empty: { fontFamily: theme.font.proseItalic, fontSize: theme.size.md, color: theme.color.textFaint, paddingVertical: theme.space(6), textAlign: "center", lineHeight: 22 },
  invRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: theme.space(2), borderBottomWidth: 1, borderBottomColor: theme.color.surfaceLine },
  invName: { fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.text },
  invQty: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.textDim },
  locLine: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, marginTop: theme.space(2) },
  roomCard: { padding: theme.space(4), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard, gap: theme.space(2) },
  roomName: { fontFamily: theme.font.displaySemi, fontSize: theme.size.md, color: theme.color.accentBright },
  roomDesc: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim, lineHeight: 21 },
  moveChip: { backgroundColor: theme.color.accentGhost, paddingHorizontal: theme.space(3), paddingVertical: theme.space(2), borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.accentSoft },
  moveText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.accentBright },
  enemyRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), paddingVertical: theme.space(2) },
  enemyName: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.text },
  strikeBtn: { paddingHorizontal: theme.space(3), paddingVertical: theme.space(2), borderRadius: theme.radius.sm, borderWidth: 1, borderColor: palette.blood40 },
  strikeText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1, color: theme.color.danger },
  nextBtn: { marginTop: theme.space(2), paddingVertical: theme.space(3), alignItems: "center", borderRadius: theme.radius.md, backgroundColor: theme.color.accent },
  nextText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.sm, letterSpacing: 1, color: theme.color.bg, textTransform: "uppercase" },
  diceRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: theme.space(1) },
  diceExpr: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.textDim },
  diceResult: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.accentBright },
  tlRow: { flexDirection: "row", gap: theme.space(3) },
  tlDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: theme.color.accent, marginTop: 5 },
  tlTitle: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  tlTime: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.accent, marginTop: 1 },
  tlDesc: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim, lineHeight: 20, marginTop: 2 },
});
