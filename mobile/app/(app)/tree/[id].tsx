/**
 * Story Tree — the branch view for a save. The platform's headline feature: git-style
 * branchable saves. We render commits as a vertical timeline thread; the active node
 * glows ember, past turns sit dim. Tap a node to jump there (activate) or fork a new
 * branch from it (continue). This is what makes the engine more than a chat log.
 */
import React, { useCallback, useState } from "react";
import { ActivityIndicator, Alert, FlatList, Pressable, StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import Animated, { FadeIn } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { branches, BranchNode } from "@/api";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

export default function TreeScreen() {
  const { id, title } = useLocalSearchParams<{ id: string; title?: string }>();
  const saveId = Number(id);
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [nodes, setNodes] = useState<BranchNode[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await branches.list(saveId);
      setNodes(r?.nodes ?? r?.commits ?? []);
      setActiveId(r?.active_commit_id ?? null);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, [saveId]);

  React.useEffect(() => {
    load();
  }, [load]);

  const onNode = (node: BranchNode) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    const nid = node.node_id ?? node.commit_id ?? node.id;
    const active = node.is_active || nid === activeId;
    Alert.alert(
      `第 ${node.turn ?? "?"} 回合`,
      active ? "你正身处此节点。" : "回到这条时间线，或从此处另辟新支？",
      [
        { text: "取消", style: "cancel" },
        ...(active
          ? []
          : [
              {
                text: "跳转至此",
                onPress: async () => {
                  try {
                    await branches.activate(nid);
                    router.replace({ pathname: "/(app)/chat/[id]", params: { id: String(saveId), title: title || "对话" } });
                  } catch (e) {
                    Alert.alert("跳转失败", e instanceof ApiError ? e.message : "请重试");
                  }
                },
              },
            ]),
        {
          text: "从此分支",
          onPress: async () => {
            try {
              const r = await branches.continueFrom(nid);
              const sid = r?.save_id ?? saveId;
              router.replace({ pathname: "/(app)/chat/[id]", params: { id: String(sid), title: title || "对话" } });
            } catch (e) {
              Alert.alert("分支失败", e instanceof ApiError ? e.message : "请重试");
            }
          },
        },
      ],
    );
  };

  const renderNode = ({ item, index }: { item: BranchNode; index: number }) => {
    const nid = item.node_id ?? item.commit_id ?? item.id;
    const active = item.is_active || nid === activeId;
    const isLast = index === nodes.length - 1;
    const player = (item.player_input || "").trim();
    const gm = (item.gm_output || item.content_preview || item.summary || "").trim();
    return (
      <Animated.View entering={FadeIn.delay(Math.min(index, 12) * 40).duration(360)}>
        <Pressable onPress={() => onNode(item)} style={styles.nodeRow}>
          {/* thread + node marker */}
          <View style={styles.rail}>
            <View style={[styles.railLine, index === 0 && { backgroundColor: "transparent" }]} />
            <View style={[styles.dot, active && styles.dotActive]}>
              {active ? <View style={styles.dotCore} /> : null}
            </View>
            <View style={[styles.railLine, isLast && { backgroundColor: "transparent" }]} />
          </View>

          <View style={[styles.card, active && styles.cardActive]}>
            <View style={styles.cardHead}>
              <Text style={[styles.turn, active && { color: theme.color.accentBright }]}>
                回合 {item.turn ?? index + 1}
              </Text>
              {item.ref_names && item.ref_names.length > 0 ? (
                <View style={styles.refChip}>
                  <Text style={styles.refText}>{item.ref_names[0]}</Text>
                </View>
              ) : null}
              {active ? <Text style={styles.hereTag}>当前</Text> : null}
            </View>
            {player ? (
              <Text style={styles.player} numberOfLines={2}>
                ❯ {player}
              </Text>
            ) : null}
            {gm ? (
              <Text style={styles.gm} numberOfLines={3}>
                {gm}
              </Text>
            ) : null}
          </View>
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
          <Text style={styles.kicker}>Story Tree</Text>
          <Text style={styles.h1} numberOfLines={1}>{title || "时间线"}</Text>
        </View>
        <Pressable onPress={() => router.push({ pathname: "/(app)/worldline/[id]", params: { id: String(saveId), title: title || "世界线" } })} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGearGlyph}>🜍</Text>
        </Pressable>
        <Pressable onPress={() => router.push({ pathname: "/(app)/save-settings", params: { id: String(saveId), title: title || "存档设置" } })} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGearGlyph}>⚙</Text>
        </Pressable>
      </View>

      {loading && nodes.length === 0 ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <FlatList
          data={nodes}
          keyExtractor={(n) => String(n.node_id ?? n.commit_id ?? n.id)}
          renderItem={renderNode}
          contentContainerStyle={{ paddingHorizontal: theme.space(5), paddingBottom: insets.bottom + 40, paddingTop: theme.space(2) }}
          ListEmptyComponent={
            !loading ? (
              <View style={styles.empty}>
                <Text style={styles.emptyGlyph}>⌥</Text>
                <Text style={styles.emptyText}>这段故事还没有分叉。每一次对话都会在此留下年轮。</Text>
              </View>
            ) : null
          }
        />
      )}
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  headGearGlyph: { fontSize: 19, color: theme.color.textDim },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  nodeRow: { flexDirection: "row", gap: theme.space(3) },
  rail: { width: 24, alignItems: "center" },
  railLine: { flex: 1, width: 2, backgroundColor: theme.color.surfaceLineStrong },
  dot: { width: 14, height: 14, borderRadius: 7, borderWidth: 2, borderColor: theme.color.surfaceLineStrong, backgroundColor: theme.color.bg, alignItems: "center", justifyContent: "center" },
  dotActive: { borderColor: theme.color.accent, shadowColor: theme.color.accent, shadowOpacity: 0.8, shadowRadius: 8, elevation: 6 },
  dotCore: { width: 6, height: 6, borderRadius: 3, backgroundColor: theme.color.accentBright },
  card: { flex: 1, marginVertical: theme.space(2), padding: theme.space(4), borderRadius: theme.radius.lg, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard, gap: theme.space(2) },
  cardActive: { borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  cardHead: { flexDirection: "row", alignItems: "center", gap: theme.space(2) },
  turn: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1.5, textTransform: "uppercase", color: theme.color.textFaint },
  refChip: { backgroundColor: theme.color.magicSoft, paddingHorizontal: theme.space(2), paddingVertical: 2, borderRadius: theme.radius.sm },
  refText: { fontFamily: theme.font.mono, fontSize: 10, color: theme.color.magic },
  hereTag: { marginLeft: "auto", fontFamily: theme.font.displaySemi, fontSize: 10, letterSpacing: 1, color: theme.color.accentBright, textTransform: "uppercase" },
  player: { fontFamily: theme.font.proseMedium, fontSize: theme.size.sm, color: theme.color.accentBright, lineHeight: 20 },
  gm: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim, lineHeight: 21 },
  empty: { alignItems: "center", paddingTop: theme.space(28), paddingHorizontal: theme.space(10), gap: theme.space(3) },
  emptyGlyph: { fontSize: 44, color: theme.color.accent, opacity: 0.6 },
  emptyText: { fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.textFaint, textAlign: "center", lineHeight: 23 },
});
