import React, { useCallback, useState } from "react";
import {
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Image } from "expo-image";
import { useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import * as DocumentPicker from "expo-document-picker";
import Animated, { FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { GrimoireDock, DOCK_HEIGHT } from "@/components/GrimoireDock";
import { IconLabelButton } from "@/components/ui";
import { PolicyBanner } from "@/components/PolicyBanner";
import { tavern, TavernChat, tavernExport } from "@/api";
import { baseUrl, ApiError } from "@/api/http";
import { appendFile } from "@/api/formdata";
import { downloadAndShare } from "@/api/download";
import { usePrompt } from "@/components/PromptDialog";
import { theme } from "@/theme/theme";

export default function ChatsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { prompt, promptNode } = usePrompt();
  const [chats, setChats] = useState<TavernChat[]>([]);
  const [loading, setLoading] = useState(false);
  const [base, setBase] = useState("");
  const [archived, setArchived] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setBase(await baseUrl());
      const r = archived ? await tavern.listArchived() : await tavern.list();
      setChats(r?.chats ?? []);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, [archived]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const open = async (chat: TavernChat) => {
    try {
      await tavern.activate(chat.id);
      router.push({ pathname: "/(app)/chat/[id]", params: { id: String(chat.id), title: chat.title || chat.character_name } });
    } catch (e) {
      Alert.alert("无法进入", e instanceof ApiError ? e.message : "请重试");
    }
  };

  const onImportJsonl = async () => {
    const res = await DocumentPicker.getDocumentAsync({ type: ["application/jsonl", "application/json", "text/plain", "*/*"], copyToCacheDirectory: true });
    if (res.canceled || !res.assets?.[0]) return;
    const file = res.assets[0];
    try {
      const form = new FormData();
      await appendFile(form, "file", { uri: file.uri, name: file.name, mimeType: file.mimeType || "application/jsonl" });
      const r = await tavern.importJsonl(form);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      const cid = r.save_id ?? r.chat_id;
      if (cid) {
        await tavern.activate(cid);
        router.push({ pathname: "/(app)/chat/[id]", params: { id: String(cid), title: r.title || "导入对话" } });
      } else {
        load();
      }
    } catch (e) {
      Alert.alert("导入失败", e instanceof ApiError ? e.message : "请检查 JSONL 格式");
    }
  };

  const onLongPress = (chat: TavernChat) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    Alert.alert(chat.title || chat.character_name, "选择操作", [
      {
        text: "重命名",
        onPress: () =>
          prompt({
            title: "重命名对话",
            initialValue: chat.title || chat.character_name || "",
            placeholder: "新的名字",
            onConfirm: async (t) => { await tavern.rename(chat.id, t); load(); },
          }),
      },
      archived
        ? { text: "取回", onPress: async () => { await tavern.archive(chat.id, false); load(); } }
        : { text: "归档", onPress: async () => { await tavern.archive(chat.id, true); load(); } },
      {
        text: "导出 JSONL",
        onPress: async () => {
          try {
            await downloadAndShare(
              tavernExport.jsonlPath(chat.id),
              `tavern-${chat.id}.jsonl`,
            );
          } catch (e) {
            Alert.alert("导出失败", e instanceof Error ? e.message : "请重试");
          }
        },
      },
      {
        text: "删除",
        style: "destructive",
        onPress: () =>
          Alert.alert("删除对话", "此操作不可撤销，确定删除？", [
            { text: "取消", style: "cancel" },
            { text: "删除", style: "destructive", onPress: async () => { await tavern.remove(chat.id); load(); } },
          ]),
      },
      { text: "取消", style: "cancel" },
    ]);
  };

  const renderItem = ({ item, index }: { item: TavernChat; index: number }) => {
    const avatar = item.avatar_path ? (item.avatar_path.startsWith("http") ? item.avatar_path : base + item.avatar_path) : null;
    const initial = (item.character_name || "?").trim().charAt(0).toUpperCase();
    return (
      <Animated.View entering={FadeInDown.delay(Math.min(index, 8) * 55).duration(420).springify().damping(18)}>
      <Pressable
        onPress={() => open(item)}
        onLongPress={() => onLongPress(item)}
        style={({ pressed }) => [styles.row, pressed && { backgroundColor: theme.color.bgElevated }]}
      >
        <View style={styles.avatarWrap}>
          {avatar ? (
            <Image source={{ uri: avatar }} style={styles.avatar} contentFit="cover" transition={200} />
          ) : (
            <View style={[styles.avatar, styles.avatarFallback]}>
              <Text style={styles.avatarInitial}>{initial}</Text>
            </View>
          )}
          <View style={styles.avatarRing} pointerEvents="none" />
        </View>
        <View style={styles.rowBody}>
          <Text style={styles.rowTitle} numberOfLines={1}>
            {item.title || item.character_name || "无名对话"}
          </Text>
          <Text style={styles.rowSnippet} numberOfLines={2}>
            {item.last_snippet || "尚未开始这段故事…"}
          </Text>
        </View>
        <Text style={styles.rowIndex}>{String(index + 1).padStart(2, "0")}</Text>
      </Pressable>
      </Animated.View>
    );
  };

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(4) }]}>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Tavern</Text>
          <Text style={styles.h1}>{archived ? "封存卷宗" : "对话长卷"}</Text>
        </View>
        <IconLabelButton glyph={archived ? "✦" : "🗄"} label={archived ? "活跃" : "封存"} onPress={() => setArchived((v) => !v)} active={archived} />
        <IconLabelButton glyph="⎙" label="导入" onPress={onImportJsonl} />
      </View>

      <FlatList
        data={chats}
        keyExtractor={(c) => String(c.id)}
        renderItem={renderItem}
        contentContainerStyle={{ paddingBottom: insets.bottom + DOCK_HEIGHT + 80, paddingTop: theme.space(2) }}
        ItemSeparatorComponent={() => <View style={styles.sep} />}
        ListHeaderComponent={<PolicyBanner />}
        refreshControl={
          <RefreshControl refreshing={loading} onRefresh={load} tintColor={theme.color.accent} colors={[theme.color.accent]} />
        }
        ListEmptyComponent={
          !loading ? (
            <View style={styles.empty}>
              <Text style={styles.emptyGlyph}>🜂</Text>
              <Text style={styles.emptyTitle}>长卷尚空</Text>
              <Text style={styles.emptyText}>导入一张角色卡，开启你的第一段酒馆密谈。</Text>
            </View>
          ) : null
        }
      />

      <Pressable
        onPress={() => router.push("/(app)/new-chat")}
        style={({ pressed }) => [styles.fab, { bottom: insets.bottom + DOCK_HEIGHT + theme.space(3) }, pressed && { transform: [{ scale: 0.96 }] }]}
      >
        <Text style={styles.fabGlyph}>＋</Text>
        <Text style={styles.fabLabel}>新对话</Text>
      </Pressable>
      {promptNode}
      <GrimoireDock />
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "flex-end", paddingHorizontal: theme.space(6), paddingBottom: theme.space(4) },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  iconBtn: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.surfaceLine, marginLeft: theme.space(2) },
  iconBtnActive: { borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  iconGlyph: { fontSize: 20, color: theme.color.textDim },
  row: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(6), paddingVertical: theme.space(4), gap: theme.space(4) },
  avatarWrap: { width: 56, height: 56 },
  avatar: { width: 56, height: 56, borderRadius: theme.radius.md },
  avatarFallback: { backgroundColor: theme.color.bgInput, alignItems: "center", justifyContent: "center" },
  avatarInitial: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.accent },
  avatarRing: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLineStrong },
  rowBody: { flex: 1, gap: theme.space(1) },
  rowTitle: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  rowSnippet: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 19 },
  rowIndex: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.surfaceLineStrong },
  sep: { height: 1, backgroundColor: theme.color.surfaceLine, marginLeft: theme.space(6) + 56 + theme.space(4), marginRight: theme.space(6) },
  empty: { alignItems: "center", paddingTop: theme.space(28), paddingHorizontal: theme.space(10), gap: theme.space(3) },
  emptyGlyph: { fontSize: 44, color: theme.color.accent, opacity: 0.6 },
  emptyTitle: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.textDim },
  emptyText: { fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.textFaint, textAlign: "center", lineHeight: 22 },
  fab: {
    position: "absolute",
    right: theme.space(6),
    flexDirection: "row",
    alignItems: "center",
    gap: theme.space(2),
    backgroundColor: theme.color.accent,
    paddingHorizontal: theme.space(5),
    height: 54,
    borderRadius: theme.radius.pill,
    shadowColor: theme.color.accent,
    shadowOpacity: 0.5,
    shadowRadius: 18,
    shadowOffset: { width: 0, height: 6 },
    elevation: 10,
  },
  fabGlyph: { fontSize: 24, color: theme.color.bg, marginTop: -2 },
  fabLabel: { fontFamily: theme.font.displaySemi, fontSize: theme.size.sm, letterSpacing: 1, color: theme.color.bg, textTransform: "uppercase" },
});
