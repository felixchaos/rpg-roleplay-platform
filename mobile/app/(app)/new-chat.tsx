import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Image } from "expo-image";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as DocumentPicker from "expo-document-picker";
import * as Haptics from "expo-haptics";
import Animated, { FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { tavern, cards } from "@/api";
import { baseUrl, ApiError } from "@/api/http";
import { appendFile } from "@/api/formdata";
import { theme } from "@/theme/theme";

type Source = "mine" | "public";

/**
 * New-chat character picker. Three ways in: your own card library, the public
 * library (clone + chat), or import a SillyTavern card file. Picking any card
 * spins up a tavern save and drops you into the conversation.
 */
export default function NewChatScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [source, setSource] = useState<Source>("mine");
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [base, setBase] = useState("");
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setBase(await baseUrl());
      const r = source === "mine" ? await cards.characterCards(query) : await cards.publicCards(query);
      setItems(r?.items ?? []);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, [source, query]);

  useEffect(() => {
    load();
  }, [load]);

  const startWithCard = async (card: any) => {
    setBusyId(card.id);
    try {
      let cardId = card.id;
      if (source === "public") {
        const cloned = await cards.clonePublicCard(card.id);
        cardId = cloned?.card?.id ?? cardId;
      }
      const r = await tavern.create({ character_card_id: cardId, title: card.name });
      const chatId = r?.save?.id;
      if (chatId) {
        await tavern.activate(chatId);
        await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
        router.replace({ pathname: "/(app)/chat/[id]", params: { id: String(chatId), title: card.name } });
      }
    } catch (e) {
      Alert.alert("无法开启", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setBusyId(null);
    }
  };

  const onImport = async () => {
    const res = await DocumentPicker.getDocumentAsync({
      type: ["image/png", "image/webp", "application/json"],
      copyToCacheDirectory: true,
    });
    if (res.canceled || !res.assets?.[0]) return;
    const file = res.assets[0];
    setBusyId(-1);
    try {
      const form = new FormData();
      await appendFile(form, "file", file);
      const r = await tavern.importCharacter(form);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      await tavern.activate(r.save_id);
      router.replace({ pathname: "/(app)/chat/[id]", params: { id: String(r.save_id), title: r.character_name } });
    } catch (e) {
      Alert.alert("导入失败", e instanceof ApiError ? e.message : "请检查卡片格式");
    } finally {
      setBusyId(null);
    }
  };

  const renderCard = ({ item, index }: { item: any; index: number }) => {
    const avatar = item.avatar_path || item.avatar_url;
    const uri = avatar ? (avatar.startsWith("http") ? avatar : base + avatar) : null;
    const initial = (item.name || "?").trim().charAt(0).toUpperCase();
    const busy = busyId === item.id;
    return (
      <Animated.View entering={FadeInDown.delay(Math.min(index, 10) * 45).duration(380).springify().damping(18)}>
        <Pressable onPress={() => startWithCard(item)} disabled={busy} style={({ pressed }) => [styles.card, pressed && { backgroundColor: theme.color.bgElevated }]}>
          {uri ? (
            <Image source={{ uri }} style={styles.avatar} contentFit="cover" transition={200} />
          ) : (
            <View style={[styles.avatar, styles.avatarFallback]}>
              <Text style={styles.avatarInitial}>{initial}</Text>
            </View>
          )}
          <View style={{ flex: 1, gap: theme.space(1) }}>
            <Text style={styles.cardName} numberOfLines={1}>{item.name || "无名角色"}</Text>
            <Text style={styles.cardDesc} numberOfLines={2}>
              {item.description || item.personality || item.tagline || "—"}
            </Text>
          </View>
          {busy ? <ActivityIndicator color={theme.color.accent} /> : <Text style={styles.go}>{source === "public" ? "克隆" : "开聊"}</Text>}
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
          <Text style={styles.kicker}>New Chat</Text>
          <Text style={styles.h1}>择一角色</Text>
        </View>
        <Pressable onPress={() => router.push("/(app)/card-edit")} hitSlop={12} style={styles.importBtn}>
          <Text style={styles.importGlyph}>✎</Text>
        </Pressable>
        <Pressable onPress={onImport} hitSlop={12} style={styles.importBtn} disabled={busyId === -1}>
          {busyId === -1 ? <ActivityIndicator color={theme.color.accent} size="small" /> : <Text style={styles.importGlyph}>⬆</Text>}
        </Pressable>
      </View>

      <View style={styles.seg}>
        {(["mine", "public"] as Source[]).map((s) => (
          <Pressable key={s} onPress={() => setSource(s)} style={[styles.segItem, source === s && styles.segActive]}>
            <Text style={[styles.segText, source === s && styles.segTextActive]}>{s === "mine" ? "我的角色" : "公开库"}</Text>
          </Pressable>
        ))}
      </View>

      <TextInput
        value={query}
        onChangeText={setQuery}
        placeholder="搜索角色…"
        placeholderTextColor={theme.color.textFaint}
        style={styles.search}
        autoCapitalize="none"
        returnKeyType="search"
        onSubmitEditing={load}
      />

      <FlatList
        data={items}
        keyExtractor={(c) => String(c.id)}
        renderItem={renderCard}
        contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 40, gap: theme.space(3), paddingTop: theme.space(2) }}
        ListEmptyComponent={
          loading ? (
            <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
          ) : (
            <View style={styles.empty}>
              <Text style={styles.emptyGlyph}>❦</Text>
              <Text style={styles.emptyText}>
                {source === "mine" ? "你还没有角色卡。点右上角导入一张，或去公开库逛逛。" : "公开库暂无结果。"}
              </Text>
            </View>
          )
        }
      />
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  importBtn: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.surfaceLine },
  importGlyph: { fontSize: 20, color: theme.color.accent },
  seg: { flexDirection: "row", marginHorizontal: theme.space(6), marginBottom: theme.space(3), backgroundColor: theme.color.bgInput, borderRadius: theme.radius.md, padding: 3, borderWidth: 1, borderColor: theme.color.surfaceLine },
  segItem: { flex: 1, paddingVertical: theme.space(2.5), alignItems: "center", borderRadius: theme.radius.sm },
  segActive: { backgroundColor: theme.color.accentGhost },
  segText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.sm, letterSpacing: 1.5, color: theme.color.textFaint },
  segTextActive: { color: theme.color.accentBright },
  search: { marginHorizontal: theme.space(6), marginBottom: theme.space(3), backgroundColor: theme.color.bgInput, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, paddingHorizontal: theme.space(4), paddingVertical: theme.space(3), color: theme.color.text, fontFamily: theme.font.prose, fontSize: theme.size.base },
  card: { flexDirection: "row", alignItems: "center", gap: theme.space(4), padding: theme.space(3), borderRadius: theme.radius.lg, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  avatar: { width: 60, height: 60, borderRadius: theme.radius.md },
  avatarFallback: { backgroundColor: theme.color.bgInput, alignItems: "center", justifyContent: "center" },
  avatarInitial: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.accent },
  cardName: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  cardDesc: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 19 },
  go: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1.5, color: theme.color.accent, textTransform: "uppercase" },
  empty: { alignItems: "center", paddingTop: theme.space(24), paddingHorizontal: theme.space(10), gap: theme.space(3) },
  emptyGlyph: { fontSize: 44, color: theme.color.accent, opacity: 0.6 },
  emptyText: { fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.textFaint, textAlign: "center", lineHeight: 23 },
});
