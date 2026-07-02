// useEffect (not useFocusEffect) is deliberate: the URL field pre-fills from storage
// only on first mount so that back-navigation doesn't overwrite user edits.
import React, { useEffect, useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { EmberButton, Field, RuneDivider } from "@/components/ui";
import { useAuth } from "@/state/auth";
import { getServerUrl, normalizeBaseUrl } from "@/api/storage";
import { theme } from "@/theme/theme";

export default function ServerScreen() {
  const { setServer } = useAuth();
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const insets = useSafeAreaInsets();

  useEffect(() => {
    getServerUrl().then((u) => u && setUrl(u));
  }, []);

  const onContinue = async () => {
    setErr(null);
    const normalized = normalizeBaseUrl(url);
    if (!normalized) {
      setErr("请输入服务器地址");
      return;
    }
    setBusy(true);
    try {
      // Probe reachability so a typo'd host fails here, not three screens later.
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 8000);
      const res = await fetch(`${normalized}/api/v1/platform`, { signal: ctrl.signal }).catch(
        () => null,
      );
      clearTimeout(t);
      if (!res) {
        setErr("无法连接到该服务器，请检查地址与网络");
        setBusy(false);
        return;
      }
      await setServer(normalized);
      router.push("/(auth)/login");
    } finally {
      setBusy(false);
    }
  };

  return (
    <GrimoireBackdrop>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          contentContainerStyle={[
            styles.scroll,
            { paddingTop: insets.top + theme.space(20), paddingBottom: insets.bottom + 40 },
          ]}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={styles.kicker}>Bring Your Own Server</Text>
          <Text style={styles.title}>开启传送门</Text>
          <Text style={styles.sub}>
            连接你自部署的 RPG Roleplay 服务器。{"\n"}你的故事，运行在你自己的机器上。
          </Text>

          <View style={styles.card}>
            <Field
              label="服务器地址"
              value={url}
              onChangeText={setUrl}
              placeholder="http://192.168.1.10:7860"
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              inputMode="url"
              onSubmitEditing={onContinue}
            />
            {err ? <Text style={styles.err}>{err}</Text> : null}
            <RuneDivider />
            <EmberButton label="连接" onPress={onContinue} loading={busy} />
            <Pressable onPress={() => router.push("/(auth)/qr")} hitSlop={8} style={styles.qrRow}>
              <Text style={styles.qrGlyph}>⌖</Text>
              <Text style={styles.qrText}>扫描桌面二维码免密登入</Text>
            </Pressable>
          </View>

          <Text style={styles.hint}>
            提示：在桌面端「控制台 → LAN 共享」可查看本机地址与端口（默认 :7860）。
          </Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  scroll: { paddingHorizontal: theme.space(6), gap: theme.space(3) },
  kicker: {
    fontFamily: theme.font.displaySemi,
    fontSize: theme.size.xs,
    letterSpacing: 4,
    textTransform: "uppercase",
    color: theme.color.accent,
  },
  title: {
    fontFamily: theme.font.display,
    fontSize: theme.size.display,
    color: theme.color.text,
    letterSpacing: 1,
  },
  sub: {
    fontFamily: theme.font.prose,
    fontSize: theme.size.md,
    lineHeight: 24,
    color: theme.color.textDim,
    marginBottom: theme.space(4),
  },
  card: {
    backgroundColor: theme.color.bgCard,
    borderRadius: theme.radius.lg,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    padding: theme.space(5),
    gap: theme.space(4),
  },
  err: { fontFamily: theme.font.prose, color: theme.color.danger, fontSize: theme.size.sm },
  qrRow: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: theme.space(2), paddingVertical: theme.space(2) },
  qrGlyph: { fontSize: 18, color: theme.color.magic },
  qrText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.magicDim },
  hint: {
    fontFamily: theme.font.prose,
    fontSize: theme.size.sm,
    color: theme.color.textFaint,
    lineHeight: 20,
    marginTop: theme.space(4),
  },
});
