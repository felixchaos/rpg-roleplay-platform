/**
 * Scrying Glass — conjure imagery mid-roleplay. The player whispers a scene; we enqueue
 * an async image job, poll it to completion, and reveal the result. Past conjurings for
 * this save form a gallery below. Generation jobs are slow + can fail on quota/credentials,
 * so the polling state is first-class: a slow ember shimmer while the vision forms.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Image } from "expo-image";
import { BlurView } from "expo-blur";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeIn, Easing, useAnimatedStyle, useSharedValue, withRepeat, withTiming, cancelAnimation } from "react-native-reanimated";
import { images, GenImage } from "@/api";
import { baseUrl, ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

const TERMINAL = new Set(["done", "failed", "cancelled", "error", "succeeded", "success"]);

// The five documented aspect tiers, mapped to the backend's WxH size param.
const SIZE_TIERS: { label: string; size: string }[] = [
  { label: "2:3", size: "832x1216" },
  { label: "1:1", size: "1024x1024" },
  { label: "3:2", size: "1216x832" },
  { label: "9:16", size: "768x1344" },
  { label: "16:9", size: "1344x768" },
];

function ConjuringShimmer() {
  const o = useSharedValue(0.3);
  useEffect(() => {
    o.value = withRepeat(withTiming(0.9, { duration: 900, easing: Easing.inOut(Easing.ease) }), -1, true);
    return () => cancelAnimation(o);
  }, [o]);
  const style = useAnimatedStyle(() => ({ opacity: o.value }));
  return (
    <View style={styles.conjuring}>
      <Animated.Text style={[styles.conjureGlyph, style]}>✶</Animated.Text>
      <Text style={styles.conjureText}>幻象凝结中…</Text>
    </View>
  );
}

export function ScryingGlass({
  visible,
  saveId,
  onClose,
}: {
  visible: boolean;
  saveId: number;
  onClose: () => void;
}) {
  const insets = useSafeAreaInsets();
  const [prompt, setPrompt] = useState("");
  const [base, setBase] = useState("");
  const [gallery, setGallery] = useState<GenImage[]>([]);
  const [generating, setGenerating] = useState(false);
  const [size, setSize] = useState("1024x1024");
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const resolveUrl = useCallback((u?: string) => (!u ? "" : u.startsWith("http") ? u : base + u), [base]);

  const loadGallery = useCallback(async () => {
    try {
      const r = await images.list(saveId);
      const items = Array.isArray(r) ? r : (r as any)?.items ?? [];
      setGallery(items.filter((it: GenImage) => it.url || it.status === "done"));
    } catch {
      /* gallery is best-effort */
    }
  }, [saveId]);

  useEffect(() => {
    if (visible) {
      baseUrl().then(setBase).catch(() => {});
      loadGallery();
    }
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [visible, loadGallery]);

  const poll = useCallback(
    (id: number, attempt = 0) => {
      pollRef.current = setTimeout(async () => {
        try {
          const img = await images.get(id);
          if (TERMINAL.has(img.status)) {
            setGenerating(false);
            if (img.url) {
              setGallery((prev) => [img, ...prev.filter((p) => p.id !== id)]);
            } else if (img.status === "failed" || img.status === "error") {
              Alert.alert("生成失败", img.error || "幻象未能成形，请重试。");
            }
            return;
          }
        } catch {
          /* transient; keep polling */
        }
        if (attempt < 40) poll(id, attempt + 1);
        else setGenerating(false);
      }, 2200);
    },
    [],
  );

  const conjure = async () => {
    const text = prompt.trim();
    if (!text || generating) return;
    setGenerating(true);
    try {
      const r = await images.generate({ prompt: text, kind: "chat", save_id: saveId, size });
      if (r?.code === "quota_exceeded") {
        setGenerating(false);
        Alert.alert("额度已尽", "今日生图额度已用完，请明日再来。");
        return;
      }
      if (r?.code === "credentials_required") {
        setGenerating(false);
        Alert.alert("缺少凭据", "请先在设置中为生图提供商配置 API Key。");
        return;
      }
      if (r?.image_id) {
        setPrompt("");
        poll(r.image_id);
      } else {
        setGenerating(false);
        Alert.alert("无法生成", "服务器未返回任务。");
      }
    } catch (e) {
      setGenerating(false);
      Alert.alert("生成失败", e instanceof ApiError ? e.message : "请重试");
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
        <BlurView intensity={30} tint="dark" style={styles.fill} />
        <View style={styles.grabber} />
        <Text style={styles.title}>映象之镜</Text>

        <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <View style={styles.sizeRow}>
            {SIZE_TIERS.map((s) => {
              const on = size === s.size;
              return (
                <Pressable key={s.size} onPress={() => setSize(s.size)} style={[styles.sizeChip, on && styles.sizeChipOn]}>
                  <Text style={[styles.sizeText, on && { color: theme.color.accentBright }]}>{s.label}</Text>
                </Pressable>
              );
            })}
          </View>
          <View style={styles.composer}>
            <TextInput
              value={prompt}
              onChangeText={setPrompt}
              placeholder="描绘你想看见的场景…"
              placeholderTextColor={theme.color.textFaint}
              style={styles.input}
              multiline
              editable={!generating}
            />
            <Pressable onPress={conjure} disabled={!prompt.trim() || generating} style={[styles.conjureBtn, (!prompt.trim() || generating) && { opacity: 0.4 }]}>
              {generating ? <ActivityIndicator color={theme.color.bg} size="small" /> : <Text style={styles.conjureBtnGlyph}>✶</Text>}
            </Pressable>
          </View>
        </KeyboardAvoidingView>

        <ScrollView style={styles.body} contentContainerStyle={{ gap: theme.space(3), paddingTop: theme.space(3) }}>
          {generating ? <ConjuringShimmer /> : null}
          {gallery.length === 0 && !generating ? (
            <Text style={styles.empty}>尚无映象。说出一个场景，让它显形。</Text>
          ) : (
            gallery.map((img, i) => {
              const uri = resolveUrl(img.url);
              if (!uri) return null;
              return (
                <Animated.View key={img.id} entering={FadeIn.delay(i * 50).duration(400)} style={styles.frame}>
                  <Image source={{ uri }} style={styles.image} contentFit="cover" transition={300} />
                  {img.prompt ? <Text style={styles.caption} numberOfLines={2}>{img.prompt}</Text> : null}
                </Animated.View>
              );
            })
          )}
        </ScrollView>
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
    maxHeight: "84%",
    backgroundColor: "rgba(20,16,12,0.9)",
    borderTopLeftRadius: theme.radius.xl,
    borderTopRightRadius: theme.radius.xl,
    borderWidth: 1,
    borderColor: theme.color.surfaceLineStrong,
    overflow: "hidden",
    paddingHorizontal: theme.space(5),
    paddingTop: theme.space(3),
  },
  grabber: { alignSelf: "center", width: 44, height: 4, borderRadius: 2, backgroundColor: theme.color.surfaceLineStrong, marginBottom: theme.space(3) },
  title: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.text, letterSpacing: 1, marginBottom: theme.space(3) },
  composer: { flexDirection: "row", alignItems: "flex-end", gap: theme.space(3) },
  sizeRow: { flexDirection: "row", gap: theme.space(2), marginBottom: theme.space(2) },
  sizeChip: { flex: 1, paddingVertical: theme.space(2), alignItems: "center", borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  sizeChipOn: { backgroundColor: theme.color.accentGhost, borderColor: theme.color.accentSoft },
  sizeText: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint },
  input: { flex: 1, maxHeight: 110, minHeight: 48, backgroundColor: theme.color.bgInput, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, paddingHorizontal: theme.space(4), paddingTop: theme.space(3), paddingBottom: theme.space(3), color: theme.color.text, fontFamily: theme.font.prose, fontSize: theme.size.md, lineHeight: 22 },
  conjureBtn: { width: 48, height: 48, borderRadius: theme.radius.pill, backgroundColor: theme.color.accent, alignItems: "center", justifyContent: "center", marginBottom: 1 },
  conjureBtnGlyph: { fontSize: 22, color: theme.color.bg },
  body: { marginTop: theme.space(2) },
  conjuring: { alignItems: "center", justifyContent: "center", paddingVertical: theme.space(10), gap: theme.space(2), borderRadius: theme.radius.lg, borderWidth: 1, borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  conjureGlyph: { fontSize: 40, color: theme.color.accentBright },
  conjureText: { fontFamily: theme.font.proseItalic, fontSize: theme.size.md, color: theme.color.accent },
  empty: { fontFamily: theme.font.proseItalic, fontSize: theme.size.md, color: theme.color.textFaint, textAlign: "center", paddingVertical: theme.space(12) },
  frame: { borderRadius: theme.radius.lg, overflow: "hidden", borderWidth: 1, borderColor: theme.color.surfaceLineStrong, backgroundColor: theme.color.bgCard },
  image: { width: "100%", aspectRatio: 1 },
  caption: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textDim, padding: theme.space(3), lineHeight: 19 },
});
