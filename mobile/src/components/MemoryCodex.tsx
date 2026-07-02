/**
 * Memory Codex — the player's persistent recall, surfaced as a slide-up panel.
 * Five buckets the engine reads every turn: pinned (always-on), notes, facts,
 * resources, abilities. Player can add/edit/remove any entry; the backend returns
 * refreshed state so we re-read the buckets after each mutation. Pinned entries get
 * an ember sigil; the rest sit as a quiet ledger.
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
import { game, memory, MemoryBucket } from "@/api";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

const BUCKETS: { key: MemoryBucket; label: string; glyph: string }[] = [
  { key: "pinned", label: "固定", glyph: "✦" },
  { key: "notes", label: "手记", glyph: "✎" },
  { key: "facts", label: "事实", glyph: "❖" },
  { key: "resources", label: "资源", glyph: "◈" },
  { key: "abilities", label: "能力", glyph: "⚝" },
];

type Entries = Record<string, string[]>;

function extractBuckets(state: any): Entries {
  const mem = state?.state?.memory || state?.memory || {};
  const out: Entries = {};
  for (const b of BUCKETS) {
    const raw = mem[b.key];
    out[b.key] = Array.isArray(raw)
      ? raw.map((x: any) => (typeof x === "string" ? x : x?.text || x?.content || JSON.stringify(x)))
      : [];
  }
  return out;
}

type Header = { quest: string; objective: string; retrieval: string[] };

// Main quest + current objective + last-turn retrieval — read-only orientation strip.
function extractHeader(state: any): Header {
  const s = state?.state ?? state ?? {};
  const mem = s.memory || {};
  const quest = String(s.quest?.main || s.main_quest || s.quest || "");
  const objective = String(s.objective || s.current_objective || s.quest?.current || "");
  const lr = mem.last_retrieval || mem.last_context || [];
  const retrieval: string[] = Array.isArray(lr)
    ? lr.map((x: any) => (typeof x === "string" ? x : x?.text || x?.content || x?.snippet || "")).filter(Boolean)
    : typeof lr === "string"
      ? [lr]
      : [];
  return { quest, objective, retrieval };
}

export function MemoryCodex({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const insets = useSafeAreaInsets();
  const [bucket, setBucket] = useState<MemoryBucket>("pinned");
  const [entries, setEntries] = useState<Entries>({});
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [editIndex, setEditIndex] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [header, setHeader] = useState<Header>({ quest: "", objective: "", retrieval: [] });

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const state = await game.state();
      setEntries(extractBuckets(state));
      setHeader(extractHeader(state));
    } catch {
      /* leave as-is */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (visible) {
      setBucket("pinned");
      setDraft("");
      setEditIndex(null);
      refresh();
    }
  }, [visible, refresh]);

  const applyState = (res: any) => {
    if (res?.state) {
      setEntries(extractBuckets(res));
      setHeader(extractHeader(res));
    } else refresh();
  };

  const submit = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    setBusy(true);
    try {
      const res =
        editIndex != null
          ? await memory.update(bucket, editIndex, text)
          : await memory.add(bucket, text);
      applyState(res);
      setDraft("");
      setEditIndex(null);
    } catch (e) {
      Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setBusy(false);
    }
  };

  const remove = (index: number) => {
    Alert.alert("删除条目", "确定移除这条记忆？", [
      { text: "取消", style: "cancel" },
      {
        text: "删除",
        style: "destructive",
        onPress: async () => {
          try {
            applyState(await memory.remove(bucket, index));
          } catch (e) {
            Alert.alert("删除失败", e instanceof ApiError ? e.message : "请重试");
          }
        },
      },
    ]);
  };

  const list = entries[bucket] || [];

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
        <BlurView intensity={30} tint="dark" style={styles.fill} />
        <View style={styles.grabber} />
        <Text style={styles.title}>记忆典籍</Text>

        {header.quest || header.objective ? (
          <View style={styles.questBox}>
            {header.quest ? (
              <View style={styles.questLine}>
                <Text style={styles.questTag}>主线</Text>
                <Text style={styles.questText} numberOfLines={2}>{header.quest}</Text>
              </View>
            ) : null}
            {header.objective ? (
              <View style={styles.questLine}>
                <Text style={[styles.questTag, styles.objTag]}>当前</Text>
                <Text style={styles.questText} numberOfLines={2}>{header.objective}</Text>
              </View>
            ) : null}
          </View>
        ) : null}

        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tabs}>
          {BUCKETS.map((b) => {
            const active = bucket === b.key;
            const count = (entries[b.key] || []).length;
            return (
              <Pressable key={b.key} onPress={() => { setBucket(b.key); setEditIndex(null); setDraft(""); }} style={[styles.tab, active && styles.tabActive]}>
                <Text style={[styles.tabGlyph, active && { color: theme.color.accentBright }]}>{b.glyph}</Text>
                <Text style={[styles.tabText, active && styles.tabTextActive]}>{b.label}</Text>
                {count > 0 ? <Text style={[styles.tabCount, active && { color: theme.color.accent }]}>{count}</Text> : null}
              </Pressable>
            );
          })}
        </ScrollView>

        {loading ? (
          <ActivityIndicator color={theme.color.accent} style={{ marginVertical: theme.space(8) }} />
        ) : (
          <ScrollView style={styles.body} keyboardShouldPersistTaps="handled">
            {list.length === 0 ? (
              <Text style={styles.empty}>此卷尚空。写下你想让故事记住的事。</Text>
            ) : (
              list.map((entry, i) => (
                <Animated.View key={i} entering={FadeIn.delay(i * 30).duration(280)} style={styles.entry}>
                  <Text style={styles.entryText}>{entry}</Text>
                  <View style={styles.entryActions}>
                    <Pressable onPress={() => { setEditIndex(i); setDraft(entry); }} hitSlop={8}>
                      <Text style={styles.entryAction}>编辑</Text>
                    </Pressable>
                    <Pressable onPress={() => remove(i)} hitSlop={8}>
                      <Text style={[styles.entryAction, { color: theme.color.danger }]}>删除</Text>
                    </Pressable>
                  </View>
                </Animated.View>
              ))
            )}
          </ScrollView>
        )}

        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <View style={styles.composer}>
            <TextInput
              value={draft}
              onChangeText={setDraft}
              placeholder={editIndex != null ? "修改此条…" : `添加到「${BUCKETS.find((b) => b.key === bucket)?.label}」…`}
              placeholderTextColor={theme.color.textFaint}
              style={styles.input}
              multiline
            />
            <Pressable onPress={submit} disabled={!draft.trim() || busy} style={[styles.addBtn, (!draft.trim() || busy) && { opacity: 0.4 }]}>
              {busy ? <ActivityIndicator color={theme.color.bg} size="small" /> : <Text style={styles.addGlyph}>{editIndex != null ? "✓" : "＋"}</Text>}
            </Pressable>
          </View>
        </KeyboardAvoidingView>
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
    backgroundColor: "rgba(20,16,12,0.88)",
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
  questBox: { gap: theme.space(2), padding: theme.space(3), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard, marginBottom: theme.space(3) },
  questLine: { flexDirection: "row", alignItems: "flex-start", gap: theme.space(2) },
  questTag: { fontFamily: theme.font.displaySemi, fontSize: 10, letterSpacing: 1, color: theme.color.bg, backgroundColor: theme.color.accent, paddingHorizontal: theme.space(2), paddingVertical: 2, borderRadius: theme.radius.sm, overflow: "hidden", marginTop: 1 },
  objTag: { backgroundColor: theme.color.magic },
  questText: { flex: 1, fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim, lineHeight: 20 },
  tabs: { gap: theme.space(2), paddingBottom: theme.space(3) },
  tab: { flexDirection: "row", alignItems: "center", gap: theme.space(2), paddingHorizontal: theme.space(3), paddingVertical: theme.space(2), borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.surfaceLine },
  tabActive: { backgroundColor: theme.color.accentGhost, borderColor: theme.color.accentSoft },
  tabGlyph: { fontSize: 13, color: theme.color.textFaint },
  tabText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.textFaint },
  tabTextActive: { color: theme.color.accentBright },
  tabCount: { fontFamily: theme.font.mono, fontSize: 10, color: theme.color.textFaint },
  body: { maxHeight: 360 },
  empty: { fontFamily: theme.font.proseItalic, fontSize: theme.size.md, color: theme.color.textFaint, textAlign: "center", paddingVertical: theme.space(10) },
  entry: { paddingVertical: theme.space(3), borderBottomWidth: 1, borderBottomColor: theme.color.surfaceLine, gap: theme.space(2) },
  entryText: { fontFamily: theme.font.prose, fontSize: theme.size.md, color: theme.color.text, lineHeight: 23 },
  entryActions: { flexDirection: "row", gap: theme.space(5) },
  entryAction: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.accent },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: theme.space(3), paddingTop: theme.space(3), borderTopWidth: 1, borderTopColor: theme.color.surfaceLine },
  input: { flex: 1, maxHeight: 100, minHeight: 46, backgroundColor: theme.color.bgInput, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, paddingHorizontal: theme.space(4), paddingTop: theme.space(3), paddingBottom: theme.space(3), color: theme.color.text, fontFamily: theme.font.prose, fontSize: theme.size.md, lineHeight: 21 },
  addBtn: { width: 46, height: 46, borderRadius: theme.radius.pill, backgroundColor: theme.color.accent, alignItems: "center", justifyContent: "center", marginBottom: 1 },
  addGlyph: { fontSize: 22, color: theme.color.bg, marginTop: -1 },
});
