/**
 * Missive — the feedback channel. The player pens a note to the keepers of the realm.
 * The backend requires a 64-char SHA256 consent_token (proof the user saw the consent
 * statement before sending), so we hash the exact consent text with expo-crypto at send
 * time. NSFW pre-moderation lives server-side; an auto-reject returns an error_key we
 * surface plainly rather than swallowing.
 */
import React, { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { BlurView } from "expo-blur";
import * as Crypto from "expo-crypto";
import Constants from "expo-constants";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { EmberButton, Field } from "@/components/ui";
import { compliance } from "@/api";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

const CONSENT_TEXT =
  "我同意将本反馈内容（及可选的联系邮箱）提交给平台运营方用于产品改进，并理解反馈会经过内容审核。";

export function FeedbackDrawer({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const insets = useSafeAreaInsets();
  const [text, setText] = useState("");
  const [email, setEmail] = useState("");
  const [consented, setConsented] = useState(false);
  const [busy, setBusy] = useState(false);

  const reset = () => {
    setText("");
    setEmail("");
    setConsented(false);
  };

  const send = async () => {
    if (!text.trim()) return;
    if (!consented) {
      Alert.alert("需要同意", "请先勾选同意声明，再提交反馈。");
      return;
    }
    setBusy(true);
    try {
      const consent_token = await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, CONSENT_TEXT);
      const app_version = Constants.expoConfig?.version || "1.0.0";
      const r = await compliance.submitFeedback({
        free_text: text.trim(),
        consent_token,
        app_version,
        contact_email: email.trim() || undefined,
      });
      if (r?.error_key === "feedback.nsfw_terminate") {
        Alert.alert("提交被拒", r.message || "反馈内容触发了内容红线。");
        setBusy(false);
        return;
      }
      Alert.alert("已送达", "感谢你的来信，我们已收到。", [
        { text: "好", onPress: () => { reset(); onClose(); } },
      ]);
    } catch (e) {
      Alert.alert("提交失败", e instanceof ApiError ? e.message : "请稍后重试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
        <BlurView intensity={30} tint="dark" style={styles.fill} />
        <View style={styles.grabber} />
        <Text style={styles.title}>寄出一封信</Text>
        <Text style={styles.subtitle}>错漏、心愿，或只是想说点什么 — 我们在听。</Text>

        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ gap: theme.space(4) }}>
          <TextInput
            value={text}
            onChangeText={setText}
            placeholder="写下你的反馈…"
            placeholderTextColor={theme.color.textFaint}
            style={styles.textArea}
            multiline
            editable={!busy}
          />
          <Field
            label="联系邮箱（可选）"
            value={email}
            onChangeText={setEmail}
            placeholder="收到回执时使用"
            autoCapitalize="none"
            keyboardType="email-address"
          />
          <Pressable style={styles.consentRow} onPress={() => setConsented((v) => !v)}>
            <View style={[styles.checkbox, consented && styles.checkboxOn]}>
              {consented ? <Text style={styles.checkGlyph}>✓</Text> : null}
            </View>
            <Text style={styles.consentText}>{CONSENT_TEXT}</Text>
          </Pressable>
          <EmberButton label={busy ? "寄送中…" : "寄出"} onPress={send} loading={busy} />
        </KeyboardAvoidingView>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  fill: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: theme.color.scrim },
  sheet: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "rgba(20,16,12,0.92)",
    borderTopLeftRadius: theme.radius.xl,
    borderTopRightRadius: theme.radius.xl,
    borderWidth: 1,
    borderColor: theme.color.surfaceLineStrong,
    overflow: "hidden",
    paddingHorizontal: theme.space(5),
    paddingTop: theme.space(3),
  },
  grabber: { alignSelf: "center", width: 44, height: 4, borderRadius: 2, backgroundColor: theme.color.surfaceLineStrong, marginBottom: theme.space(3) },
  title: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.text, letterSpacing: 1 },
  subtitle: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, marginTop: theme.space(1), marginBottom: theme.space(4), lineHeight: 19 },
  textArea: { minHeight: 120, backgroundColor: theme.color.bgInput, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, padding: theme.space(4), color: theme.color.text, fontFamily: theme.font.prose, fontSize: theme.size.md, lineHeight: 22, textAlignVertical: "top" },
  consentRow: { flexDirection: "row", gap: theme.space(3), alignItems: "flex-start" },
  checkbox: { width: 22, height: 22, borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLineStrong, alignItems: "center", justifyContent: "center", marginTop: 1 },
  checkboxOn: { backgroundColor: theme.color.accent, borderColor: theme.color.accent },
  checkGlyph: { color: theme.color.bg, fontSize: 14, fontWeight: "700" },
  consentText: { flex: 1, fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim, lineHeight: 20 },
});
