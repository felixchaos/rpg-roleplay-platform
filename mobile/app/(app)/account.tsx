/**
 * Sigil & Severance — account identity and the grave actions. The top half is benign:
 * revise your display name, export your data. The lower half is the Severance — a
 * visually-set-apart danger zone for deactivation and account deletion (a 30-day grace
 * window, cancellable). Destructive actions are styled in blood-tone and double-gated by
 * confirm dialogs, so nothing irreversible happens on a single tap.
 */
import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Linking, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { EmberButton, Field } from "@/components/ui";
import { account } from "@/api";
import { ApiError } from "@/api/http";
import { useAuth } from "@/state/auth";
import { theme } from "@/theme/theme";

export default function AccountScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, logout, refresh } = useAuth();
  const [displayName, setDisplayName] = useState(user?.display_name || "");
  const [savingName, setSavingName] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<{ pending: boolean; purge_at?: string | null } | null>(null);
  const [loading, setLoading] = useState(true);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    try {
      const r = await account.deleteStatus();
      setPendingDelete({ pending: !!r?.pending, purge_at: r?.purge_at });
    } catch {
      setPendingDelete({ pending: false });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const saveName = async () => {
    if (!displayName.trim()) return;
    setSavingName(true);
    try {
      await account.saveProfile({ display_name: displayName.trim() });
      await refresh();
      Alert.alert("已更新", "你的名号已铭刻。");
    } catch (e) {
      Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setSavingName(false);
    }
  };

  const exportData = async () => {
    const url = await account.exportUrl();
    Linking.openURL(url).catch(() => Alert.alert("无法打开", "请在浏览器中登录后下载。"));
  };

  const deactivate = () => {
    Alert.alert("停用账号", "停用后你将登出，重新登录即可恢复。确定停用？", [
      { text: "取消", style: "cancel" },
      {
        text: "停用",
        style: "destructive",
        onPress: async () => {
          try {
            await account.deactivate();
            await logout();
            router.replace("/(auth)/login");
          } catch (e) {
            Alert.alert("操作失败", e instanceof ApiError ? e.message : "请重试");
          }
        },
      },
    ]);
  };

  const requestDelete = () => {
    Alert.alert(
      "请求注销账号",
      "这将启动 30 天宽限期。期满后你的账号与全部数据将被永久抹除，不可恢复。期间可随时取消。",
      [
        { text: "取消", style: "cancel" },
        {
          text: "确认注销",
          style: "destructive",
          onPress: async () => {
            try {
              await account.requestDelete();
              loadStatus();
            } catch (e) {
              Alert.alert("操作失败", e instanceof ApiError ? e.message : "请重试");
            }
          },
        },
      ],
    );
  };

  const cancelDelete = async () => {
    try {
      await account.cancelDelete();
      loadStatus();
      Alert.alert("已撤回", "注销请求已取消，你的账号安然无恙。");
    } catch (e) {
      Alert.alert("操作失败", e instanceof ApiError ? e.message : "请重试");
    }
  };

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Account</Text>
          <Text style={styles.h1}>名号与了断</Text>
        </View>
      </View>

      <ScrollView contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 50, gap: theme.space(6), paddingTop: theme.space(3) }}>
        <Animated.View entering={FadeInDown.duration(360)} style={styles.section}>
          <Text style={styles.sectionTitle}>身份</Text>
          <Field label="显示名号" value={displayName} onChangeText={setDisplayName} placeholder="旅人之名" />
          <EmberButton label={savingName ? "铭刻中…" : "保存名号"} onPress={saveName} loading={savingName} />
        </Animated.View>

        <Animated.View entering={FadeInDown.delay(80).duration(360)} style={styles.section}>
          <Text style={styles.sectionTitle}>数据</Text>
          <Pressable onPress={exportData} style={({ pressed }) => [styles.dataRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
            <Text style={styles.dataGlyph}>⬇</Text>
            <View style={{ flex: 1 }}>
              <Text style={styles.dataLabel}>导出我的数据</Text>
              <Text style={styles.dataNote}>下载剧本 / 存档 / 角色卡 / 偏好的完整副本。</Text>
            </View>
          </Pressable>
        </Animated.View>

        {/* Severance — danger zone */}
        <Animated.View entering={FadeInDown.delay(160).duration(360)} style={styles.danger}>
          <Text style={styles.dangerTitle}>⚠ 危险地带</Text>

          {loading ? (
            <ActivityIndicator color={theme.color.danger} style={{ marginVertical: theme.space(4) }} />
          ) : pendingDelete?.pending ? (
            <View style={styles.pendingBox}>
              <Text style={styles.pendingText}>
                账号已进入注销宽限期{pendingDelete.purge_at ? `，将于 ${String(pendingDelete.purge_at).slice(0, 10)} 永久抹除` : ""}。
              </Text>
              <EmberButton label="撤回注销" onPress={cancelDelete} />
            </View>
          ) : (
            <>
              <Pressable onPress={deactivate} style={styles.dangerRow}>
                <Text style={styles.dangerLabel}>停用账号</Text>
                <Text style={styles.dangerNote}>临时登出，可随时恢复</Text>
              </Pressable>
              <Pressable onPress={requestDelete} style={[styles.dangerRow, styles.dangerRowFinal]}>
                <Text style={[styles.dangerLabel, { color: theme.color.danger }]}>注销账号</Text>
                <Text style={styles.dangerNote}>30 天宽限后永久抹除，不可恢复</Text>
              </Pressable>
            </>
          )}
        </Animated.View>
      </ScrollView>
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  section: { gap: theme.space(3) },
  sectionTitle: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 3, textTransform: "uppercase", color: theme.color.accent },
  dataRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), padding: theme.space(4), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  dataGlyph: { fontSize: 20, color: theme.color.accent, width: 24, textAlign: "center" },
  dataLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.text },
  dataNote: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 18, marginTop: 2 },
  danger: { borderWidth: 1, borderColor: "rgba(184,69,58,0.4)", borderRadius: theme.radius.lg, padding: theme.space(4), gap: theme.space(2), backgroundColor: "rgba(184,69,58,0.05)" },
  dangerTitle: { fontFamily: theme.font.displaySemi, fontSize: theme.size.sm, letterSpacing: 2, textTransform: "uppercase", color: theme.color.danger, marginBottom: theme.space(1) },
  dangerRow: { paddingVertical: theme.space(3), borderTopWidth: 1, borderTopColor: "rgba(184,69,58,0.2)" },
  dangerRowFinal: {},
  dangerLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  dangerNote: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, marginTop: 2 },
  pendingBox: { gap: theme.space(3), paddingVertical: theme.space(2) },
  pendingText: { fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.text, lineHeight: 22 },
});
