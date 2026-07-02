import React, { useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Link, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { EmberButton, Field, RuneDivider } from "@/components/ui";
import { useAuth } from "@/state/auth";
import { auth as authApi } from "@/api";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

type Mode = "password" | "otp";

export default function LoginScreen() {
  const { login, serverUrl, refresh } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("password");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const insets = useSafeAreaInsets();

  const onLogin = async () => {
    setErr(null);
    if (!username || !password) {
      setErr("请输入用户名与密码");
      return;
    }
    setBusy(true);
    try {
      await login(username.trim(), password);
      router.replace("/(app)/chats");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "登录失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  const onRequestCode = async () => {
    setErr(null);
    if (!email.trim()) {
      setErr("请输入邮箱");
      return;
    }
    setBusy(true);
    try {
      await authApi.loginCodeRequest(email.trim());
      setCodeSent(true);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "发送失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  const onVerifyCode = async () => {
    setErr(null);
    if (!code.trim()) {
      setErr("请输入收到的验证码");
      return;
    }
    setBusy(true);
    try {
      await authApi.loginCodeVerify(email.trim(), code.trim());
      await refresh();
      router.replace("/(app)/chats");  // refresh() has committed setUser synchronously; RouteGuard handles the redirect.
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "验证码无效或已过期");
    } finally {
      setBusy(false);
    }
  };

  return (
    <GrimoireBackdrop>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView
          contentContainerStyle={[styles.scroll, { paddingTop: insets.top + theme.space(18), paddingBottom: insets.bottom + 40 }]}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={styles.kicker}>欢迎归来，旅人</Text>
          <Text style={styles.title}>踏入酒馆</Text>
          <Text style={styles.server} numberOfLines={1}>
            {serverUrl}
          </Text>

          <View style={styles.seg}>
            {(["password", "otp"] as Mode[]).map((m) => (
              <Pressable key={m} onPress={() => { setMode(m); setErr(null); }} style={[styles.segItem, mode === m && styles.segActive]}>
                <Text style={[styles.segText, mode === m && styles.segTextActive]}>{m === "password" ? "密码登录" : "邮箱验证码"}</Text>
              </Pressable>
            ))}
          </View>

          {mode === "password" ? (
            <View style={styles.card}>
              <Field label="用户名" value={username} onChangeText={setUsername} autoCapitalize="none" autoCorrect={false} placeholder="your name" />
              <Field label="密码" value={password} onChangeText={setPassword} secureTextEntry placeholder="••••••••" onSubmitEditing={onLogin} />
              {err ? <Text style={styles.err}>{err}</Text> : null}
              <Link href="/(auth)/forgot" asChild>
                <Pressable hitSlop={8} style={{ alignSelf: "flex-end" }}>
                  <Text style={styles.forgot}>忘记密码？</Text>
                </Pressable>
              </Link>
              <RuneDivider />
              <EmberButton label="进入" onPress={onLogin} loading={busy} />
            </View>
          ) : (
            <View style={styles.card}>
              <Field label="邮箱" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="you@example.com" editable={!codeSent} />
              {codeSent ? (
                <Field label="验证码" value={code} onChangeText={setCode} keyboardType="number-pad" placeholder="6 位数字" onSubmitEditing={onVerifyCode} />
              ) : null}
              {err ? <Text style={styles.err}>{err}</Text> : null}
              <RuneDivider />
              {codeSent ? (
                <>
                  <EmberButton label="验证并进入" onPress={onVerifyCode} loading={busy} />
                  <Pressable hitSlop={8} onPress={onRequestCode} style={{ alignSelf: "center", marginTop: theme.space(2) }}>
                    <Text style={styles.linkMuted}>重新发送验证码</Text>
                  </Pressable>
                </>
              ) : (
                <EmberButton label="发送验证码" onPress={onRequestCode} loading={busy} />
              )}
            </View>
          )}

          <View style={styles.footRow}>
            <Link href="/(auth)/register" asChild>
              <Pressable hitSlop={10}>
                <Text style={styles.linkText}>没有账号？立即注册</Text>
              </Pressable>
            </Link>
            <Link href="/(auth)/server" asChild>
              <Pressable hitSlop={10}>
                <Text style={styles.linkMuted}>更换服务器</Text>
              </Pressable>
            </Link>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  scroll: { paddingHorizontal: theme.space(6), gap: theme.space(2) },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  title: { fontFamily: theme.font.display, fontSize: theme.size.xxl, color: theme.color.text, letterSpacing: 1 },
  server: { fontFamily: theme.font.mono, fontSize: theme.size.sm, color: theme.color.textFaint, marginBottom: theme.space(4) },
  seg: { flexDirection: "row", backgroundColor: theme.color.bgInput, borderRadius: theme.radius.md, padding: 3, borderWidth: 1, borderColor: theme.color.surfaceLine, marginBottom: theme.space(4) },
  segItem: { flex: 1, paddingVertical: theme.space(2.5), alignItems: "center", borderRadius: theme.radius.sm },
  segActive: { backgroundColor: theme.color.accentGhost },
  segText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.sm, letterSpacing: 1, color: theme.color.textFaint },
  segTextActive: { color: theme.color.accentBright },
  card: { backgroundColor: theme.color.bgCard, borderRadius: theme.radius.lg, borderWidth: 1, borderColor: theme.color.surfaceLine, padding: theme.space(5), gap: theme.space(4) },
  err: { fontFamily: theme.font.prose, color: theme.color.danger, fontSize: theme.size.sm },
  forgot: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.magicDim },
  footRow: { marginTop: theme.space(6), gap: theme.space(4), alignItems: "center" },
  linkText: { fontFamily: theme.font.proseSemi, color: theme.color.accentBright, fontSize: theme.size.base },
  linkMuted: { fontFamily: theme.font.prose, color: theme.color.textFaint, fontSize: theme.size.sm },
});
