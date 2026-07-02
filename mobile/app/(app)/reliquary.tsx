/**
 * Reliquary — the vault of generated and uploaded media. Filterable by kind (everything /
 * covers / avatars / scene art), laid out as a dense relic grid. Tap a relic to enlarge it
 * full-screen; long-press to banish it. Assets are owner-scoped server-side; deletion is
 * confirm-gated since some relics may be bound to scripts or cards.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, FlatList, Modal, Pressable, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import { Image } from "expo-image";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import Animated, { FadeIn } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { library, LibraryAsset } from "@/api";
import { baseUrl, ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

const KINDS = [
  { id: "", label: "全部" },
  { id: "cover", label: "封面" },
  { id: "avatar", label: "头像" },
  { id: "chat", label: "场景" },
];

export default function ReliquaryScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();
  const [kind, setKind] = useState("");
  const [assets, setAssets] = useState<LibraryAsset[]>([]);
  const [loading, setLoading] = useState(false);
  const [base, setBase] = useState("");
  const [lightbox, setLightbox] = useState<string | null>(null);

  const col = 3;
  const gap = theme.space(2);
  const cell = (width - theme.space(6) * 2 - gap * (col - 1)) / col;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setBase(await baseUrl());
      const r = await library.list(kind || undefined);
      setAssets((r?.items ?? []).filter((a) => a.url));
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, [kind]);

  useEffect(() => {
    load();
  }, [load]);

  const resolve = (u?: string) => (!u ? "" : u.startsWith("http") ? u : base + u);

  const banish = (asset: LibraryAsset) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    Alert.alert("销毁此物", "确定从图库移除？若它正被剧本或角色卡引用，删除可能失败。", [
      { text: "取消", style: "cancel" },
      {
        text: "销毁",
        style: "destructive",
        onPress: async () => {
          try {
            await library.remove(asset.id);
            load();
          } catch (e) {
            Alert.alert("无法删除", e instanceof ApiError ? e.message : "请重试");
          }
        },
      },
    ]);
  };

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Reliquary</Text>
          <Text style={styles.h1}>图库</Text>
        </View>
      </View>

      <View style={styles.tabs}>
        {KINDS.map((k) => (
          <Pressable key={k.id} onPress={() => setKind(k.id)} style={[styles.tab, kind === k.id && styles.tabActive]}>
            <Text style={[styles.tabText, kind === k.id && styles.tabTextActive]}>{k.label}</Text>
          </Pressable>
        ))}
      </View>

      <FlatList
        data={assets}
        key={col}
        numColumns={col}
        keyExtractor={(a) => String(a.id)}
        columnWrapperStyle={{ gap }}
        contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 40, gap }}
        renderItem={({ item, index }) => {
          const uri = resolve(item.url);
          return (
            <Animated.View entering={FadeIn.delay(Math.min(index, 12) * 30).duration(300)}>
              <Pressable onPress={() => setLightbox(uri)} onLongPress={() => banish(item)} style={[styles.cell, { width: cell, height: cell }]}>
                <Image source={{ uri }} style={styles.cellImg} contentFit="cover" transition={200} />
              </Pressable>
            </Animated.View>
          );
        }}
        ListEmptyComponent={
          loading ? (
            <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
          ) : (
            <View style={styles.empty}>
              <Text style={styles.emptyGlyph}>◈</Text>
              <Text style={styles.emptyText}>此类暂无藏品。生成的图像与上传的素材会聚于此。</Text>
            </View>
          )
        }
      />

      <Modal visible={!!lightbox} transparent animationType="fade" onRequestClose={() => setLightbox(null)}>
        <Pressable style={styles.lightbox} onPress={() => setLightbox(null)}>
          {lightbox ? <Image source={{ uri: lightbox }} style={styles.lightboxImg} contentFit="contain" transition={200} /> : null}
        </Pressable>
      </Modal>
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  tabs: { flexDirection: "row", gap: theme.space(2), marginHorizontal: theme.space(6), marginBottom: theme.space(3) },
  tab: { flex: 1, paddingVertical: theme.space(2), alignItems: "center", borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine },
  tabActive: { backgroundColor: theme.color.accentGhost, borderColor: theme.color.accentSoft },
  tabText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1, color: theme.color.textFaint },
  tabTextActive: { color: theme.color.accentBright },
  cell: { borderRadius: theme.radius.md, overflow: "hidden", borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  cellImg: { width: "100%", height: "100%" },
  empty: { alignItems: "center", paddingTop: theme.space(24), paddingHorizontal: theme.space(10), gap: theme.space(3) },
  emptyGlyph: { fontSize: 44, color: theme.color.accent, opacity: 0.6 },
  emptyText: { fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.textFaint, textAlign: "center", lineHeight: 23 },
  lightbox: { flex: 1, backgroundColor: "rgba(5,4,3,0.95)", alignItems: "center", justifyContent: "center", padding: theme.space(4) },
  lightboxImg: { width: "100%", height: "100%" },
});
