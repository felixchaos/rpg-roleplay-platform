import React, { useCallback, useState } from "react";
import { Alert, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import { Image } from "expo-image";
import { useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import Animated, { FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { GrimoireDock, DOCK_HEIGHT } from "@/components/GrimoireDock";
import { scripts, saves, ScriptSummary, SaveSummary } from "@/api";
import { baseUrl, ApiError } from "@/api/http";
import { usePrompt } from "@/components/PromptDialog";
import { theme } from "@/theme/theme";

type Mode = "scripts" | "saves";

export default function ConsoleScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { prompt, promptNode } = usePrompt();
  const [mode, setMode] = useState<Mode>("saves");
  const [scriptList, setScriptList] = useState<ScriptSummary[]>([]);
  const [saveList, setSaveList] = useState<SaveSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [base, setBase] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setBase(await baseUrl());
      if (mode === "scripts") {
        const r = await scripts.list();
        setScriptList(r?.items ?? []);
      } else {
        const r = await saves.list();
        setSaveList(r?.items ?? []);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, [mode]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const openSave = async (save: SaveSummary) => {
    try {
      await saves.activate(save.id);
      router.push({ pathname: "/(app)/chat/[id]", params: { id: String(save.id), title: save.title } });
    } catch (e) {
      Alert.alert("无法加载", e instanceof ApiError ? e.message : "请重试");
    }
  };

  const startFromScript = async (script: ScriptSummary) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    Alert.alert(script.title, "以此剧本开启新游戏？", [
      { text: "取消", style: "cancel" },
      {
        text: "开始",
        onPress: async () => {
          try {
            const r = await saves.newGame({ script_id: script.id });
            const sid = r?.state?.save_id ?? r?.state?.id;
            if (sid) await saves.activate(Number(sid));
            router.push({ pathname: "/(app)/chat/[id]", params: { id: String(sid ?? ""), title: script.title } });
          } catch (e) {
            Alert.alert("创建失败", e instanceof ApiError ? e.message : "请重试");
          }
        },
      },
    ]);
  };

  const saveActions = (save: SaveSummary) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    Alert.alert(save.title || "存档", "选择操作", [
      {
        text: "重命名",
        onPress: () =>
          prompt({
            title: "重命名存档",
            initialValue: save.title || "",
            placeholder: "新的名字",
            onConfirm: async (t) => { await saves.rename(save.id, t); load(); },
          }),
      },
      {
        text: "删除",
        style: "destructive",
        onPress: () =>
          Alert.alert("删除存档", "此存档及其分支将被永久删除，不可恢复。确定？", [
            { text: "取消", style: "cancel" },
            { text: "删除", style: "destructive", onPress: async () => { try { await saves.remove(save.id); load(); } catch (e) { Alert.alert("删除失败", e instanceof ApiError ? e.message : "请重试"); } } },
          ]),
      },
      { text: "取消", style: "cancel" },
    ]);
  };

  const scriptActions = (script: ScriptSummary) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    Alert.alert(script.title, "选择操作", [
      { text: "阅读典籍", onPress: () => router.push({ pathname: "/(app)/script/[id]", params: { id: String(script.id), title: script.title } }) },
      { text: "开启新游戏", onPress: () => startFromScript(script) },
      {
        text: script.is_subscribed ? "取消订阅" : "删除剧本",
        style: "destructive",
        onPress: () =>
          Alert.alert(script.is_subscribed ? "取消订阅" : "删除剧本", script.is_subscribed ? "将从你的书架移除此订阅。" : "此剧本及其全部数据将被永久删除，不可恢复。确定？", [
            { text: "取消", style: "cancel" },
            { text: "确定", style: "destructive", onPress: async () => { try { await scripts.remove(script.id, !!script.is_subscribed); load(); } catch (e) { Alert.alert("操作失败", e instanceof ApiError ? e.message : "请重试"); } } },
          ]),
      },
      { text: "取消", style: "cancel" },
    ]);
  };

  const renderScript = ({ item, index }: { item: ScriptSummary; index: number }) => {
    const cover = item.cover_url ? (item.cover_url.startsWith("http") ? item.cover_url : base + item.cover_url) : null;
    return (
      <Animated.View entering={FadeInDown.delay(Math.min(index, 8) * 55).duration(420).springify().damping(18)}>
        <Pressable onPress={() => startFromScript(item)} onLongPress={() => scriptActions(item)} style={({ pressed }) => [styles.scriptRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
          <View style={styles.cover}>
            {cover ? (
              <Image source={{ uri: cover }} style={styles.coverImg} contentFit="cover" transition={200} />
            ) : (
              <View style={[styles.coverImg, styles.coverFallback]}>
                <Text style={styles.coverGlyph}>❦</Text>
              </View>
            )}
          </View>
          <View style={{ flex: 1, gap: theme.space(1) }}>
            <Text style={styles.scriptTitle} numberOfLines={2}>{item.title}</Text>
            <Text style={styles.meta}>
              {item.chapter_count ? `${item.chapter_count} 章` : ""}
              {item.word_count ? `  ·  ${(item.word_count / 10000).toFixed(1)} 万字` : ""}
            </Text>
          </View>
          <Text style={styles.playGlyph}>▷</Text>
        </Pressable>
      </Animated.View>
    );
  };

  const renderSave = ({ item, index }: { item: SaveSummary; index: number }) => (
    <Animated.View entering={FadeInDown.delay(Math.min(index, 8) * 55).duration(420).springify().damping(18)}>
      <Pressable onPress={() => openSave(item)} onLongPress={() => saveActions(item)} style={({ pressed }) => [styles.saveRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
        <View style={{ flex: 1, gap: theme.space(1) }}>
          <Text style={styles.scriptTitle} numberOfLines={1}>{item.title || "未命名存档"}</Text>
          <Text style={styles.meta}>
            {item.player_name ? `${item.player_name}` : "旅人"}
            {item.turn != null ? `  ·  第 ${item.turn} 回合` : ""}
            {item.world_time ? `  ·  ${item.world_time}` : ""}
          </Text>
        </View>
        <Text style={styles.playGlyph}>▷</Text>
      </Pressable>
    </Animated.View>
  );

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Game Console</Text>
          <Text style={styles.h1}>剧本世界</Text>
        </View>
      </View>

      <View style={styles.seg}>
        {(["saves", "scripts"] as Mode[]).map((m) => (
          <Pressable key={m} onPress={() => setMode(m)} style={[styles.segItem, mode === m && styles.segActive]}>
            <Text style={[styles.segText, mode === m && styles.segTextActive]}>
              {m === "saves" ? "存档" : "剧本"}
            </Text>
          </Pressable>
        ))}
      </View>

      {mode === "scripts" ? (
        <FlatList
          data={scriptList}
          keyExtractor={(s) => String(s.id)}
          renderItem={renderScript}
          contentContainerStyle={[styles.listPad, { paddingBottom: insets.bottom + DOCK_HEIGHT + 30 }]}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={theme.color.accent} colors={[theme.color.accent]} />}
          ListEmptyComponent={!loading ? <EmptyState glyph="❦" title="书架尚空" text="在网页端导入小说剧本，它便会出现在这里。" /> : null}
        />
      ) : (
        <FlatList
          data={saveList}
          keyExtractor={(s) => String(s.id)}
          renderItem={renderSave}
          contentContainerStyle={[styles.listPad, { paddingBottom: insets.bottom + DOCK_HEIGHT + 30 }]}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={theme.color.accent} colors={[theme.color.accent]} />}
          ListEmptyComponent={!loading ? <EmptyState glyph="🜂" title="尚无存档" text="切到「剧本」标签，选一部作品开启你的冒险。" /> : null}
        />
      )}
      {promptNode}
      <GrimoireDock />
    </GrimoireBackdrop>
  );
}

function EmptyState({ glyph, title, text }: { glyph: string; title: string; text: string }) {
  return (
    <View style={styles.empty}>
      <Text style={styles.emptyGlyph}>{glyph}</Text>
      <Text style={styles.emptyTitle}>{title}</Text>
      <Text style={styles.emptyText}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  seg: { flexDirection: "row", marginHorizontal: theme.space(6), marginVertical: theme.space(3), backgroundColor: theme.color.bgInput, borderRadius: theme.radius.md, padding: 3, borderWidth: 1, borderColor: theme.color.surfaceLine },
  segItem: { flex: 1, paddingVertical: theme.space(2.5), alignItems: "center", borderRadius: theme.radius.sm },
  segActive: { backgroundColor: theme.color.accentGhost },
  segText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.sm, letterSpacing: 1.5, color: theme.color.textFaint },
  segTextActive: { color: theme.color.accentBright },
  listPad: { paddingHorizontal: theme.space(6), paddingBottom: 60, gap: theme.space(3) },
  scriptRow: { flexDirection: "row", alignItems: "center", gap: theme.space(4), paddingVertical: theme.space(3) },
  saveRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), paddingVertical: theme.space(4), borderBottomWidth: 1, borderBottomColor: theme.color.surfaceLine },
  cover: { width: 54, height: 76, borderRadius: theme.radius.sm, overflow: "hidden", borderWidth: 1, borderColor: theme.color.surfaceLineStrong },
  coverImg: { width: 54, height: 76 },
  coverFallback: { backgroundColor: theme.color.bgInput, alignItems: "center", justifyContent: "center" },
  coverGlyph: { fontSize: 26, color: theme.color.accent, opacity: 0.6 },
  scriptTitle: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  meta: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint },
  playGlyph: { fontSize: 18, color: theme.color.accent },
  empty: { alignItems: "center", paddingTop: theme.space(28), paddingHorizontal: theme.space(10), gap: theme.space(3) },
  emptyGlyph: { fontSize: 44, color: theme.color.accent, opacity: 0.6 },
  emptyTitle: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.textDim },
  emptyText: { fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.textFaint, textAlign: "center", lineHeight: 22 },
});
