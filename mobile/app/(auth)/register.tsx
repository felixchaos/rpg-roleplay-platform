import React, { useEffect, useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import { Link, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { EmberButton, Field, RuneDivider } from "@/components/ui";
import { TurnstileGate } from "@/components/TurnstileGate";
import { useAuth } from "@/state/auth";
import { auth as authApi } from "@/api";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

export default function RegisterScreen() {
  const { register } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [invite, setInvite] = useState("");
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [sitekey, setSitekey] = useState<string | null>(null);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const insets = useSafeAreaInsets();

  // Discover whether this instance enforces Turnstile, and its sitekey.
  useEffect(() => {
    authApi.schema().then((s) => {
      const sk = s?.notes?.turnstile_sitekey;
      if (sk) setSitekey(sk);
    }).catch(() => {});
  }, []);

  const onRegister = async () => {
    setErr(null);
    setNotice(null);
    if (!username || !password) {
      setErr("用户名与密码为必填项");
      return;
    }
    if (!agree) {
      setErr("请先同意用户协议并确认已成年");
      return;
    }
    if (sitekey && !turnstileToken) {
      setErr("请先完成人机验证");
      return;
    }
    setBusy(true);
    try {
      const { pending } = await register({
        username: username.trim(),
        password,
        email: email.trim() || undefined,
        invite_code: invite.trim() || undefined,
        terms_accepted: true,
        age_confirmed: true,
        turnstile_token: turnstileToken || undefined,
      });
      if (pending) {
        setNotice("注册成功，请前往邮箱完成验证后再登录。");
      } else {
        router.replace("/(app)/chats");
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "注册失败，请重试");
      // Turnstile tokens are single-use; force a fresh challenge after a failed attempt.
      if (sitekey) setTurnstileToken(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <GrimoireBackdrop>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView
          contentContainerStyle={[styles.scroll, { paddingTop: insets.top + theme.space(14), paddingBottom: insets.bottom + 40 }]}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={styles.kicker}>结契立名</Text>
          <Text style={styles.title}>铸造身份</Text>

          <View style={styles.card}>
            <Field label="用户名" value={username} onChangeText={setUsername} autoCapitalize="none" autoCorrect={false} placeholder="your name" />
            <Field label="密码" value={password} onChangeText={setPassword} secureTextEntry placeholder="至少 8 位" />
            <Field label="邮箱（可选）" value={email} onChangeText={setEmail} autoCapitalize="none" keyboardType="email-address" placeholder="you@example.com" />
            <Field label="邀请码（可选）" value={invite} onChangeText={setInvite} autoCapitalize="none" placeholder="若服务器需要" />

            <Pressable style={styles.agreeRow} onPress={() => setAgree((v) => !v)}>
              <Switch
                value={agree}
                onValueChange={setAgree}
                trackColor={{ false: theme.color.bgInput, true: theme.color.accentDeep }}
                thumbColor={agree ? theme.color.accentBright : theme.color.textFaint}
              />
              <Text style={styles.agreeText}>我已满 18 周岁，并同意用户协议与隐私政策。</Text>
            </Pressable>

            {sitekey ? (
              <View style={{ gap: theme.space(2) }}>
                <Text style={styles.tsLabel}>{turnstileToken ? "✓ 人机验证已通过" : "人机验证"}</Text>
                {!turnstileToken ? (
                  <TurnstileGate sitekey={sitekey} onToken={setTurnstileToken} onExpire={() => setTurnstileToken(null)} />
                ) : null}
              </View>
            ) : null}

            {err ? <Text style={styles.err}>{err}</Text> : null}
            {notice ? <Text style={styles.notice}>{notice}</Text> : null}
            <RuneDivider />
            <EmberButton label="注册" onPress={onRegister} loading={busy} />
          </View>

          <Link href="/(auth)/login" asChild>
            <Pressable hitSlop={10} style={{ alignItems: "center", marginTop: theme.space(6) }}>
              <Text style={styles.linkText}>已有账号？返回登录</Text>
            </Pressable>
          </Link>
        </ScrollView>
      </KeyboardAvoidingView>
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  scroll: { paddingHorizontal: theme.space(6), gap: theme.space(2) },
  kicker: {
    fontFamily: theme.font.displaySemi,
    fontSize: theme.size.xs,
    letterSpacing: 4,
    textTransform: "uppercase",
    color: theme.color.accent,
  },
  title: { fontFamily: theme.font.display, fontSize: theme.size.xxl, color: theme.color.text, letterSpacing: 1, marginBottom: theme.space(4) },
  card: {
    backgroundColor: theme.color.bgCard,
    borderRadius: theme.radius.lg,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    padding: theme.space(5),
    gap: theme.space(4),
  },
  agreeRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3) },
  agreeText: { flex: 1, fontFamily: theme.font.prose, color: theme.color.textDim, fontSize: theme.size.sm, lineHeight: 20 },
  err: { fontFamily: theme.font.prose, color: theme.color.danger, fontSize: theme.size.sm },
  tsLabel: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 2, textTransform: "uppercase", color: theme.color.textFaint },
  notice: { fontFamily: theme.font.prose, color: theme.color.success, fontSize: theme.size.sm, lineHeight: 20 },
  linkText: { fontFamily: theme.font.proseSemi, color: theme.color.accentBright, fontSize: theme.size.base },
});
