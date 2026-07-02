import React, { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeIn } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { EmberButton } from "@/components/ui";
import { useAuth } from "@/state/auth";
import { auth as authApi } from "@/api";
import { normalizeBaseUrl } from "@/api/storage";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

/**
 * Scan the desktop console's login QR. The QR encodes a full magic-link URL like
 *   http://host:7860/api/auth/desktop-login?token=XXX
 * We derive the server origin from the URL, persist it, then exchange the token for
 * a session cookie. Falls back to magic-consume when the QR carries email + token.
 */
export default function QrScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { setServer, refresh } = useAuth();
  const [permission, requestPermission] = useCameraPermissions();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const handleScan = async ({ data }: { data: string }) => {
    if (busy || done) return;
    setBusy(true);
    setErr(null);
    try {
      const url = new URL(data.trim());
      const origin = normalizeBaseUrl(`${url.protocol}//${url.host}`);
      await setServer(origin);

      const token = url.searchParams.get("token");
      const email = url.searchParams.get("email");

      if (token && email) {
        await authApi.magicConsume(token, email);
      } else if (token) {
        // desktop-login redirect endpoint sets the cookie; our http layer captures it.
        await fetch(`${origin}/api/auth/desktop-login?token=${encodeURIComponent(token)}`, {
          redirect: "manual",
        }).catch(() => {});
        // The redirect carries Set-Cookie; re-fetch /me to confirm + cache it.
      }
      await refresh();
      setDone(true);
      router.replace("/(app)/chats");
    } catch (e) {
      setErr(
        e instanceof ApiError
          ? e.message
          : "二维码无法识别，请确认这是桌面端「控制台 → 登录二维码」。",
      );
      setTimeout(() => setBusy(false), 1200);
    }
  };

  if (!permission) {
    return <GrimoireBackdrop />;
  }

  if (!permission.granted) {
    return (
      <GrimoireBackdrop>
        <View style={[styles.center, { paddingTop: insets.top }]}>
          <Text style={styles.glyph}>⌖</Text>
          <Text style={styles.title}>需要相机权限</Text>
          <Text style={styles.body}>扫描桌面端登录二维码以免密登入你的账号。</Text>
          <EmberButton label="授予权限" onPress={requestPermission} style={{ marginTop: theme.space(4) }} />
          <Pressable onPress={() => router.back()} hitSlop={10} style={{ marginTop: theme.space(5) }}>
            <Text style={styles.link}>返回</Text>
          </Pressable>
        </View>
      </GrimoireBackdrop>
    );
  }

  return (
    <View style={styles.root}>
      <CameraView
        style={StyleSheet.absoluteFill as any}
        facing="back"
        barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
        onBarcodeScanned={busy ? undefined : handleScan}
      />
      <View style={[styles.overlay, { paddingTop: insets.top + theme.space(6) }]} pointerEvents="box-none">
        <Text style={styles.scanKicker}>QR · 免密登入</Text>
        <Text style={styles.scanTitle}>对准桌面二维码</Text>

        <View style={styles.reticleWrap}>
          <Animated.View entering={FadeIn.duration(600)} style={styles.reticle}>
            <View style={[styles.corner, styles.tl]} />
            <View style={[styles.corner, styles.tr]} />
            <View style={[styles.corner, styles.bl]} />
            <View style={[styles.corner, styles.br]} />
          </Animated.View>
        </View>

        {err ? <Text style={styles.err}>{err}</Text> : null}
        {busy && !err ? <Text style={styles.scanning}>正在登入…</Text> : null}

        <Pressable onPress={() => router.back()} hitSlop={12} style={[styles.backChip, { bottom: insets.bottom + theme.space(8) }]}>
          <Text style={styles.backChipText}>用密码登录</Text>
        </Pressable>
      </View>
    </View>
  );
}

const RET = 240;
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000" },
  overlay: { flex: 1, alignItems: "center", paddingHorizontal: theme.space(6) },
  center: { flex: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: theme.space(10) },
  glyph: { fontSize: 56, color: theme.color.accent, opacity: 0.7, marginBottom: theme.space(4) },
  title: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, textAlign: "center" },
  body: { fontFamily: theme.font.prose, fontSize: theme.size.md, color: theme.color.textDim, textAlign: "center", lineHeight: 23, marginTop: theme.space(2) },
  link: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.accentBright },
  scanKicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accentBright },
  scanTitle: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: "#fff", marginTop: theme.space(1), textShadowColor: "rgba(0,0,0,0.8)", textShadowRadius: 8 },
  reticleWrap: { flex: 1, alignItems: "center", justifyContent: "center" },
  reticle: { width: RET, height: RET },
  corner: { position: "absolute", width: 38, height: 38, borderColor: theme.color.accentBright },
  tl: { top: 0, left: 0, borderTopWidth: 3, borderLeftWidth: 3, borderTopLeftRadius: 6 },
  tr: { top: 0, right: 0, borderTopWidth: 3, borderRightWidth: 3, borderTopRightRadius: 6 },
  bl: { bottom: 0, left: 0, borderBottomWidth: 3, borderLeftWidth: 3, borderBottomLeftRadius: 6 },
  br: { bottom: 0, right: 0, borderBottomWidth: 3, borderRightWidth: 3, borderBottomRightRadius: 6 },
  err: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.accentBright, textAlign: "center", backgroundColor: "rgba(0,0,0,0.7)", padding: theme.space(3), borderRadius: theme.radius.md, overflow: "hidden" },
  scanning: { fontFamily: theme.font.displaySemi, fontSize: theme.size.sm, letterSpacing: 2, color: "#fff", textTransform: "uppercase" },
  backChip: { position: "absolute", paddingHorizontal: theme.space(5), paddingVertical: theme.space(3), borderRadius: theme.radius.pill, borderWidth: 1, borderColor: "rgba(255,255,255,0.3)", backgroundColor: "rgba(0,0,0,0.5)" },
  backChipText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: "#fff" },
});
