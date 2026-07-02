/**
 * Lost Sigil — password recovery. The reset itself completes via an emailed web link
 * (the backend issues a tokened URL), so the app's job is just to request it. We always
 * report success regardless of whether the email exists — the backend is anti-enumeration
 * by design, and the UI honors that by never confirming an address is registered.
 */
import React, { useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Link, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { EmberButton, Field, RuneDivider } from "@/components/ui";
import { auth as authApi } from "@/api";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

export default function ForgotScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const onSend = async () => {
    if (!email.trim()) {
      setErr("请输入邮箱");
      return;
    }
    setErr(null);
    setBusy(true);
    try {
      await authApi.forgotPassword(email.trim());
      setSent(true);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "请求失败，请重试");
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
          <Text style={styles.kicker}>遗失的印记</Text>
          <Text style={styles.title}>找回密码</Text>

          {sent ? (
            <View style={styles.card}>
              <Text style={styles.doneGlyph}>✉</Text>
              <Text style={styles.doneText}>
                若该邮箱已注册，我们已寄出一封重置邮件。请点击邮件中的链接设置新密码。
              </Text>
              <RuneDivider />
              <EmberButton label="返回登录" onPress={() => router.replace("/(auth)/login")} />
            </View>
          ) : (
            <View style={styles.card}>
              <Text style={styles.sub}>输入注册邮箱，我们将寄出一封重置链接。</Text>
              <Field
                label="邮箱"
                value={email}
                onChangeText={setEmail}
                autoCapitalize="none"
                keyboardType="email-address"
                placeholder="you@example.com"
                onSubmitEditing={onSend}
              />
              {err ? <Text style={styles.err}>{err}</Text> : null}
              <RuneDivider />
              <EmberButton label="寄出重置邮件" onPress={onSend} loading={busy} />
            </View>
          )}

          <Link href="/(auth)/login" asChild>
            <Pressable hitSlop={10} style={{ alignItems: "center", marginTop: theme.space(6) }}>
              <Text style={styles.linkText}>返回登录</Text>
            </Pressable>
          </Link>
        </ScrollView>
      </KeyboardAvoidingView>
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  scroll: { paddingHorizontal: theme.space(6), gap: theme.space(2) },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  title: { fontFamily: theme.font.display, fontSize: theme.size.xxl, color: theme.color.text, letterSpacing: 1, marginBottom: theme.space(5) },
  sub: { fontFamily: theme.font.prose, fontSize: theme.size.md, color: theme.color.textDim, lineHeight: 23 },
  card: { backgroundColor: theme.color.bgCard, borderRadius: theme.radius.lg, borderWidth: 1, borderColor: theme.color.surfaceLine, padding: theme.space(5), gap: theme.space(4) },
  err: { fontFamily: theme.font.prose, color: theme.color.danger, fontSize: theme.size.sm },
  doneGlyph: { fontSize: 40, color: theme.color.accent, textAlign: "center" },
  doneText: { fontFamily: theme.font.prose, fontSize: theme.size.md, color: theme.color.textDim, lineHeight: 24, textAlign: "center" },
  linkText: { fontFamily: theme.font.proseSemi, color: theme.color.accentBright, fontSize: theme.size.base },
});
