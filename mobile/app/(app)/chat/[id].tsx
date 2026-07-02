/**
 * Tavern chat screen — the immersive 1:1 roleplay surface.
 * History comes from GET /api/v1/state (tavern.history); turns stream from
 * POST /api/v1/chat as SSE (token / tool_call / tool_result / usage / done).
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
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
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import * as ImagePicker from "expo-image-picker";
import * as Clipboard from "expo-clipboard";
import { Image } from "expo-image";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { IconLabelButton } from "@/components/ui";
import { MessageBubble } from "@/components/MessageBubble";
import { UsageRing } from "@/components/UsageRing";
import { CharacterSheet } from "@/components/CharacterSheet";
import { MemoryCodex } from "@/components/MemoryCodex";
import { ContextLedger } from "@/components/ContextLedger";
import { ScryingGlass } from "@/components/ScryingGlass";
import { WorldCodex } from "@/components/WorldCodex";
import { CodexOfTrials } from "@/components/CodexOfTrials";
import { OracleFork, PendingQuestion } from "@/components/OracleFork";
import { ModelEffortPicker } from "@/components/ModelEffortPicker";
import { game, tavern, branches, permissions } from "@/api";
import { SseController } from "@/api/sse";
import { theme } from "@/theme/theme";
import { ChatMessage, ToolEvent, normalizeHistory } from "@/state/chatModel";

/** Pull the first unanswered choice question out of state.permissions.pending_questions. */
function extractPendingQuestion(state: any): PendingQuestion | null {
  const pqs = state?.permissions?.pending_questions;
  if (!Array.isArray(pqs)) return null;
  const q = pqs.find((p: any) => (p?.options || p?.choices)?.length);
  return q ?? null;
}

// GM state-write permission modes (backend tool names) with their display labels + cycle.
const PERM_LABEL: Record<string, string> = {
  readonly: "只读叙事",
  default: "默认权限",
  auto: "自动审查",
  full_access: "完全访问",
};
const PERM_CYCLE = ["readonly", "default", "auto", "full_access"];

// Slash commands: inserted into the composer (some need an argument), then sent. The
// backend parses them and streams a system_receipt the SSE layer already handles.
const SLASH_COMMANDS: { cmd: string; label: string; arg: boolean }[] = [
  { cmd: "/continue", label: "继续推进剧情", arg: false },
  { cmd: "/loc ", label: "更新所在地点", arg: true },
  { cmd: "/time ", label: "更新时间", arg: true },
  { cmd: "/pin ", label: "固定一条记忆", arg: true },
  { cmd: "/set ", label: "自然语言写入状态", arg: true },
  { cmd: "/save", label: "手动存档", arg: false },
  { cmd: "/retry", label: "重试上一轮", arg: false },
];

export default function ChatScreen() {
  const { id, title } = useLocalSearchParams<{ id: string; title?: string }>();
  const saveId = Number(id);
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [bootLoading, setBootLoading] = useState(true);
  const [usage, setUsage] = useState<{ pct: number; cost: number } | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [codexOpen, setCodexOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<{ index: number; id: string } | null>(null);
  const [ledgerOpen, setLedgerOpen] = useState(false);
  const [scryOpen, setScryOpen] = useState(false);
  const [worldOpen, setWorldOpen] = useState(false);
  const [trialsOpen, setTrialsOpen] = useState(false);
  const [pendingQ, setPendingQ] = useState<PendingQuestion | null>(null);
  const [answeringQ, setAnsweringQ] = useState(false);
  const [slashOpen, setSlashOpen] = useState(false);
  const [permMode, setPermMode] = useState<string>("default");
  const [pendingAttachments, setPendingAttachments] = useState<{ name: string; type: string; data_url: string }[]>([]);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [relationNames, setRelationNames] = useState<string[]>([]);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [stageLabel, setStageLabel] = useState<string | null>(null);
  const [museLoading, setMuseLoading] = useState(false);
  const [overflowOpen, setOverflowOpen] = useState(false);

  const listRef = useRef<FlatList<ChatMessage>>(null);
  const streamRef = useRef<SseController | null>(null);
  const draftIdRef = useRef<string | null>(null);
  const lastSentRef = useRef<{ text: string; atts: { name: string; type: string; data_url: string }[] } | null>(null);

  const isNearBottomRef = useRef(true);

  const scrollToEnd = useCallback(() => {
    requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));
  }, []);

  const handleScroll = useCallback((event: any) => {
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    const dist = contentSize.height - contentOffset.y - layoutMeasurement.height;
    isNearBottomRef.current = dist < 80;
  }, []);

  const autoScrollIfNearBottom = useCallback(() => {
    if (isNearBottomRef.current) scrollToEnd();
  }, [scrollToEnd]);

  const boot = useCallback(async () => {
    setBootLoading(true);
    try {
      const res = await game.state();
      // GET /api/state returns the payload directly; some other endpoints wrap in {state}.
      const state = res?.state ?? res;
      const hist = normalizeHistory(state);
      setMessages(hist);
      const ctxPct = state?.tavern?.context_pct ?? state?.context_pct;
      if (typeof ctxPct === "number") setUsage({ pct: ctxPct, cost: 0 });
      setPendingQ(extractPendingQuestion(state));
      const pm = state?.permissions?.mode;
      if (typeof pm === "string" && PERM_LABEL[pm]) setPermMode(pm);
      // relationship names power the @mention list
      const rels = state?.relationships;
      const names = Array.isArray(rels)
        ? rels.map((r: any) => r?.character || r?.name).filter(Boolean)
        : rels && typeof rels === "object"
          ? Object.keys(rels)
          : [];
      setRelationNames(names);
    } catch {
      /* leave empty; user can still send */
    } finally {
      setBootLoading(false);
      scrollToEnd();
    }
  }, [scrollToEnd]);

  useEffect(() => {
    boot();
    return () => streamRef.current?.close();
  }, [boot]);

  const updateDraft = useCallback((mutate: (m: ChatMessage) => ChatMessage) => {
    const did = draftIdRef.current;
    if (!did) return;
    setMessages((prev) => prev.map((m) => (m.id === did ? mutate(m) : m)));
  }, []);

  const send = useCallback(async () => {
    const text = input.trim();
    if ((!text && pendingAttachments.length === 0) || streaming) return;
    setInput("");
    const atts = pendingAttachments;
    setPendingAttachments([]);
    setStreaming(true);
    lastSentRef.current = { text, atts };

    const userText = text || (atts.length ? `[附图 ${atts.length} 张]` : "");
    const userMsg: ChatMessage = { id: `u-${Date.now()}`, role: "user", text: userText };
    const draftId = `a-${Date.now()}`;
    draftIdRef.current = draftId;
    const draft: ChatMessage = { id: draftId, role: "assistant", text: "", tools: [], pending: true };
    setMessages((prev) => [...prev, userMsg, draft]);
    scrollToEnd();

    streamRef.current = await game.chat(
      { message: text, save_id: saveId, ...(atts.length ? { attachments: atts } : {}) },
      {
        token: (d) => {
          const chunk = typeof d === "string" ? d : d?.text || "";
          if (chunk) {
            setStageLabel(null);
            updateDraft((m) => ({ ...m, text: m.text + chunk }));
            scrollToEnd();
          }
        },
        stage: (d) => {
          const label = typeof d === "string" ? d : d?.label || "";
          const phase = d?.phase;
          if (phase === "done") setStageLabel(null);
          else if (label) setStageLabel(label);
        },
        tool_call: (d) =>
          updateDraft((m) => ({
            ...m,
            tools: [...(m.tools || []), { kind: "call", name: d?.name || "tool", payload: d?.args }],
          })),
        tool_result: (d) =>
          updateDraft((m) => ({
            ...m,
            tools: [...(m.tools || []), { kind: "result", name: d?.name || "tool", payload: d?.result }],
          })),
        usage: (d) => {
          const pct = d?.context_pct ?? d?.context_used / (d?.context_max || 1);
          setUsage({ pct: typeof pct === "number" ? (pct > 1 ? pct / 100 : pct) : 0, cost: d?.cost_usd || 0 });
        },
        error: (d) =>
          updateDraft((m) => ({ ...m, error: d?.message || "生成出错", pending: false })),
        done: () => {
          updateDraft((m) => ({ ...m, pending: false }));
          setStreaming(false);
          streamRef.current = null;
          scrollToEnd();
          // A turn may have produced a new ask_player_choice — re-read state to surface it.
          game.state().then((res) => setPendingQ(extractPendingQuestion(res?.state ?? res))).catch(() => {});
        },
        onError: (e) => {
          updateDraft((m) => ({ ...m, error: e.message, pending: false }));
          setStreaming(false);
        },
      },
    );
  }, [input, streaming, saveId, updateDraft, scrollToEnd]);

  const stop = useCallback(async () => {
    streamRef.current?.close();
    streamRef.current = null;
    try {
      await game.stop();
    } catch {
      /* noop */
    }
    updateDraft((m) => ({ ...m, pending: false }));
    setStreaming(false);
  }, [updateDraft]);

  // Retry the last failed turn: restore its text + attachments into the composer and re-send.
  const retryLast = useCallback(() => {
    if (streaming) return;
    const last = lastSentRef.current;
    if (!last) return;
    setInput(last.text);
    setPendingAttachments(last.atts);
    setTimeout(() => send(), 0);
  }, [streaming, send]);

  // The Muse: ask the backend to draft a reply *in the player's voice*, then drop it
  // into the composer (never auto-sent — the player always gets the last word).
  const muse = useCallback(async () => {
    if (streaming || museLoading) return;
    setMuseLoading(true);
    try {
      const r = await tavern.aiReply(saveId);
      if (r?.text) setInput(r.text);
    } catch {
      /* silent — the muse is just a convenience */
    } finally {
      setMuseLoading(false);
    }
  }, [streaming, museLoading, saveId]);

  // Answer the GM's pending choice: record it server-side, dismiss the fork, then send
  // the chosen text as the next turn so the story advances on the player's decision.
  const answerChoice = useCallback(
    async (choice: string) => {
      if (answeringQ || streaming) return;
      setAnsweringQ(true);
      try {
        await game.clearQuestion(pendingQ?.id, choice);
        setPendingQ(null);
        setInput(choice);
      } catch {
        /* if clearing fails, still let the player type */
      } finally {
        setAnsweringQ(false);
      }
    },
    [answeringQ, streaming, pendingQ],
  );

  const dismissChoice = useCallback(async () => {
    setAnsweringQ(true);
    try {
      await game.clearQuestion(pendingQ?.id);
    } catch {
      /* noop */
    } finally {
      setPendingQ(null);
      setAnsweringQ(false);
    }
  }, [pendingQ]);

  // Continue: let the GM advance a beat with no player prose (passive/cutscene moments).
  const onContinue = useCallback(() => {
    if (streaming) return;
    setInput("继续");
    setTimeout(() => send(), 0);
  }, [streaming, send]);

  // Cycle the GM state-write permission mode and persist it.
  const cyclePermission = useCallback(async () => {
    const next = PERM_CYCLE[(PERM_CYCLE.indexOf(permMode) + 1) % PERM_CYCLE.length];
    setPermMode(next);
    try {
      await permissions.setMode(next);
    } catch {
      /* revert silently on failure */
      setPermMode(permMode);
    }
  }, [permMode]);

  // Pick a slash command: arg-less ones send immediately; arg ones prefill the composer.
  const runSlash = useCallback(
    (entry: { cmd: string; arg: boolean }) => {
      setSlashOpen(false);
      if (entry.arg) {
        setInput(entry.cmd);
      } else {
        setInput(entry.cmd);
        setTimeout(() => send(), 0);
      }
    },
    [send],
  );

  // Attach an image: pick from gallery, encode as a data_url the backend's _save_attachments consumes.
  const addAttachment = useCallback(async () => {
    if (streaming) return;
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      Alert.alert("需要相册权限", "请在系统设置中允许访问相册。");
      return;
    }
    const res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], quality: 0.7, base64: true });
    if (res.canceled || !res.assets?.[0]) return;
    const a = res.assets[0];
    if (!a.base64) {
      Alert.alert("读取失败", "无法读取图片数据。");
      return;
    }
    const type = a.mimeType || "image/jpeg";
    setPendingAttachments((prev) => [
      ...prev,
      { name: a.fileName || `image-${prev.length + 1}.jpg`, type, data_url: `data:${type};base64,${a.base64}` },
    ]);
    await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
  }, [streaming]);

  // @mention: insert @Name into the composer, replacing the trailing partial @token.
  const pickMention = useCallback((name: string) => {
    setMentionOpen(false);
    setInput((prev) => prev.replace(/@[^\s@]*$/, "") + `@${name} `);
  }, []);

  // Open the @ list when the composer's trailing token starts with @.
  const onInputChange = useCallback((t: string) => {
    setInput(t);
    setMentionOpen(/@[^\s@]*$/.test(t) && relationNames.length > 0);
  }, [relationNames.length]);

  // Long-press a message: copy / edit / regenerate / delete-after.
  const editMsg = useCallback(
    (message: ChatMessage) => {
      if (streaming) return;
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);

      const isUser = message.role === "user";
      const canEdit = message.messageIndex != null;
      const actions: { text: string; style?: "destructive" | "cancel"; onPress?: () => void }[] = [];

      actions.push({
        text: "复制文本",
        onPress: () => Clipboard.setStringAsync(message.text).catch(() => {}),
      });

      if (canEdit) {
        actions.push({
          text: "编辑此回合",
          onPress: () => {
            setEditTarget({ index: message.messageIndex!, id: message.id });
            setInput(message.text);
          },
        });
      }

      if (!isUser) {
        actions.push({
          text: "重新生成",
          onPress: () => retryLast(),
        });
      }

      if (canEdit) {
        actions.push({
          text: "删除此回合及以后",
          style: "destructive",
          onPress: () => {
            Alert.alert("删除", "该回合及之后的对话将被删除，确定？", [
              { text: "取消", style: "cancel" },
              {
                text: "删除", style: "destructive",
                onPress: async () => {
                  try {
                    await branches.rollback(saveId, message.messageIndex!);
                    boot();
                  } catch (e) {
                    Alert.alert("删除失败", e instanceof Error ? e.message : "请重试");
                  }
                },
              },
            ]);
          },
        });
      }

      actions.push({ text: "取消", style: "cancel" });
      Alert.alert(
        isUser ? "玩家发言" : "GM 回复",
        message.text.slice(0, 80) + (message.text.length > 80 ? "…" : ""),
        actions,
      );
    },
    [streaming, saveId, retryLast, boot],
  );

  const commitEdit = useCallback(async () => {
    if (!editTarget) return;
    const content = input.trim();
    if (!content) return;
    const { index, id } = editTarget;
    try {
      await game.editMessage(saveId, index, content);
      setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, text: content } : m)));
      setEditTarget(null);
      setInput("");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "编辑失败";
      // surface inline on the edited bubble
      setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, error: msg } : m)));
    }
  }, [editTarget, input, saveId]);

  const cancelEdit = useCallback(() => {
    setEditTarget(null);
    setInput("");
  }, []);

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1, alignItems: "center" }}>
          <Pressable onPress={() => setModelPickerOpen(true)} hitSlop={8}>
            <Text style={styles.headTitle} numberOfLines={1}>
              {title || "对话"} <Text style={styles.headTitleCaret}>⌄</Text>
            </Text>
          </Pressable>
          {usage ? (
            <Pressable onPress={() => setLedgerOpen(true)} hitSlop={8}>
              <UsageRing pct={usage.pct} />
            </Pressable>
          ) : null}
        </View>
        <IconLabelButton
          glyph="⌥"
          label="分支"
          onPress={() => router.push({ pathname: "/(app)/tree/[id]", params: { id: String(saveId), title: title || "对话" } })}
        />
        <IconLabelButton glyph="⋯" label="更多" onPress={() => setOverflowOpen(true)} />
      </View>

      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        keyboardVerticalOffset={Platform.OS === "ios" ? insets.top + 48 : 0}
      >
        {bootLoading ? (
          <View style={styles.boot}>
            <ActivityIndicator color={theme.color.accent} />
          </View>
        ) : (
          <FlatList
            ref={listRef}
            data={messages}
            keyExtractor={(m) => m.id}
            renderItem={({ item }) => <MessageBubble message={item} onLongPress={editMsg} onRetry={item.error ? retryLast : undefined} />}
            contentContainerStyle={{ paddingHorizontal: theme.space(5), paddingTop: theme.space(4), paddingBottom: theme.space(6) }}
            onContentSizeChange={autoScrollIfNearBottom}
            onScroll={handleScroll}
            scrollEventThrottle={100}
            ListEmptyComponent={
              <Text style={styles.placeholder}>幕布升起，等待你的第一句话…</Text>
            }
            ListFooterComponent={
              <>
                {streaming && stageLabel ? (
                  <View style={styles.stagePill}>
                    <ActivityIndicator size="small" color={theme.color.accent} />
                    <Text style={styles.stageText}>{stageLabel}</Text>
                  </View>
                ) : null}
                {pendingQ && !streaming ? (
                  <OracleFork question={pendingQ} onChoose={answerChoice} onDismiss={dismissChoice} busy={answeringQ} />
                ) : null}
              </>
            }
          />
        )}

        {editTarget ? (
          <View style={styles.editBanner}>
            <Text style={styles.editBannerText}>编辑此回合</Text>
            <Pressable onPress={cancelEdit} hitSlop={8}>
              <Text style={styles.editCancel}>取消</Text>
            </Pressable>
          </View>
        ) : null}

        {!editTarget && !streaming ? (
          <View style={styles.toolbar}>
            <Pressable onPress={onContinue} style={styles.toolChip} hitSlop={6}>
              <Text style={styles.toolChipGlyph}>▷</Text>
              <Text style={styles.toolChipText}>继续</Text>
            </Pressable>
            <Pressable onPress={() => setSlashOpen(true)} style={styles.toolChip} hitSlop={6}>
              <Text style={styles.toolChipGlyph}>/</Text>
              <Text style={styles.toolChipText}>命令</Text>
            </Pressable>
            <Pressable onPress={cyclePermission} style={[styles.toolChip, styles.permChip]} hitSlop={6}>
              <Text style={styles.permChipText}>{PERM_LABEL[permMode] || "默认权限"}</Text>
            </Pressable>
          </View>
        ) : null}

        {mentionOpen ? (
          <View style={styles.mentionBar}>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} keyboardShouldPersistTaps="handled" contentContainerStyle={{ gap: theme.space(2), paddingHorizontal: theme.space(4) }}>
              {relationNames
                .filter((n) => {
                  const m = /@([^\s@]*)$/.exec(input);
                  const q = (m?.[1] || "").toLowerCase();
                  return !q || n.toLowerCase().includes(q);
                })
                .slice(0, 12)
                .map((n) => (
                  <Pressable key={n} onPress={() => pickMention(n)} style={styles.mentionChip}>
                    <Text style={styles.mentionText}>@{n}</Text>
                  </Pressable>
                ))}
            </ScrollView>
          </View>
        ) : null}

        {pendingAttachments.length > 0 ? (
          <View style={styles.attachBar}>
            {pendingAttachments.map((a, i) => (
              <View key={i} style={styles.attachChip}>
                <Image source={{ uri: a.data_url }} style={styles.attachThumb} contentFit="cover" />
                <Pressable onPress={() => setPendingAttachments((prev) => prev.filter((_, j) => j !== i))} style={styles.attachRemove} hitSlop={6}>
                  <Text style={styles.attachRemoveText}>×</Text>
                </Pressable>
              </View>
            ))}
          </View>
        ) : null}

        <View style={[styles.composer, { paddingBottom: insets.bottom + theme.space(3) }]}>
          {!editTarget ? (
            <Pressable onPress={addAttachment} disabled={streaming} style={[styles.composerSlot, streaming && { opacity: 0.4 }]} hitSlop={6}>
              <Text style={styles.attachGlyph}>＋</Text>
              <Text style={styles.composerSlotLabel}>附件</Text>
            </Pressable>
          ) : null}
          {!editTarget ? (
            <Pressable
              onPress={muse}
              disabled={streaming || museLoading}
              style={[styles.composerSlot, (streaming || museLoading) && { opacity: 0.4 }]}
              hitSlop={6}
            >
              {museLoading ? (
                <ActivityIndicator color={theme.color.magic} size="small" />
              ) : (
                <Text style={styles.museGlyph}>✶</Text>
              )}
              <Text style={styles.composerSlotLabel}>灵感</Text>
            </Pressable>
          ) : null}
          <TextInput
            value={input}
            onChangeText={onInputChange}
            placeholder={editTarget ? "修订这段文字…" : "低语，或行动…"}
            placeholderTextColor={theme.color.textFaint}
            style={styles.composerInput}
            multiline
            editable={!streaming}
          />
          {editTarget ? (
            <Pressable onPress={commitEdit} disabled={!input.trim()} style={[styles.sendBtn, styles.editBtn, !input.trim() && { opacity: 0.4 }]}>
              <Text style={styles.sendGlyph}>✓</Text>
            </Pressable>
          ) : streaming ? (
            <Pressable onPress={stop} style={[styles.sendBtn, styles.stopBtn]}>
              <View style={styles.stopSquare} />
            </Pressable>
          ) : (
            <Pressable onPress={send} disabled={!input.trim()} style={[styles.sendBtn, !input.trim() && { opacity: 0.4 }]}>
              <Text style={styles.sendGlyph}>➤</Text>
            </Pressable>
          )}
        </View>
      </KeyboardAvoidingView>

      <CharacterSheet visible={sheetOpen} chatId={saveId} onClose={() => setSheetOpen(false)} />
      <MemoryCodex visible={codexOpen} onClose={() => setCodexOpen(false)} />
      <ContextLedger visible={ledgerOpen} onClose={() => setLedgerOpen(false)} />
      <ScryingGlass visible={scryOpen} saveId={saveId} onClose={() => setScryOpen(false)} />
      <WorldCodex visible={worldOpen} onClose={() => setWorldOpen(false)} />
      <CodexOfTrials visible={trialsOpen} onClose={() => setTrialsOpen(false)} />
      <ModelEffortPicker visible={modelPickerOpen} saveId={saveId} onClose={() => setModelPickerOpen(false)} />

      <Modal visible={slashOpen} transparent animationType="fade" onRequestClose={() => setSlashOpen(false)}>
        <Pressable style={styles.slashBackdrop} onPress={() => setSlashOpen(false)} />
        <View style={[styles.slashSheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
          <View style={styles.slashGrab} />
          <Text style={styles.slashTitle}>斜杠命令</Text>
          {SLASH_COMMANDS.map((c) => (
            <Pressable key={c.cmd} onPress={() => runSlash(c)} style={({ pressed }) => [styles.slashRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
              <Text style={styles.slashCmd}>{c.cmd.trim()}</Text>
              <Text style={styles.slashLabel}>{c.label}</Text>
            </Pressable>
          ))}
        </View>
      </Modal>

      {/* Overflow sheet — secondary actions previously crammed into the header. */}
      <Modal visible={overflowOpen} transparent animationType="slide" onRequestClose={() => setOverflowOpen(false)}>
        <Pressable style={styles.slashBackdrop} onPress={() => setOverflowOpen(false)} />
        <View style={[styles.slashSheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
          <View style={styles.slashGrab} />
          <Text style={styles.slashTitle}>更多</Text>
          <View style={styles.overflowGrid}>
            <Pressable
              onPress={() => { setOverflowOpen(false); setCodexOpen(true); }}
              style={({ pressed }) => [styles.overflowCard, pressed && { backgroundColor: theme.color.bgElevated }]}
            >
              <Text style={styles.overflowGlyph}>✦</Text>
              <Text style={styles.overflowLabel}>记忆</Text>
              <Text style={styles.overflowHint}>调阅 GM 的记事桶</Text>
            </Pressable>
            <Pressable
              onPress={() => { setOverflowOpen(false); setWorldOpen(true); }}
              style={({ pressed }) => [styles.overflowCard, pressed && { backgroundColor: theme.color.bgElevated }]}
            >
              <Text style={styles.overflowGlyph}>⊛</Text>
              <Text style={styles.overflowLabel}>世界</Text>
              <Text style={styles.overflowHint}>关系 · 规则 · 世界书</Text>
            </Pressable>
            <Pressable
              onPress={() => { setOverflowOpen(false); setScryOpen(true); }}
              style={({ pressed }) => [styles.overflowCard, pressed && { backgroundColor: theme.color.bgElevated }]}
            >
              <Text style={styles.overflowGlyph}>◎</Text>
              <Text style={styles.overflowLabel}>察视</Text>
              <Text style={styles.overflowHint}>检索剧情 · 摘要</Text>
            </Pressable>
            <Pressable
              onPress={() => { setOverflowOpen(false); setTrialsOpen(true); }}
              style={({ pressed }) => [styles.overflowCard, pressed && { backgroundColor: theme.color.bgElevated }]}
            >
              <Text style={styles.overflowGlyph}>⚔</Text>
              <Text style={styles.overflowLabel}>试炼</Text>
              <Text style={styles.overflowHint}>任务 · 抽奖 · 投骰</Text>
            </Pressable>
            <Pressable
              onPress={() => { setOverflowOpen(false); setSheetOpen(true); }}
              style={({ pressed }) => [styles.overflowCard, pressed && { backgroundColor: theme.color.bgElevated }]}
            >
              <Text style={styles.overflowGlyph}>❖</Text>
              <Text style={styles.overflowLabel}>角色卡</Text>
              <Text style={styles.overflowHint}>查阅当前角色面板</Text>
            </Pressable>
            <Pressable
              onPress={() => { setOverflowOpen(false); setLedgerOpen(true); }}
              style={({ pressed }) => [styles.overflowCard, pressed && { backgroundColor: theme.color.bgElevated }]}
            >
              <Text style={styles.overflowGlyph}>◷</Text>
              <Text style={styles.overflowLabel}>账册</Text>
              <Text style={styles.overflowHint}>上下文 · 费用</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(2), borderBottomWidth: 1, borderBottomColor: theme.color.surfaceLine },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  headGlyphSmall: { fontSize: 20, color: theme.color.accent },
  headTitle: { fontFamily: theme.font.displaySemi, fontSize: theme.size.md, color: theme.color.text, letterSpacing: 0.5 },
  headTitleCaret: { fontSize: theme.size.sm, color: theme.color.accent },
  boot: { flex: 1, alignItems: "center", justifyContent: "center" },
  placeholder: { fontFamily: theme.font.proseItalic, fontSize: theme.size.md, color: theme.color.textFaint, textAlign: "center", marginTop: theme.space(20) },
  stagePill: { flexDirection: "row", alignItems: "center", gap: theme.space(2), alignSelf: "flex-start", marginHorizontal: theme.space(5), marginTop: theme.space(2), paddingHorizontal: theme.space(4), paddingVertical: theme.space(2), borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  stageText: { fontFamily: theme.font.proseItalic, fontSize: theme.size.sm, color: theme.color.accent },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: theme.space(3), paddingHorizontal: theme.space(4), paddingTop: theme.space(3), borderTopWidth: 1, borderTopColor: theme.color.surfaceLine, backgroundColor: theme.color.bgElevated },
  composerInput: { flex: 1, maxHeight: 130, minHeight: 48, backgroundColor: theme.color.bgInput, borderRadius: theme.radius.lg, borderWidth: 1, borderColor: theme.color.surfaceLine, paddingHorizontal: theme.space(4), paddingTop: theme.space(3), paddingBottom: theme.space(3), color: theme.color.text, fontFamily: theme.font.prose, fontSize: theme.size.md, lineHeight: 22 },
  toolbar: { flexDirection: "row", gap: theme.space(2), paddingHorizontal: theme.space(4), paddingTop: theme.space(2), backgroundColor: theme.color.bgElevated },
  toolChip: { flexDirection: "row", alignItems: "center", gap: theme.space(1), paddingHorizontal: theme.space(3), paddingVertical: theme.space(1.5), borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.surfaceLineStrong },
  toolChipGlyph: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.accent },
  toolChipText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.textDim },
  permChip: { marginLeft: "auto", borderColor: theme.color.magicSoft, backgroundColor: theme.color.magicSoft },
  permChipText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.xs, color: theme.color.magic, letterSpacing: 0.5 },
  slashBackdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: theme.color.scrim },
  slashSheet: { position: "absolute", left: 0, right: 0, bottom: 0, backgroundColor: "rgba(20,16,12,0.97)", borderTopLeftRadius: theme.radius.xl, borderTopRightRadius: theme.radius.xl, borderWidth: 1, borderColor: theme.color.surfaceLineStrong, paddingHorizontal: theme.space(5), paddingTop: theme.space(3) },
  slashGrab: { alignSelf: "center", width: 44, height: 4, borderRadius: 2, backgroundColor: theme.color.surfaceLineStrong, marginBottom: theme.space(3) },
  slashTitle: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.text, letterSpacing: 1, marginBottom: theme.space(2) },
  slashRow: { flexDirection: "row", alignItems: "center", gap: theme.space(4), paddingVertical: theme.space(3), borderBottomWidth: 1, borderBottomColor: theme.color.surfaceLine },
  slashCmd: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.accent, width: 96 },
  slashLabel: { flex: 1, fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.text },
  overflowGrid: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(3), paddingBottom: theme.space(2) },
  overflowCard: {
    width: "47%",
    flexGrow: 1,
    paddingVertical: theme.space(4),
    paddingHorizontal: theme.space(3),
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    backgroundColor: theme.color.bgCard,
    alignItems: "center",
    gap: theme.space(1),
  },
  overflowGlyph: {
    fontSize: 26,
    color: theme.color.accent,
    textShadowColor: theme.color.accentSoft,
    textShadowRadius: 10,
  },
  overflowLabel: {
    fontFamily: theme.font.display,
    fontSize: theme.size.md,
    color: theme.color.text,
    letterSpacing: 0.5,
  },
  overflowHint: {
    fontFamily: theme.font.prose,
    fontSize: theme.size.xs,
    color: theme.color.textFaint,
    textAlign: "center",
  },
  sendBtn: { width: 48, height: 48, borderRadius: theme.radius.pill, backgroundColor: theme.color.accent, alignItems: "center", justifyContent: "center", marginBottom: 1 },
  sendGlyph: { fontSize: 20, color: theme.color.bg, marginLeft: 2 },
  museBtn: { width: 44, height: 48, alignItems: "center", justifyContent: "center", marginBottom: 1 },
  composerSlot: { width: 44, alignItems: "center", justifyContent: "center", marginBottom: 1, paddingTop: 4, gap: 1 },
  composerSlotLabel: { fontFamily: theme.font.proseSemi, fontSize: 9, letterSpacing: 1, color: theme.color.textFaint },
  museGlyph: { fontSize: 22, color: theme.color.magic },
  attachGlyph: { fontSize: 24, color: theme.color.accent, marginTop: -2 },
  mentionBar: { paddingVertical: theme.space(2), backgroundColor: theme.color.bgElevated, borderTopWidth: 1, borderTopColor: theme.color.surfaceLine },
  mentionChip: { paddingHorizontal: theme.space(3), paddingVertical: theme.space(1.5), borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.magicSoft, backgroundColor: theme.color.magicSoft },
  mentionText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.magic },
  attachBar: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(2), paddingHorizontal: theme.space(4), paddingVertical: theme.space(2), backgroundColor: theme.color.bgElevated, borderTopWidth: 1, borderTopColor: theme.color.surfaceLine },
  attachChip: { width: 56, height: 56, borderRadius: theme.radius.sm, overflow: "hidden", borderWidth: 1, borderColor: theme.color.surfaceLineStrong },
  attachThumb: { width: "100%", height: "100%" },
  attachRemove: { position: "absolute", top: 0, right: 0, width: 20, height: 20, alignItems: "center", justifyContent: "center", backgroundColor: theme.color.scrim },
  attachRemoveText: { color: "#fff", fontSize: 14, marginTop: -2 },
  stopBtn: { backgroundColor: theme.color.danger },
  stopSquare: { width: 14, height: 14, borderRadius: 3, backgroundColor: theme.color.text },
  editBanner: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: theme.space(5), paddingVertical: theme.space(2), backgroundColor: theme.color.magicSoft, borderTopWidth: 1, borderTopColor: theme.color.surfaceLine },
  editBannerText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1.5, textTransform: "uppercase", color: theme.color.magic },
  editCancel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.textDim },
  editBtn: { backgroundColor: theme.color.magic },
});
