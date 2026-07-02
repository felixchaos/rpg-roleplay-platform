/**
 * Chamber of Verification — the 设定核对 ritual. AI extraction is fallible; this is where
 * the scribe inspects what the engine claims to have understood from the original text,
 * mends imperfect summaries, banishes false entries, then seals the chamber with the
 * "确认设定无误" oath. Three movements: a quality-marker prologue (extraction flags from
 * import_report), the canon entity ledger (with inline summary edit + banish), and a
 * timeline coda. The seal at the bottom is the lock that gates public sharing.
 *
 * Aesthetic notes:
 *   · Status banner is a wax-seal block in ember or jade, sealing the chamber's verdict.
 *   · Quality flags read as runic warnings, not log entries.
 *   · Inline edits open as a parchment-textured panel, not a dialog — feels in-place.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeIn, FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { EmberButton, RuneDivider } from "@/components/ui";
import { scripts } from "@/api";
import { ApiError } from "@/api/http";
import { theme, palette } from "@/theme/theme";

type Entity = {
  logical_key: string;
  name: string;
  type?: string;
  first_revealed_chapter?: number;
  importance?: number;
  summary?: string;
};

type ReviewFlags = {
  needs_review?: boolean;
  author_notes?: any[];
  weird_titles?: any[];
  gaps?: any[];
  cleaning?: Record<string, unknown>;
};

const TYPE_GLYPH: Record<string, string> = {
  character: "❦",
  location: "⌖",
  faction: "✦",
  item: "◈",
  concept: "✺",
};

export default function ScriptAuditScreen() {
  const { id, title } = useLocalSearchParams<{ id: string; title?: string }>();
  const scriptId = Number(id);
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [loading, setLoading] = useState(true);
  const [reviewStatus, setReviewStatus] = useState<string>("unreviewed");
  const [reviewedAt, setReviewedAt] = useState<string | null>(null);
  const [flags, setFlags] = useState<ReviewFlags>({});
  const [entities, setEntities] = useState<Entity[]>([]);
  const [timeline, setTimeline] = useState<any[]>([]);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [draftSummary, setDraftSummary] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [sealing, setSealing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await scripts.audit(scriptId);
      setReviewStatus(r?.script?.review_status || "unreviewed");
      setReviewedAt(r?.script?.reviewed_at ?? null);
      setFlags(r?.review_flags || {});
      setEntities((r?.entities || []) as Entity[]);
      setTimeline(r?.timeline || []);
    } catch (e) {
      if (e instanceof ApiError) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, [scriptId]);

  useEffect(() => {
    load();
  }, [load]);

  const startEdit = (ent: Entity) => {
    setEditingKey(ent.logical_key);
    setDraftSummary(ent.summary || "");
  };

  const saveSummary = async (ent: Entity) => {
    setBusyKey(ent.logical_key);
    try {
      await scripts.patchCanon(scriptId, {
        op: "update_entity",
        logical_key: ent.logical_key,
        summary: draftSummary.trim(),
      });
      setEntities((prev) =>
        prev.map((e) => (e.logical_key === ent.logical_key ? { ...e, summary: draftSummary.trim() } : e)),
      );
      setEditingKey(null);
    } catch (e) {
      Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试。");
    } finally {
      setBusyKey(null);
    }
  };

  const banishEntity = (ent: Entity) => {
    Alert.alert(
      "驱逐此条目",
      `「${ent.name}」将从规范实体中移除，本次操作不可撤销。重新运行提取可恢复此类条目，但手动修改将丢失。`,
      [
        { text: "留下", style: "cancel" },
        {
          text: "驱逐",
          style: "destructive",
          onPress: async () => {
            setBusyKey(ent.logical_key);
            try {
              await scripts.patchCanon(scriptId, { op: "delete_entity", logical_key: ent.logical_key });
              setEntities((prev) => prev.filter((e) => e.logical_key !== ent.logical_key));
            } catch (e) {
              Alert.alert("删除失败", e instanceof ApiError ? e.message : "请重试。");
            } finally {
              setBusyKey(null);
            }
          },
        },
      ],
    );
  };

  const seal = (next: boolean) => {
    const title = next ? "确认设定无误" : "撤回确认";
    const message = next
      ? "盖下印章后，本剧本方可对外分享。若日后重新运行提取，封印会自动解除。"
      : "撤回封印将回到「未核对」状态，你可继续修订。";
    Alert.alert(title, message, [
      { text: "取消", style: "cancel" },
      {
        text: next ? "盖下印章" : "撤回",
        onPress: async () => {
          setSealing(true);
          try {
            await scripts.markReviewed(scriptId, next);
            await load();
          } catch (e) {
            Alert.alert("操作失败", e instanceof ApiError ? e.message : "请重试。");
          } finally {
            setSealing(false);
          }
        },
      },
    ]);
  };

  const sealed = reviewStatus === "reviewed";
  const authorNotes = flags.author_notes?.length || 0;
  const weirdTitles = flags.weird_titles?.length || 0;
  const gaps = flags.gaps?.length || 0;
  const cleanedRows = Object.values(flags.cleaning || {}).reduce<number>(
    (sum, v) => sum + (typeof v === "number" ? v : 0),
    0,
  );

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Verification</Text>
          <Text style={styles.h1} numberOfLines={1}>设定核对</Text>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <ScrollView
            contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 50, paddingTop: theme.space(2) }}
            keyboardShouldPersistTaps="handled"
          >
            {/* Wax-seal status banner */}
            <Animated.View entering={FadeInDown.duration(420).springify().damping(20)}>
              <View style={[styles.seal, sealed ? styles.sealOk : styles.sealOpen]}>
                <Text style={styles.sealGlyph}>{sealed ? "✦" : "◯"}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={[styles.sealStatus, sealed && { color: palette.jade }]}>
                    {sealed ? "此剧本的设定已核对通过" : "此剧本的设定还没核对"}
                  </Text>
                  {sealed && reviewedAt ? (
                    <Text style={styles.sealMeta}>{new Date(reviewedAt).toLocaleString("zh-CN")}</Text>
                  ) : (
                    <Text style={styles.sealMeta}>核对通过后方可对外分享此剧本。</Text>
                  )}
                </View>
              </View>
              {!sealed && entities.length > 0 ? (
                <EmberButton
                  label={sealing ? "盖印中…" : "确认设定无误"}
                  onPress={() => seal(true)}
                  loading={sealing}
                  style={{ marginTop: theme.space(3) }}
                />
              ) : null}
              {sealed ? (
                <EmberButton
                  label="撤回确认"
                  variant="ghost"
                  onPress={() => seal(false)}
                  loading={sealing}
                  style={{ marginTop: theme.space(3) }}
                />
              ) : null}
            </Animated.View>

            {/* Quality markers — runic warnings */}
            {(authorNotes || weirdTitles || gaps || cleanedRows || flags.needs_review) ? (
              <Animated.View entering={FadeInDown.delay(120).duration(420)} style={styles.runicRow}>
                {flags.needs_review ? (
                  <Rune kind="warn" label="需核对" />
                ) : (
                  <Rune kind="ok" label="提取正常" />
                )}
                {authorNotes > 0 ? <Rune kind="info" label={`作者非正文 ${authorNotes}`} /> : null}
                {weirdTitles > 0 ? <Rune kind="warn" label={`怪标题 ${weirdTitles}`} /> : null}
                {gaps > 0 ? <Rune kind="warn" label={`编号缺口 ${gaps}`} /> : null}
                {cleanedRows > 0 ? <Rune kind="info" label={`广告清洗 ${cleanedRows} 行`} /> : null}
              </Animated.View>
            ) : null}

            <RuneDivider />

            {/* Canon entity ledger */}
            <Text style={styles.sectionLabel}>规范实体 · {entities.length}</Text>
            {entities.length === 0 ? (
              <Text style={styles.empty}>此剧本尚未提取规范实体。在剧本管理页触发知识提取后回来。</Text>
            ) : (
              entities.map((ent, i) => {
                const glyph = TYPE_GLYPH[(ent.type || "").toLowerCase()] || "◇";
                const editing = editingKey === ent.logical_key;
                const busy = busyKey === ent.logical_key;
                return (
                  <Animated.View
                    key={ent.logical_key}
                    entering={FadeIn.delay(Math.min(i, 16) * 24).duration(280)}
                    style={[styles.entCard, editing && styles.entCardEditing]}
                  >
                    <View style={styles.entHead}>
                      <Text style={styles.entGlyph}>{glyph}</Text>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.entName} numberOfLines={1}>{ent.name}</Text>
                        <Text style={styles.entMeta}>
                          {ent.type || "—"}
                          {ent.first_revealed_chapter ? ` · 首现 第 ${ent.first_revealed_chapter} 章` : ""}
                          {ent.importance != null ? ` · 重要度 ${ent.importance}` : ""}
                        </Text>
                      </View>
                      {!editing ? (
                        <View style={styles.entActions}>
                          <Pressable onPress={() => startEdit(ent)} disabled={sealed || busy} hitSlop={6}>
                            <Text style={[styles.entAction, sealed && { opacity: 0.4 }]}>改</Text>
                          </Pressable>
                          <Pressable onPress={() => banishEntity(ent)} disabled={sealed || busy} hitSlop={6}>
                            <Text style={[styles.entAction, styles.entActionDanger, sealed && { opacity: 0.4 }]}>删</Text>
                          </Pressable>
                        </View>
                      ) : null}
                    </View>

                    {editing ? (
                      <Animated.View entering={FadeIn.duration(240)} style={styles.editorPanel}>
                        <TextInput
                          value={draftSummary}
                          onChangeText={setDraftSummary}
                          multiline
                          style={styles.editorInput}
                          placeholder="为这条实体补一段摘要…"
                          placeholderTextColor={theme.color.textFaint}
                          autoFocus
                        />
                        <View style={styles.editorActions}>
                          <Pressable onPress={() => setEditingKey(null)} hitSlop={6} disabled={busy}>
                            <Text style={styles.cancelAction}>取消</Text>
                          </Pressable>
                          <Pressable
                            onPress={() => saveSummary(ent)}
                            disabled={busy || !draftSummary.trim()}
                            style={[styles.saveBtn, (busy || !draftSummary.trim()) && { opacity: 0.5 }]}
                          >
                            {busy ? (
                              <ActivityIndicator color={theme.color.bg} size="small" />
                            ) : (
                              <Text style={styles.saveBtnText}>存</Text>
                            )}
                          </Pressable>
                        </View>
                      </Animated.View>
                    ) : ent.summary ? (
                      <Text style={styles.entSummary}>{ent.summary}</Text>
                    ) : (
                      <Text style={styles.entSummaryEmpty}>暂无摘要</Text>
                    )}
                  </Animated.View>
                );
              })
            )}

            {timeline.length > 0 ? (
              <>
                <RuneDivider />
                <Text style={styles.sectionLabel}>规范世界线 · {timeline.length} 节点</Text>
                {timeline.slice(0, 20).map((t, i) => (
                  <Animated.View key={i} entering={FadeIn.delay(i * 18).duration(240)} style={styles.tlRow}>
                    <View style={styles.tlDot} />
                    <View style={{ flex: 1 }}>
                      <Text style={styles.tlLabel} numberOfLines={1}>{t.story_time_label || `节点 ${i + 1}`}</Text>
                      {t.chapter_min != null ? (
                        <Text style={styles.tlMeta}>
                          第 {t.chapter_min}{t.chapter_max != null && t.chapter_max !== t.chapter_min ? `–${t.chapter_max}` : ""} 章
                        </Text>
                      ) : null}
                    </View>
                  </Animated.View>
                ))}
                {timeline.length > 20 ? (
                  <Text style={styles.tlMore}>另有 {timeline.length - 20} 个节点未在此显示。</Text>
                ) : null}
              </>
            ) : null}
          </ScrollView>
        </KeyboardAvoidingView>
      )}
    </GrimoireBackdrop>
  );
}

function Rune({ kind, label }: { kind: "ok" | "warn" | "info"; label: string }) {
  const color =
    kind === "ok" ? palette.jade : kind === "warn" ? theme.color.accent : palette.parchmentDim;
  const bg =
    kind === "ok"
      ? "rgba(111,174,135,0.10)"
      : kind === "warn"
        ? "rgba(232,146,58,0.10)"
        : "rgba(233,220,194,0.06)";
  return (
    <View style={[styles.rune, { borderColor: color + "55", backgroundColor: bg }]}>
      <Text style={[styles.runeLabel, { color }]}>{label}</Text>
    </View>
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
  // wax-seal banner
  seal: {
    flexDirection: "row",
    alignItems: "center",
    gap: theme.space(4),
    padding: theme.space(5),
    borderRadius: theme.radius.lg,
    borderWidth: 2,
    overflow: "hidden",
  },
  sealOpen: { borderColor: theme.color.accentDeep, backgroundColor: "rgba(232,146,58,0.06)" },
  sealOk: { borderColor: palette.jade, backgroundColor: "rgba(111,174,135,0.06)" },
  sealGlyph: {
    fontSize: 36,
    color: theme.color.accent,
    width: 44,
    textAlign: "center",
    textShadowColor: theme.color.accentSoft,
    textShadowRadius: 12,
  },
  sealStatus: {
    fontFamily: theme.font.display,
    fontSize: theme.size.md,
    color: theme.color.accentBright,
    letterSpacing: 0.5,
  },
  sealMeta: {
    fontFamily: theme.font.prose,
    fontSize: theme.size.sm,
    color: theme.color.textDim,
    marginTop: 2,
    lineHeight: 19,
  },
  // runic flag row
  runicRow: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(2), marginTop: theme.space(5) },
  rune: {
    paddingHorizontal: theme.space(3),
    paddingVertical: theme.space(1.5),
    borderRadius: theme.radius.pill,
    borderWidth: 1,
  },
  runeLabel: { fontFamily: theme.font.proseMedium, fontSize: theme.size.sm, letterSpacing: 0.3 },
  // sections
  sectionLabel: {
    fontFamily: theme.font.displaySemi,
    fontSize: theme.size.xs,
    letterSpacing: 3,
    textTransform: "uppercase",
    color: theme.color.accent,
    marginTop: theme.space(3),
    marginBottom: theme.space(3),
  },
  empty: {
    fontFamily: theme.font.proseItalic,
    fontSize: theme.size.base,
    color: theme.color.textFaint,
    lineHeight: 22,
    paddingVertical: theme.space(4),
  },
  // entity card
  entCard: {
    padding: theme.space(4),
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    backgroundColor: theme.color.bgCard,
    marginBottom: theme.space(3),
    gap: theme.space(2),
  },
  entCardEditing: { borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  entHead: { flexDirection: "row", alignItems: "center", gap: theme.space(3) },
  entGlyph: { fontSize: 18, color: theme.color.accent, width: 24, textAlign: "center" },
  entName: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  entMeta: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint, marginTop: 2 },
  entActions: { flexDirection: "row", gap: theme.space(4) },
  entAction: { fontFamily: theme.font.displaySemi, fontSize: theme.size.sm, color: theme.color.accent, letterSpacing: 1 },
  entActionDanger: { color: theme.color.danger },
  entSummary: {
    fontFamily: theme.font.prose,
    fontSize: theme.size.sm,
    color: theme.color.textDim,
    lineHeight: 21,
    paddingTop: theme.space(1),
    borderTopWidth: 1,
    borderTopColor: theme.color.surfaceLine,
  },
  entSummaryEmpty: {
    fontFamily: theme.font.proseItalic,
    fontSize: theme.size.sm,
    color: theme.color.textFaint,
    paddingTop: theme.space(1),
    borderTopWidth: 1,
    borderTopColor: theme.color.surfaceLine,
  },
  // editor panel
  editorPanel: { gap: theme.space(2), marginTop: theme.space(1), paddingTop: theme.space(3), borderTopWidth: 1, borderTopColor: theme.color.accentSoft },
  editorInput: {
    backgroundColor: theme.color.bgInput,
    borderRadius: theme.radius.sm,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    paddingHorizontal: theme.space(3),
    paddingVertical: theme.space(2.5),
    color: theme.color.text,
    fontFamily: theme.font.prose,
    fontSize: theme.size.base,
    lineHeight: 22,
    minHeight: 76,
    textAlignVertical: "top",
  },
  editorActions: { flexDirection: "row", alignItems: "center", justifyContent: "flex-end", gap: theme.space(4) },
  cancelAction: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.textFaint, paddingHorizontal: theme.space(2) },
  saveBtn: {
    paddingHorizontal: theme.space(4),
    paddingVertical: theme.space(2),
    borderRadius: theme.radius.pill,
    backgroundColor: theme.color.accent,
    alignItems: "center",
    minWidth: 48,
  },
  saveBtnText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.sm, color: theme.color.bg, letterSpacing: 1 },
  // timeline coda
  tlRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), paddingVertical: theme.space(2) },
  tlDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: theme.color.accent, opacity: 0.7 },
  tlLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.text },
  tlMeta: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint, marginTop: 1 },
  tlMore: {
    fontFamily: theme.font.proseItalic,
    fontSize: theme.size.sm,
    color: theme.color.textFaint,
    marginTop: theme.space(2),
    textAlign: "center",
  },
});
