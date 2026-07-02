import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { GrimoireDock, DOCK_HEIGHT } from "@/components/GrimoireDock";
import { FeedbackDrawer } from "@/components/FeedbackDrawer";
import { EmberButton, RuneDivider } from "@/components/ui";
import { settings, ProviderInfo } from "@/api";
import { ApiError } from "@/api/http";
import { useAuth } from "@/state/auth";
import { theme } from "@/theme/theme";

export default function SettingsScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, serverUrl, logout, setServer } = useAuth();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [selected, setSelected] = useState<{ api_id?: string; model_id?: string }>({});
  const [loading, setLoading] = useState(true);
  const [keyApi, setKeyApi] = useState<string | null>(null);
  const [keyValue, setKeyValue] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const [cost30, setCost30] = useState<number | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const m = await settings.models();
      const providerList: ProviderInfo[] = m?.models?.apis ?? [];
      // Catalog provider objects key their id as `id`, not `api_id` (only the top-level
      // wrapper uses api_id). Normalize so every downstream read of p.api_id works.
      for (const p of providerList) {
        if (!p.api_id) p.api_id = (p as any).id;
      }
      // Overlay the authoritative /me/credentials list by api_id so a key added on the
      // web shows as configured here too.
      try {
        const c = await settings.credentials();
        const credMap = new Map((c?.items ?? []).map((it: any) => [it.api_id, it]));
        for (const p of providerList) {
          const cred = credMap.get(p.api_id);
          if (cred) {
            p.has_credential = !!cred.has_credential || !!p.has_credential;
            if (cred.enabled != null) p.enabled = cred.enabled;
          }
        }
      } catch {
        /* fall back to catalog-only has_credential */
      }
      setProviders(providerList);
      setSelected({ api_id: m?.selected?.api_id, model_id: m?.selected?.model_id });
      const u = await settings.usage(30).catch(() => null);
      if (u?.total_cost_usd != null) setCost30(u.total_cost_usd);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const pickModel = async (api_id: string, model_id: string) => {
    setSelected({ api_id, model_id });
    try {
      await settings.selectModel(api_id, model_id);
    } catch (e) {
      Alert.alert("切换失败", e instanceof ApiError ? e.message : "请重试");
      load();
    }
  };

  const saveKey = async (api_id: string) => {
    if (!keyValue.trim()) return;
    setSavingKey(true);
    try {
      await settings.setCredential({ api_id, api_key: keyValue.trim(), enabled: true });
      setKeyApi(null);
      setKeyValue("");
      Alert.alert("已保存", "密钥已加密存储。");
      load();
    } catch (e) {
      Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setSavingKey(false);
    }
  };

  const testKey = async (api_id: string) => {
    try {
      const r = await settings.testCredential(api_id);
      Alert.alert(r.ok ? "连接正常" : "连接失败", r.ok ? `延迟 ${r.latency_ms ?? "?"}ms` : r.error || "未知错误");
    } catch (e) {
      Alert.alert("测试失败", e instanceof ApiError ? e.message : "请重试");
    }
  };

  const onLogout = () => {
    Alert.alert("退出登录", "确定要退出当前账号吗？", [
      { text: "取消", style: "cancel" },
      {
        text: "退出",
        style: "destructive",
        onPress: async () => {
          await logout();
          router.replace("/(auth)/login");
        },
      },
    ]);
  };

  const switchServer = () => {
    Alert.alert("切换实例", "这将登出当前账号并返回服务器选择。确定？", [
      { text: "取消", style: "cancel" },
      {
        text: "切换",
        onPress: async () => {
          await logout();
          await setServer("");
          router.replace("/(auth)/server");
        },
      },
    ]);
  };

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Me</Text>
          <Text style={styles.h1}>我的</Text>
        </View>
      </View>

      <ScrollView contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + DOCK_HEIGHT + 30, gap: theme.space(5) }}>
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>账号</Text>
          <Text style={styles.bigName}>{user?.display_name || user?.username || "—"}</Text>
          <Pressable onPress={switchServer} hitSlop={6} style={styles.serverRow}>
            <Text style={styles.mono} numberOfLines={1}>{serverUrl}</Text>
            <Text style={styles.switchTag}>切换实例</Text>
          </Pressable>
          {cost30 != null ? <Text style={styles.usage}>近 30 天花费 ${cost30.toFixed(3)}</Text> : null}
          <View style={styles.navRows}>
            <Pressable onPress={() => router.push("/(app)/profile")} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
              <Text style={styles.navGlyph}>𓂀</Text>
              <Text style={styles.navLabel}>游侠纪 · 成就与统计</Text>
              <Text style={styles.navChevron}>›</Text>
            </Pressable>
            <Pressable onPress={() => router.push("/(app)/personas")} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
              <Text style={styles.navGlyph}>❦</Text>
              <Text style={styles.navLabel}>我的身份 · Personas</Text>
              <Text style={styles.navChevron}>›</Text>
            </Pressable>
            <Pressable onPress={() => router.push("/(app)/distillery")} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
              <Text style={styles.navGlyph}>⚗</Text>
              <Text style={styles.navLabel}>蒸馏所 · 人格技能</Text>
              <Text style={styles.navChevron}>›</Text>
            </Pressable>
            <Pressable onPress={() => router.push("/(app)/gm-style")} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
              <Text style={styles.navGlyph}>⚝</Text>
              <Text style={styles.navLabel}>叙事调律 · GM 风格</Text>
              <Text style={styles.navChevron}>›</Text>
            </Pressable>
            <Pressable onPress={() => router.push("/(app)/memory-settings")} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
              <Text style={styles.navGlyph}>⌬</Text>
              <Text style={styles.navLabel}>记忆调律 · Memory</Text>
              <Text style={styles.navChevron}>›</Text>
            </Pressable>
            <Pressable onPress={() => router.push("/(app)/modules")} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
              <Text style={styles.navGlyph}>♛</Text>
              <Text style={styles.navLabel}>众手议会 · 模块模型</Text>
              <Text style={styles.navChevron}>›</Text>
            </Pressable>
            <Pressable onPress={() => router.push("/(app)/model-params")} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
              <Text style={styles.navGlyph}>✺</Text>
              <Text style={styles.navLabel}>炼金参数 · Sampling</Text>
              <Text style={styles.navChevron}>›</Text>
            </Pressable>
            <Pressable onPress={() => router.push("/(app)/aviary")} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
              <Text style={styles.navGlyph}>𓅓</Text>
              <Text style={styles.navLabel}>鸟巢 · 模型管理</Text>
              <Text style={styles.navChevron}>›</Text>
            </Pressable>
            <Pressable onPress={() => router.push("/(app)/preferences")} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
              <Text style={styles.navGlyph}>⚙</Text>
              <Text style={styles.navLabel}>偏好 · Attunements</Text>
              <Text style={styles.navChevron}>›</Text>
            </Pressable>
            <Pressable onPress={() => router.push("/(app)/account")} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
              <Text style={styles.navGlyph}>⛨</Text>
              <Text style={styles.navLabel}>名号与了断 · 账号</Text>
              <Text style={styles.navChevron}>›</Text>
            </Pressable>
            <Pressable onPress={() => router.push("/(app)/reliquary")} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
              <Text style={styles.navGlyph}>◈</Text>
              <Text style={styles.navLabel}>图库 · Reliquary</Text>
              <Text style={styles.navChevron}>›</Text>
            </Pressable>
            <Pressable onPress={() => router.push("/(app)/advanced")} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
              <Text style={styles.navGlyph}>⌬</Text>
              <Text style={styles.navLabel}>高级 · 参数与装置</Text>
              <Text style={styles.navChevron}>›</Text>
            </Pressable>
          </View>
        </View>

        {loading ? (
          <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(10) }} />
        ) : (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>模型与密钥</Text>
            {providers.map((p) => {
              const editing = keyApi === p.api_id;
              return (
                <View key={p.api_id} style={styles.provider}>
                  <View style={styles.providerHead}>
                    <Text style={styles.providerName}>{p.display_name || p.api_id}</Text>
                    <View style={[styles.dot, { backgroundColor: p.has_credential ? theme.color.success : theme.color.textFaint }]} />
                  </View>

                  {(p.models || []).slice(0, 8).map((m) => {
                    const active = selected.api_id === p.api_id && selected.model_id === m.id;
                    return (
                      <Pressable key={m.id} onPress={() => pickModel(p.api_id, m.id)} style={[styles.modelRow, active && styles.modelRowActive]}>
                        <Text style={[styles.modelName, active && { color: theme.color.accentBright }]} numberOfLines={1}>
                          {m.name || m.id}
                        </Text>
                        {active ? <Text style={styles.check}>✦</Text> : null}
                      </Pressable>
                    );
                  })}

                  {editing ? (
                    <View style={{ gap: theme.space(2), marginTop: theme.space(2) }}>
                      <TextInput
                        value={keyValue}
                        onChangeText={setKeyValue}
                        placeholder="粘贴 API Key"
                        placeholderTextColor={theme.color.textFaint}
                        secureTextEntry
                        autoCapitalize="none"
                        style={styles.keyInput}
                      />
                      <View style={{ flexDirection: "row", gap: theme.space(2) }}>
                        <EmberButton label="保存" onPress={() => saveKey(p.api_id)} loading={savingKey} style={{ flex: 1 }} />
                        <EmberButton label="取消" variant="ghost" onPress={() => { setKeyApi(null); setKeyValue(""); }} style={{ flex: 1 }} />
                      </View>
                    </View>
                  ) : (
                    <View style={styles.keyActions}>
                      <Pressable onPress={() => { setKeyApi(p.api_id); setKeyValue(""); }} hitSlop={8}>
                        <Text style={styles.keyAction}>{p.has_credential ? "更换密钥" : "添加密钥"}</Text>
                      </Pressable>
                      {p.has_credential ? (
                        <Pressable onPress={() => testKey(p.api_id)} hitSlop={8}>
                          <Text style={styles.keyAction}>测试</Text>
                        </Pressable>
                      ) : null}
                    </View>
                  )}
                </View>
              );
            })}
          </View>
        )}

        <RuneDivider />
        <Pressable onPress={() => router.push("/(app)/help")} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
          <Text style={styles.navGlyph}>❡</Text>
          <Text style={styles.navLabel}>帮助 · Grimoire Index</Text>
          <Text style={styles.navChevron}>›</Text>
        </Pressable>
        <Pressable onPress={() => setFeedbackOpen(true)} style={({ pressed }) => [styles.navRow, pressed && { backgroundColor: theme.color.bgElevated }]}>
          <Text style={styles.navGlyph}>✉</Text>
          <Text style={styles.navLabel}>寄出反馈</Text>
          <Text style={styles.navChevron}>›</Text>
        </Pressable>
        <EmberButton label="退出登录" variant="ghost" onPress={onLogout} />
      </ScrollView>

      <FeedbackDrawer visible={feedbackOpen} onClose={() => setFeedbackOpen(false)} />
      <GrimoireDock />
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), borderBottomWidth: 1, borderBottomColor: theme.color.surfaceLine },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  headTitle: { flex: 1, textAlign: "center", fontFamily: theme.font.displaySemi, fontSize: theme.size.md, color: theme.color.text, letterSpacing: 1 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  section: { gap: theme.space(2), paddingTop: theme.space(4) },
  sectionTitle: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 3, textTransform: "uppercase", color: theme.color.accent, marginBottom: theme.space(2) },
  bigName: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text },
  mono: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.textFaint },
  serverRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: theme.space(3), marginTop: theme.space(1) },
  switchTag: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1, textTransform: "uppercase", color: theme.color.accent },
  usage: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim, marginTop: theme.space(1) },
  navRows: { marginTop: theme.space(3), gap: theme.space(2) },
  navRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), paddingVertical: theme.space(3), paddingHorizontal: theme.space(3), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  navGlyph: { fontSize: 18, color: theme.color.accent, width: 24, textAlign: "center" },
  navLabel: { flex: 1, fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.text },
  navChevron: { fontSize: 22, color: theme.color.textFaint },
  provider: { borderWidth: 1, borderColor: theme.color.surfaceLine, borderRadius: theme.radius.md, padding: theme.space(4), gap: theme.space(2), marginBottom: theme.space(3), backgroundColor: theme.color.bgCard },
  providerHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  providerName: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  dot: { width: 8, height: 8, borderRadius: 4 },
  modelRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: theme.space(2), paddingHorizontal: theme.space(2), borderRadius: theme.radius.sm },
  modelRowActive: { backgroundColor: theme.color.accentGhost },
  modelName: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.textDim, flex: 1 },
  check: { color: theme.color.accentBright, fontSize: theme.size.md },
  keyActions: { flexDirection: "row", gap: theme.space(5), marginTop: theme.space(2) },
  keyAction: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.accent },
  keyInput: { backgroundColor: theme.color.bgInput, borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine, paddingHorizontal: theme.space(3), paddingVertical: theme.space(3), color: theme.color.text, fontFamily: theme.font.mono, fontSize: theme.size.sm },
});
