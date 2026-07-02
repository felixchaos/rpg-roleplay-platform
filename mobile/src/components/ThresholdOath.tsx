/**
 * Threshold Oath — the mandatory 18+ compliance gate (AGE-02). Blocks the entire authed
 * app until the user affirms they're of age for this version of the adult-content notice.
 * Status comes from /api/me/splash/status; affirmation posts to /api/me/splash/ack. This
 * is a legal requirement, not decoration — it must be impossible to slip past, so it owns
 * the full screen with no dismiss affordance other than the oath itself.
 */
import React, { useState } from "react";
import { Linking, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeIn, FadeInUp } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { EmberButton } from "@/components/ui";
import { compliance } from "@/api";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

const LEGAL_URL = "https://play.stellatrix.icu/legal/adult-content-disclaimer.zh-CN.html";

export function ThresholdOath({ version, onAcked }: { version: string; onAcked: () => void }) {
  const insets = useSafeAreaInsets();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const affirm = async () => {
    setBusy(true);
    setErr(null);
    try {
      await compliance.splashAck(version);
      onAcked();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "确认失败，请重试");
      setBusy(false);
    }
  };

  return (
    <GrimoireBackdrop>
      <ScrollView
        contentContainerStyle={[styles.scroll, { paddingTop: insets.top + theme.space(16), paddingBottom: insets.bottom + 40 }]}
        showsVerticalScrollIndicator={false}
      >
        <Animated.Text entering={FadeIn.duration(700)} style={styles.seal}>
          ⛧
        </Animated.Text>
        <Animated.View entering={FadeInUp.delay(150).duration(500)}>
          <Text style={styles.kicker}>逾越门槛之前</Text>
          <Text style={styles.title}>成人内容声明</Text>
        </Animated.View>

        <Animated.View entering={FadeInUp.delay(280).duration(500)} style={styles.scroll1}>
          <Text style={styles.body}>
            本平台承载面向成年人的虚构叙事，可能包含暴力、性暗示及其他不适合未成年人的题材。继续即表示你郑重声明：
          </Text>
          <View style={styles.oathList}>
            <OathLine text="我已年满 18 周岁（或所在司法辖区的法定成年年龄）。" />
            <OathLine text="我自愿浏览此类虚构内容，并对自己的选择负责。" />
            <OathLine text="此处一切角色与情节均为虚构，不涉及任何真实人物。" />
          </View>
          <Pressable onPress={() => Linking.openURL(LEGAL_URL).catch(() => {})} hitSlop={8}>
            <Text style={styles.legalLink}>阅读完整《成人内容免责声明》 ›</Text>
          </Pressable>
        </Animated.View>

        {err ? <Text style={styles.err}>{err}</Text> : null}

        <Animated.View entering={FadeInUp.delay(420).duration(500)} style={styles.actions}>
          <EmberButton label={busy ? "确认中…" : "我已成年，进入"} onPress={affirm} loading={busy} />
          <Text style={styles.declineHint}>若你未满 18 周岁，请立即离开。</Text>
        </Animated.View>
      </ScrollView>
    </GrimoireBackdrop>
  );
}

function OathLine({ text }: { text: string }) {
  return (
    <View style={styles.oathLine}>
      <Text style={styles.oathDiamond}>◆</Text>
      <Text style={styles.oathText}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  scroll: { paddingHorizontal: theme.space(7), alignItems: "center" },
  seal: { fontSize: 64, color: theme.color.accent, opacity: 0.85, marginBottom: theme.space(5), textShadowColor: theme.color.accentSoft, textShadowRadius: 24 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 5, textTransform: "uppercase", color: theme.color.accent, textAlign: "center" },
  title: { fontFamily: theme.font.display, fontSize: theme.size.xxl, color: theme.color.text, letterSpacing: 1, textAlign: "center", marginTop: theme.space(2), marginBottom: theme.space(6) },
  scroll1: { backgroundColor: theme.color.bgCard, borderRadius: theme.radius.lg, borderWidth: 1, borderColor: theme.color.surfaceLine, padding: theme.space(5), gap: theme.space(4), width: "100%" },
  body: { fontFamily: theme.font.prose, fontSize: theme.size.md, color: theme.color.textDim, lineHeight: 25 },
  oathList: { gap: theme.space(3) },
  oathLine: { flexDirection: "row", gap: theme.space(3), alignItems: "flex-start" },
  oathDiamond: { color: theme.color.accent, fontSize: 10, marginTop: 5 },
  oathText: { flex: 1, fontFamily: theme.font.proseMedium, fontSize: theme.size.base, color: theme.color.text, lineHeight: 23 },
  legalLink: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.magic, marginTop: theme.space(1) },
  err: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.danger, marginTop: theme.space(4), textAlign: "center" },
  actions: { width: "100%", marginTop: theme.space(7), gap: theme.space(3) },
  declineHint: { fontFamily: theme.font.proseItalic, fontSize: theme.size.sm, color: theme.color.textFaint, textAlign: "center" },
});
