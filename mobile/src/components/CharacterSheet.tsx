/**
 * Character / persona / system-prompt panel — slides up from the chat screen.
 * Mirrors the web client's right-panel tabs: AI character card, your persona,
 * and a per-conversation system-prompt override. Also exposes immersive toggle.
 */
import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";
import { BlurView } from "expo-blur";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { tavern, game, cards, NpcCardRef } from "@/api";
import { ApiError } from "@/api/http";
import { EmberButton, RuneDivider } from "@/components/ui";
import { theme, palette } from "@/theme/theme";

type Tab = "character" | "persona" | "prompt";

export function CharacterSheet({
  visible,
  chatId,
  onClose,
}: {
  visible: boolean;
  chatId: number;
  onClose: () => void;
}) {
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState<Tab>("character");
  const [loading, setLoading] = useState(false);
  const [characterDesc, setCharacterDesc] = useState("");
  const [characterName, setCharacterName] = useState("");
  const [personaName, setPersonaName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [immersive, setImmersive] = useState(false);
  const [npcCards, setNpcCards] = useState<NpcCardRef[]>([]);
  const [savingPrompt, setSavingPrompt] = useState(false);
  const [myPersonas, setMyPersonas] = useState<any[]>([]);
  const [boundPersonaId, setBoundPersonaId] = useState<number | null>(null);
  const [bindingId, setBindingId] = useState<number | null>(null);

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const res = await game.state();
        if (!alive) return;
        const state = res?.state ?? res;
        const tav = state?.tavern || {};
        const char = tav.character_card || tav.character || {};
        const persona = tav.persona_card || tav.persona || {};
        setCharacterName(char.name || tav.character_name || "");
        setCharacterDesc(char.description || char.personality || char.scenario || "");
        setPersonaName(persona.name || "");
        setBoundPersonaId(tav.persona_card_id ?? persona.id ?? null);
        setSystemPrompt(tav.system_prompt || "");
        setImmersive(!!tav.immersive);
        const npcRaw = state?.last_context?.npc_cards;
        setNpcCards(Array.isArray(npcRaw) ? npcRaw : []);
      } catch {
        /* keep blanks */
      } finally {
        if (alive) setLoading(false);
      }
      // load the player's persona roster for the bind picker (best-effort)
      cards.personas().then((r) => setMyPersonas(r?.items ?? [])).catch(() => {});
    })();
    return () => { alive = false; };
  }, [visible, chatId]);

  const bindPersona = async (personaId: number | null) => {
    setBindingId(personaId ?? -1);
    try {
      await tavern.bindCard(chatId, "persona", personaId);
      setBoundPersonaId(personaId);
      const picked = myPersonas.find((p) => p.id === personaId);
      setPersonaName(personaId == null ? "" : picked?.name || "");
    } catch (e) {
      Alert.alert("绑定失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setBindingId(null);
    }
  };

  const savePrompt = async () => {
    setSavingPrompt(true);
    try {
      await tavern.setSystemPrompt(chatId, systemPrompt);
      Alert.alert("已保存", "本对话的系统提示词已更新。");
    } catch (e) {
      Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setSavingPrompt(false);
    }
  };

  const toggleImmersive = async (v: boolean) => {
    setImmersive(v);
    try {
      await tavern.setImmersive(chatId, v);
    } catch {
      setImmersive(!v);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
        <BlurView intensity={28} tint="dark" style={StyleSheet.absoluteFill} />
        <View style={styles.grabber} />

        <View style={styles.tabs}>
          {(["character", "persona", "prompt"] as Tab[]).map((t) => (
            <Pressable key={t} onPress={() => setTab(t)} style={[styles.tab, tab === t && styles.tabActive]}>
              <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>
                {t === "character" ? "角色" : t === "persona" ? "我" : "系统提示"}
              </Text>
            </Pressable>
          ))}
        </View>

        {loading ? (
          <ActivityIndicator color={theme.color.accent} style={{ marginVertical: theme.space(10) }} />
        ) : (
          <ScrollView style={styles.body} keyboardShouldPersistTaps="handled">
            {tab === "character" ? (
              <View style={{ gap: theme.space(3) }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: theme.space(2) }}>
                <Text style={styles.cardName}>{characterName || "未命名角色"}</Text>
                {npcCards.some((n) => n.name && characterName && (n.name === characterName || characterName.includes(n.name) || n.name.includes(characterName))) ? (
                  <View style={styles.npcBadge}>
                    <Text style={styles.npcBadgeText}>本轮</Text>
                  </View>
                ) : null}
              </View>
                <RuneDivider />
                <Text style={styles.cardDesc}>{characterDesc || "这张卡没有提供描述。"}</Text>
                <View style={styles.immersiveRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.immersiveLabel}>沉浸拟人模式</Text>
                    <Text style={styles.immersiveHint}>注入指令，让角色更贴近真人语气</Text>
                  </View>
                  <Switch
                    value={immersive}
                    onValueChange={toggleImmersive}
                    trackColor={{ false: theme.color.bgInput, true: theme.color.accentDeep }}
                    thumbColor={immersive ? theme.color.accentBright : theme.color.textFaint}
                  />
                </View>
              </View>
            ) : null}

            {tab === "persona" ? (
              <View style={{ gap: theme.space(3) }}>
                <Text style={styles.cardName}>{personaName || "默认旅人"}</Text>
                <RuneDivider />
                <Text style={styles.cardDesc}>选择你在这段故事里的身份。</Text>
                <Pressable
                  onPress={() => bindPersona(null)}
                  style={[styles.personaOpt, boundPersonaId == null && styles.personaOptActive]}
                  disabled={bindingId != null}
                >
                  <Text style={[styles.personaOptText, boundPersonaId == null && { color: theme.color.accentBright }]}>默认旅人（不绑定）</Text>
                  {bindingId === -1 ? <ActivityIndicator size="small" color={theme.color.accent} /> : null}
                </Pressable>
                {myPersonas.map((p) => {
                  const active = boundPersonaId === p.id;
                  return (
                    <Pressable
                      key={p.id}
                      onPress={() => bindPersona(p.id)}
                      style={[styles.personaOpt, active && styles.personaOptActive]}
                      disabled={bindingId != null}
                    >
                      <View style={{ flex: 1 }}>
                        <Text style={[styles.personaOptText, active && { color: theme.color.accentBright }]}>{p.name || "无名"}</Text>
                        {p.identity || p.personality ? <Text style={styles.personaOptSub} numberOfLines={1}>{p.identity || p.personality}</Text> : null}
                      </View>
                      {bindingId === p.id ? <ActivityIndicator size="small" color={theme.color.accent} /> : active ? <Text style={styles.personaCheck}>✦</Text> : null}
                    </Pressable>
                  );
                })}
                {myPersonas.length === 0 ? (
                  <Text style={styles.cardDesc}>你还没有 persona。在「设置 → 我的身份」中铸造一个。</Text>
                ) : null}
              </View>
            ) : null}

            {tab === "prompt" ? (
              <View style={{ gap: theme.space(3) }}>
                <Text style={styles.cardDesc}>本对话的系统提示词覆盖（越狱 / 人设 / 行为指令）。</Text>
                <TextInput
                  value={systemPrompt}
                  onChangeText={setSystemPrompt}
                  multiline
                  placeholder="留空则使用默认。"
                  placeholderTextColor={theme.color.textFaint}
                  style={styles.promptInput}
                />
                <EmberButton label={savingPrompt ? "保存中…" : "保存提示词"} onPress={savePrompt} loading={savingPrompt} />
              </View>
            ) : null}
          </ScrollView>
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: theme.color.scrim },
  sheet: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    maxHeight: "78%",
    backgroundColor: palette.scrimSheet,
    borderTopLeftRadius: theme.radius.xl,
    borderTopRightRadius: theme.radius.xl,
    borderWidth: 1,
    borderColor: theme.color.surfaceLineStrong,
    overflow: "hidden",
    paddingHorizontal: theme.space(5),
    paddingTop: theme.space(3),
  },
  grabber: { alignSelf: "center", width: 44, height: 4, borderRadius: 2, backgroundColor: theme.color.surfaceLineStrong, marginBottom: theme.space(4) },
  tabs: { flexDirection: "row", gap: theme.space(2), marginBottom: theme.space(4) },
  tab: { flex: 1, paddingVertical: theme.space(2.5), alignItems: "center", borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine },
  tabActive: { backgroundColor: theme.color.accentGhost, borderColor: theme.color.accentSoft },
  tabText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1.5, textTransform: "uppercase", color: theme.color.textFaint },
  tabTextActive: { color: theme.color.accentBright },
  body: { maxHeight: 420 },
  cardName: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text },
  cardDesc: { fontFamily: theme.font.prose, fontSize: theme.size.md, color: theme.color.textDim, lineHeight: 24 },
  personaOpt: { flexDirection: "row", alignItems: "center", gap: theme.space(3), padding: theme.space(3), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  personaOptActive: { borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  personaOptText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.text },
  personaOptSub: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, marginTop: 2 },
  personaCheck: { fontSize: theme.size.md, color: theme.color.accentBright },
  immersiveRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), marginTop: theme.space(3), paddingTop: theme.space(3), borderTopWidth: 1, borderTopColor: theme.color.surfaceLine },
  immersiveLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.text },
  npcBadge: { paddingHorizontal: theme.space(2), paddingVertical: 2, borderRadius: theme.radius.pill, backgroundColor: theme.color.accentSoft, borderWidth: 1, borderColor: theme.color.accent },
  npcBadgeText: { fontFamily: theme.font.displaySemi, fontSize: 9, letterSpacing: 1, color: theme.color.accentBright },
  immersiveHint: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint },
  promptInput: { minHeight: 180, backgroundColor: theme.color.bgInput, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, padding: theme.space(4), color: theme.color.text, fontFamily: theme.font.mono, fontSize: theme.size.sm, lineHeight: 20, textAlignVertical: "top" },
});
