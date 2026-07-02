/**
 * Codex Lectern — read the source novel that grounds a script. Two faces: a chapter
 * index (searchable) and the worldbook (setting-bible entries the engine consults). Tap
 * a chapter to fall into a full-bleed reading view set in Spectral, like opening the
 * actual book the world was carved from. A chapter's body lazy-loads on open.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, FlatList, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import Animated, { FadeIn, FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { FamiliarSanctum } from "@/components/FamiliarSanctum";
import { scripts, settings, ChapterSummary, WorldbookEntry } from "@/api";
import { ApiError } from "@/api/http";
import { usePrompt } from "@/components/PromptDialog";
import { theme } from "@/theme/theme";

type Tab = "chapters" | "worldbook" | "canon" | "timeline";

// Type-specific glyphs for canon entities, kept consistent with the verification chamber
// so the same entity reads the same way wherever it surfaces.
const ENTITY_GLYPH: Record<string, string> = {
  character: "❦",
  location: "⌖",
  faction: "✦",
  item: "◈",
  concept: "✺",
  organization: "✦",
  place: "⌖",
};

export default function ScriptReaderScreen() {
  const { id, title } = useLocalSearchParams<{ id: string; title?: string }>();
  const scriptId = Number(id);
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { prompt, promptNode } = usePrompt();

  const [tab, setTab] = useState<Tab>("chapters");
  const [chapters, setChapters] = useState<ChapterSummary[]>([]);
  const [worldbook, setWorldbook] = useState<WorldbookEntry[]>([]);
  const [canon, setCanon] = useState<any[]>([]);
  const [timeline, setTimeline] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");

  // reading view state
  const [reading, setReading] = useState<{ idx: number; title: string } | null>(null);
  const [chapterBody, setChapterBody] = useState("");
  const [chapterLoading, setChapterLoading] = useState(false);
  const [sanctumOpen, setSanctumOpen] = useState(false);
  const [sanctumSeed, setSanctumSeed] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [savingChapter, setSavingChapter] = useState(false);

  const loadChapters = useCallback(async (q?: string) => {
    setLoading(true);
    try {
      const r = await scripts.chapters(scriptId, q);
      setChapters(r?.items ?? r?.chapters ?? []);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) console.warn(e.message);
    } finally {
      setLoading(false);
    }
  }, [scriptId]);

  const loadWorldbook = useCallback(async () => {
    setLoading(true);
    try {
      const r = await scripts.worldbook(scriptId);
      setWorldbook(r?.items ?? r?.entries ?? []);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) console.warn(e.message);
    } finally {
      setLoading(false);
    }
  }, [scriptId]);

  // Canon & timeline: the script's regularized knowledge atoms (entities) and its
  // novelistic spine (chapter-anchored time labels). Both are read-only on mobile —
  // editing routes through the Verification chamber for canon, and through the
  // worldline runtime for timeline.
  const loadCanon = useCallback(async () => {
    setLoading(true);
    try {
      const r = await scripts.canonList(scriptId);
      setCanon(r?.items ?? []);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) console.warn(e.message);
    } finally {
      setLoading(false);
    }
  }, [scriptId]);

  const loadTimeline = useCallback(async () => {
    setLoading(true);
    try {
      const r = await scripts.timeline(scriptId);
      // Timeline endpoint returns either `items` (flat anchor list) or `phases` (grouped).
      // Flatten phases here for a single chronological scroll on mobile.
      const flat: any[] = [];
      if (Array.isArray(r?.items)) flat.push(...r.items);
      else if (Array.isArray(r?.phases)) {
        for (const p of r.phases) {
          const anchors = Array.isArray(p?.anchors) ? p.anchors : [];
          for (const a of anchors) flat.push({ ...a, _phase_label: p.phase_label });
        }
      }
      setTimeline(flat);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) console.warn(e.message);
    } finally {
      setLoading(false);
    }
  }, [scriptId]);

  // Worldbook entry CRUD. Mobile keeps it focused: edit the entry's content, or add/
  // delete an entry. Keyword arrays + advanced fields stay on the desktop editor.
  // Reorder a worldbook entry by one slot. The engine sorts entries by descending
  // priority, so "up the ribbon" means "higher priority" — we swap the two affected
  // entries' priority numbers, then persist both. Optimistic local update first; if
  // either write fails, we reload the truth from the server.
  const reorderWb = useCallback(
    async (index: number, direction: -1 | 1) => {
      const next = index + direction;
      if (next < 0 || next >= worldbook.length) return;
      const a = worldbook[index];
      const b = worldbook[next];
      if (a?.id == null || b?.id == null) return;
      const pa = typeof a.priority === "number" ? a.priority : 50;
      const pb = typeof b.priority === "number" ? b.priority : 50;
      // optimistic: swap the two rows' positions AND their priorities so the
      // server-side sort-by-priority and our local order stay aligned.
      setWorldbook((prev) => {
        const out = prev.slice();
        out[index] = { ...b, priority: pa } as WorldbookEntry;
        out[next] = { ...a, priority: pb } as WorldbookEntry;
        return out;
      });
      // persist (server-side priority swap)
      try {
        await Promise.all([
          scripts.worldbookUpdate(scriptId, Number(a.id), { ...a, priority: pb }),
          scripts.worldbookUpdate(scriptId, Number(b.id), { ...b, priority: pa }),
        ]);
        await loadWorldbook();
      } catch (e) {
        await loadWorldbook();
        Alert.alert("排序失败", e instanceof ApiError ? e.message : "已恢复原顺序。");
      }
    },
    [worldbook, scriptId, loadWorldbook],
  );

  const wbCreate = () =>
    prompt({
      title: "新建世界书条目",
      placeholder: "条目名称（如：龙裔之血）",
      onConfirm: async (name) => {
        try { await scripts.worldbookCreate(scriptId, { name, keys: [name], content: "" }); loadWorldbook(); }
        catch (e) { Alert.alert("创建失败", e instanceof ApiError ? e.message : "请重试"); }
      },
    });

  // AI 复核人名/语义: lets the engine consolidate duplicate NPC cards, lock the protagonist,
  // and discard non-name detritus. Uses the user's currently selected model — if no
  // credentials are configured for it, the backend returns a code:"credentials_required"
  // and we point the player at Settings rather than silently failing.
  const runAudit = useCallback(async () => {
    try {
      const m = await settings.models();
      const api_id = m?.selected?.api_id || "";
      const model = m?.selected?.model_id || m?.selected?.model || "";
      if (!api_id || !model) {
        Alert.alert("尚未选定模型", "请先在「设置 → 模型与密钥」选择一个可用模型，再触发 AI 复核。");
        return;
      }
      Alert.alert(
        "AI 复核人名 / 语义",
        "对本剧本所有 NPC 卡执行一次裁决：合并同人多卡、锁定主角、删除官职 / 地名等误生成。该操作消耗一次模型调用。",
        [
          { text: "取消", style: "cancel" },
          {
            text: "开始复核",
            onPress: async () => {
              try {
                const r = await scripts.auditCards(scriptId, api_id, model);
                if (r?.code === "credentials_required") {
                  Alert.alert("缺少凭据", "所选模型尚未配置 API Key，请到「设置」补齐后重试。");
                  return;
                }
                if (r?.ok === false) {
                  Alert.alert("复核失败", r.error || "请稍后重试。");
                  return;
                }
                const bits: string[] = [];
                if (r?.merged) bits.push(`合并 ${r.merged} 组`);
                if (r?.removed) bits.push(`删除 ${r.removed} 张`);
                if (r?.protagonist) bits.push(`主角 → ${r.protagonist}`);
                Alert.alert("复核已派发", bits.length ? bits.join(" · ") : "复核任务已加入队列，稍后查看卡库即可。");
              } catch (e) {
                Alert.alert("复核失败", e instanceof ApiError ? e.message : "请稍后重试。");
              }
            },
          },
        ],
      );
    } catch (e) {
      Alert.alert("无法读取模型", e instanceof ApiError ? e.message : "请重试。");
    }
  }, [scriptId]);

  const wbEdit = (entry: WorldbookEntry) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    Alert.alert(entry.name || "条目", "选择操作", [
      {
        text: "编辑内容",
        onPress: () =>
          prompt({
            title: "编辑条目内容",
            initialValue: entry.content || "",
            placeholder: "这个设定是什么…",
            onConfirm: async (content) => {
              try { if (entry.id != null) { await scripts.worldbookUpdate(scriptId, Number(entry.id), { ...entry, content }); loadWorldbook(); } }
              catch (e) { Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试"); }
            },
          }),
      },
      {
        text: "删除",
        style: "destructive",
        onPress: () =>
          Alert.alert("删除条目", "确定删除此世界书条目？", [
            { text: "取消", style: "cancel" },
            { text: "删除", style: "destructive", onPress: async () => { try { if (entry.id != null) { await scripts.worldbookDelete(scriptId, Number(entry.id)); loadWorldbook(); } } catch (e) { Alert.alert("删除失败", e instanceof ApiError ? e.message : "请重试"); } } },
          ]),
      },
      { text: "取消", style: "cancel" },
    ]);
  };

  useEffect(() => {
    if (tab === "chapters") loadChapters();
    else if (tab === "worldbook") { if (worldbook.length === 0) loadWorldbook(); }
    else if (tab === "canon") { if (canon.length === 0) loadCanon(); }
    else if (tab === "timeline") { if (timeline.length === 0) loadTimeline(); }
  }, [tab, loadChapters, loadWorldbook, loadCanon, loadTimeline]);

  const openChapter = async (ch: ChapterSummary) => {
    setReading({ idx: ch.chapter_index, title: ch.title || `第 ${ch.chapter_index + 1} 章` });
    setChapterBody("");
    setEditing(false);
    setChapterLoading(true);
    try {
      const r = await scripts.chapterDetail(scriptId, ch.chapter_index);
      setChapterBody(r?.chapter?.content || "（本章无正文）");
    } catch (e) {
      setChapterBody(e instanceof ApiError ? `加载失败：${e.message}` : "加载失败");
    } finally {
      setChapterLoading(false);
    }
  };

  const saveChapter = async () => {
    if (!reading) return;
    setSavingChapter(true);
    try {
      await scripts.updateChapter(scriptId, reading.idx, { content: chapterBody });
      setEditing(false);
      // After persisting the chapter, offer the scribe a chance to summon the familiar
      // for setting-sync. If the chapter introduced or shifted a character / rule /
      // anchor, the familiar can read the new prose and update canon, worldbook, and
      // timeline accordingly. The body we just saved is staged so the sanctum opens
      // with a pre-cast invocation.
      const snippet = chapterBody.slice(0, 1400);
      const idx = reading.idx;
      Alert.alert(
        "已铭刻",
        "本章正文已更新。是否召唤司笔灵，让它读过这段新文字并同步设定？",
        [
          { text: "暂不", style: "cancel" },
          {
            text: "召唤司笔灵",
            onPress: () => {
              setSanctumSeed(
                `我刚刚修订了第 ${idx + 1} 章的正文。请阅读以下内容，识别其中新增或改动的人物 / 设定 / 时间线节点，并调用对应工具同步到知识库。\n\n---\n${snippet}\n---`,
              );
              setSanctumOpen(true);
            },
          },
        ],
      );
    } catch (e) {
      Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setSavingChapter(false);
    }
  };

  // ---- reading view ----
  if (reading) {
    return (
      <GrimoireBackdrop>
        <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
          <Pressable onPress={() => setReading(null)} hitSlop={12} style={styles.headBtn}>
            <Text style={styles.headGlyph}>‹</Text>
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={styles.kicker}>{editing ? "Editing" : "Reading"}</Text>
            <Text style={styles.h1} numberOfLines={1}>{reading.title}</Text>
          </View>
          {!chapterLoading ? (
            editing ? (
              <Pressable onPress={saveChapter} hitSlop={12} style={styles.headBtn} disabled={savingChapter}>
                {savingChapter ? <ActivityIndicator color={theme.color.accent} size="small" /> : <Text style={styles.headActionGlyph}>✓</Text>}
              </Pressable>
            ) : (
              <Pressable onPress={() => setEditing(true)} hitSlop={12} style={styles.headBtn}>
                <Text style={styles.headActionGlyph}>✎</Text>
              </Pressable>
            )
          ) : null}
        </View>
        {chapterLoading ? (
          <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
        ) : editing ? (
          <Animated.View entering={FadeIn.duration(300)} style={{ flex: 1, paddingHorizontal: theme.space(7), paddingTop: theme.space(4) }}>
            <TextInput
              value={chapterBody}
              onChangeText={setChapterBody}
              multiline
              style={styles.editor}
              textAlignVertical="top"
              placeholder="书写本章正文…"
              placeholderTextColor={theme.color.textFaint}
            />
          </Animated.View>
        ) : (
          <Animated.ScrollView
            entering={FadeIn.duration(400)}
            contentContainerStyle={{ paddingHorizontal: theme.space(7), paddingBottom: insets.bottom + 60, paddingTop: theme.space(4) }}
          >
            <Text style={styles.chapterTitle}>{reading.title}</Text>
            <View style={styles.titleRule} />
            <Text style={styles.prose}>{chapterBody}</Text>
          </Animated.ScrollView>
        )}
      </GrimoireBackdrop>
    );
  }

  // ---- index view ----
  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Codex</Text>
          <Text style={styles.h1} numberOfLines={1}>{title || "典籍"}</Text>
        </View>
        <Pressable
          onPress={() => router.push({ pathname: "/(app)/script-audit/[id]", params: { id: String(scriptId), title: title || "设定核对" } })}
          hitSlop={12}
          style={styles.headBtn}
        >
          <Text style={styles.headActionGlyph}>⚖</Text>
        </Pressable>
        <Pressable onPress={runAudit} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headActionGlyph}>⚘</Text>
        </Pressable>
        <Pressable onPress={() => setSanctumOpen(true)} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headActionGlyph}>✶</Text>
        </Pressable>
      </View>

      <View style={styles.tabs}>
        {(["chapters", "worldbook", "canon", "timeline"] as Tab[]).map((t) => {
          const label = t === "chapters" ? "章节" : t === "worldbook" ? "世界书" : t === "canon" ? "实体" : "纪年";
          return (
            <Pressable key={t} onPress={() => setTab(t)} style={[styles.tab, tab === t && styles.tabActive]}>
              <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>{label}</Text>
            </Pressable>
          );
        })}
      </View>

      {tab === "chapters" ? (
        <>
          <TextInput
            value={query}
            onChangeText={setQuery}
            placeholder="搜索章节…"
            placeholderTextColor={theme.color.textFaint}
            style={styles.search}
            autoCapitalize="none"
            returnKeyType="search"
            onSubmitEditing={() => loadChapters(query)}
          />
          <FlatList
            data={chapters}
            keyExtractor={(c) => String(c.id ?? c.chapter_index)}
            renderItem={({ item, index }) => (
              <Animated.View entering={FadeInDown.delay(Math.min(index, 10) * 35).duration(340)}>
                <Pressable onPress={() => openChapter(item)} style={({ pressed }) => [styles.chapterRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
                  <Text style={styles.chapterNum}>{String((item.chapter_index ?? index) + 1).padStart(2, "0")}</Text>
                  <View style={{ flex: 1, gap: 2 }}>
                    {item.volume_title ? <Text style={styles.volume}>{item.volume_title}</Text> : null}
                    <Text style={styles.chapterRowTitle} numberOfLines={1}>{item.title || `第 ${(item.chapter_index ?? index) + 1} 章`}</Text>
                    {item.preview ? <Text style={styles.chapterPreview} numberOfLines={2}>{item.preview}</Text> : null}
                  </View>
                  {item.word_count ? <Text style={styles.wc}>{item.word_count}</Text> : null}
                </Pressable>
              </Animated.View>
            )}
            contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 40, gap: theme.space(1) }}
            ListEmptyComponent={loading ? <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(16) }} /> : <Text style={styles.empty}>没有章节。</Text>}
          />
        </>
      ) : (
        <FlatList
          data={worldbook}
          keyExtractor={(w, i) => String(w.id ?? i)}
          renderItem={({ item, index }) => {
            const pri = typeof item.priority === "number" ? item.priority : 50;
            const isTop = index === 0;
            const isBottom = index === worldbook.length - 1;
            return (
              <Animated.View entering={FadeInDown.delay(Math.min(index, 10) * 35).duration(340)}>
                <View style={styles.wbCardWrap}>
                  {/* Gilt ribbon spine: priority numeral + reorder sigils */}
                  <View style={styles.wbRibbon}>
                    <Pressable
                      onPress={() => reorderWb(index, -1)}
                      disabled={isTop}
                      hitSlop={8}
                      style={({ pressed }) => [styles.wbRibbonArrow, isTop && { opacity: 0.25 }, pressed && { opacity: 0.6 }]}
                    >
                      <Text style={styles.wbRibbonGlyph}>▲</Text>
                    </Pressable>
                    <Text style={styles.wbRibbonNum}>{pri}</Text>
                    <Pressable
                      onPress={() => reorderWb(index, 1)}
                      disabled={isBottom}
                      hitSlop={8}
                      style={({ pressed }) => [styles.wbRibbonArrow, isBottom && { opacity: 0.25 }, pressed && { opacity: 0.6 }]}
                    >
                      <Text style={styles.wbRibbonGlyph}>▼</Text>
                    </Pressable>
                  </View>
                  <Pressable onLongPress={() => wbEdit(item)} style={styles.wbCard}>
                    <Text style={styles.wbName}>{item.name || (item.keys && item.keys[0]) || "条目"}</Text>
                    {item.keys && item.keys.length > 0 ? (
                      <View style={styles.keyRow}>
                        {item.keys.slice(0, 6).map((k, i) => (
                          <View key={i} style={styles.keyChip}><Text style={styles.keyText}>{k}</Text></View>
                        ))}
                      </View>
                    ) : null}
                    {item.content ? <Text style={styles.wbContent} numberOfLines={6}>{item.content}</Text> : null}
                  </Pressable>
                </View>
              </Animated.View>
            );
          }}
          contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 40, gap: theme.space(3), paddingTop: theme.space(2) }}
          ListHeaderComponent={
            <Pressable onPress={wbCreate} style={styles.wbAddRow}>
              <Text style={styles.wbAddGlyph}>＋</Text>
              <Text style={styles.wbAddText}>新建世界书条目</Text>
            </Pressable>
          }
          ListEmptyComponent={loading ? <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(16) }} /> : <Text style={styles.empty}>这部作品没有世界书条目。长按条目可编辑。</Text>}
        />
      )}

      {tab === "canon" ? (
        <FlatList
          data={canon}
          keyExtractor={(e, i) => String(e.logical_key ?? e.id ?? i)}
          renderItem={({ item, index }) => {
            const glyph = ENTITY_GLYPH[(item.type || "").toLowerCase()] || "◇";
            return (
              <Animated.View entering={FadeInDown.delay(Math.min(index, 14) * 30).duration(300)} style={styles.canonRow}>
                <Text style={styles.canonGlyph}>{glyph}</Text>
                <View style={{ flex: 1 }}>
                  <View style={styles.canonHead}>
                    <Text style={styles.canonName} numberOfLines={1}>{item.name || item.logical_key}</Text>
                    {item.importance != null ? <Text style={styles.canonImp}>重要度 {item.importance}</Text> : null}
                  </View>
                  <Text style={styles.canonMeta}>
                    {item.type || "—"}
                    {item.first_revealed_chapter ? ` · 首现 第 ${item.first_revealed_chapter} 章` : ""}
                  </Text>
                  {item.summary ? <Text style={styles.canonSummary} numberOfLines={3}>{item.summary}</Text> : null}
                </View>
              </Animated.View>
            );
          }}
          contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 40, gap: theme.space(3), paddingTop: theme.space(2) }}
          ListEmptyComponent={
            loading ? (
              <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(16) }} />
            ) : (
              <Text style={styles.empty}>
                这本剧本尚未提取规范实体。{"\n"}在「设定核对」（⚖）里查看提取状态。
              </Text>
            )
          }
        />
      ) : null}

      {tab === "timeline" ? (
        <FlatList
          data={timeline}
          keyExtractor={(a, i) => String(a.id ?? a.anchor_key ?? `${a.chapter_min}-${i}`)}
          renderItem={({ item, index }) => (
            <Animated.View entering={FadeInDown.delay(Math.min(index, 12) * 30).duration(300)} style={styles.tlBead}>
              <View style={styles.tlBeadDot} />
              <View style={{ flex: 1 }}>
                <Text style={styles.tlBeadLabel} numberOfLines={1}>
                  {item.story_time_label || item.label || `节点 ${index + 1}`}
                </Text>
                <Text style={styles.tlBeadMeta}>
                  {item.chapter_min != null
                    ? `第 ${item.chapter_min}${item.chapter_max != null && item.chapter_max !== item.chapter_min ? `–${item.chapter_max}` : ""} 章`
                    : ""}
                  {item._phase_label ? ` · ${item._phase_label}` : ""}
                </Text>
                {item.summary ? <Text style={styles.tlBeadSummary} numberOfLines={2}>{item.summary}</Text> : null}
              </View>
            </Animated.View>
          )}
          contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 40, gap: theme.space(2), paddingTop: theme.space(2) }}
          ListEmptyComponent={
            loading ? (
              <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(16) }} />
            ) : (
              <Text style={styles.empty}>
                这本剧本尚未生成时间线锚点。{"\n"}在剧本管理页触发重建即可。
              </Text>
            )
          }
        />
      ) : null}

      {promptNode}
      <FamiliarSanctum
        visible={sanctumOpen}
        scriptId={scriptId}
        seed={sanctumSeed}
        onClose={() => {
          setSanctumOpen(false);
          setSanctumSeed(null);
        }}
      />
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  headActionGlyph: { fontSize: 20, color: theme.color.accent },
  editor: { flex: 1, color: theme.color.text, fontFamily: theme.font.prose, fontSize: theme.size.md, lineHeight: 26, backgroundColor: theme.color.bgInput, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, padding: theme.space(4), marginBottom: theme.space(4) },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  tabs: { flexDirection: "row", gap: theme.space(2), marginHorizontal: theme.space(6), marginBottom: theme.space(3) },
  tab: { flex: 1, paddingVertical: theme.space(2.5), alignItems: "center", borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine },
  tabActive: { backgroundColor: theme.color.accentGhost, borderColor: theme.color.accentSoft },
  tabText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.sm, letterSpacing: 1.5, color: theme.color.textFaint },
  tabTextActive: { color: theme.color.accentBright },
  search: { marginHorizontal: theme.space(6), marginBottom: theme.space(3), backgroundColor: theme.color.bgInput, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, paddingHorizontal: theme.space(4), paddingVertical: theme.space(3), color: theme.color.text, fontFamily: theme.font.prose, fontSize: theme.size.base },
  chapterRow: { flexDirection: "row", alignItems: "center", gap: theme.space(4), paddingVertical: theme.space(3), paddingHorizontal: theme.space(2), borderRadius: theme.radius.md },
  chapterNum: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.accent, width: 28 },
  volume: { fontFamily: theme.font.displaySemi, fontSize: 10, letterSpacing: 1.5, textTransform: "uppercase", color: theme.color.textFaint },
  chapterRowTitle: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  chapterPreview: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 19 },
  wc: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.surfaceLineStrong },
  empty: { fontFamily: theme.font.proseItalic, fontSize: theme.size.md, color: theme.color.textFaint, textAlign: "center", marginTop: theme.space(16) },
  // reading view
  chapterTitle: { fontFamily: theme.font.display, fontSize: theme.size.xxl, color: theme.color.text, letterSpacing: 0.5, lineHeight: 42 },
  titleRule: { height: 1, backgroundColor: theme.color.surfaceLineStrong, marginVertical: theme.space(5), width: 64 },
  prose: { fontFamily: theme.font.prose, fontSize: theme.size.lg, color: theme.color.text, lineHeight: 32, letterSpacing: 0.2 },
  // worldbook
  wbCard: { padding: theme.space(4), borderRadius: theme.radius.lg, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard, gap: theme.space(2) },
  wbAddRow: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: theme.space(2), paddingVertical: theme.space(3), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost, marginBottom: theme.space(1) },
  wbAddGlyph: { fontSize: 18, color: theme.color.accent },
  wbAddText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.accentBright },
  wbName: { fontFamily: theme.font.displaySemi, fontSize: theme.size.md, color: theme.color.accentBright, letterSpacing: 0.5 },
  keyRow: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(2) },
  keyChip: { backgroundColor: theme.color.magicSoft, paddingHorizontal: theme.space(2), paddingVertical: 2, borderRadius: theme.radius.sm },
  keyText: { fontFamily: theme.font.mono, fontSize: 10, color: theme.color.magic },
  wbContent: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim, lineHeight: 21 },
  // Worldbook entry = gilt ribbon spine + scribed card. The ribbon carries the priority
  // numeral between two reorder arrows; tapping ▲/▼ swaps with the neighbor (priority
  // doubles as DB ordering, so the swap persists with two PUTs).
  wbCardWrap: { flexDirection: "row", gap: theme.space(2), alignItems: "stretch" },
  wbRibbon: {
    width: 36,
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: theme.space(3),
    borderRadius: theme.radius.sm,
    borderWidth: 1,
    borderColor: theme.color.accentSoft,
    backgroundColor: theme.color.accentGhost,
  },
  wbRibbonArrow: { padding: theme.space(1) },
  wbRibbonGlyph: { fontSize: 12, color: theme.color.accent },
  wbRibbonNum: {
    fontFamily: theme.font.mono,
    fontSize: theme.size.xs,
    color: theme.color.accentBright,
    letterSpacing: 0.5,
  },
  // Canon ledger — each entity reads as a heraldic shield-line: type-glyph, scribed name,
  // and (where given) a one-paragraph summary. Importance sits as a quiet brass numeral.
  canonRow: {
    flexDirection: "row",
    gap: theme.space(3),
    padding: theme.space(4),
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    backgroundColor: theme.color.bgCard,
  },
  canonGlyph: {
    fontSize: 20,
    color: theme.color.accent,
    width: 24,
    textAlign: "center",
    marginTop: 2,
    textShadowColor: theme.color.accentSoft,
    textShadowRadius: 8,
  },
  canonHead: { flexDirection: "row", alignItems: "baseline", gap: theme.space(3) },
  canonName: { flex: 1, fontFamily: theme.font.display, fontSize: theme.size.md, color: theme.color.text, letterSpacing: 0.4 },
  canonImp: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint },
  canonMeta: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint, marginTop: 2 },
  canonSummary: {
    fontFamily: theme.font.prose,
    fontSize: theme.size.sm,
    color: theme.color.textDim,
    lineHeight: 21,
    marginTop: theme.space(2),
    paddingTop: theme.space(2),
    borderTopWidth: 1,
    borderTopColor: theme.color.surfaceLine,
  },
  // Timeline — beads on an unseen thread. The dot is the bead; the column to its right
  // is the bead's inscription. No backing card — chronology should read as a procession.
  tlBead: {
    flexDirection: "row",
    gap: theme.space(3),
    paddingVertical: theme.space(3),
    borderBottomWidth: 1,
    borderBottomColor: theme.color.surfaceLine,
  },
  tlBeadDot: {
    width: 9,
    height: 9,
    borderRadius: 5,
    backgroundColor: theme.color.accent,
    marginTop: 8,
    marginLeft: theme.space(1),
    shadowColor: theme.color.accent,
    shadowOpacity: 0.7,
    shadowRadius: 4,
    elevation: 3,
  },
  tlBeadLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text, letterSpacing: 0.2 },
  tlBeadMeta: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint, marginTop: 2 },
  tlBeadSummary: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim, lineHeight: 20, marginTop: theme.space(1) },
});
