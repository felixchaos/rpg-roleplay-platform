/**
 * Character Card Editor — forge or revise an NPC card. Mirrors the backend card
 * fields (name / identity / appearance / personality / background / tags) plus an
 * avatar uploaded from the device gallery. Accepts an optional `id` param to edit
 * an existing card; absent means create-new. Candlelit Grimoire styling throughout.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Image } from "expo-image";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as ImagePicker from "expo-image-picker";
import * as Haptics from "expo-haptics";
import Animated, { FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { EmberButton, Field, RuneDivider } from "@/components/ui";
import { cards } from "@/api";
import { baseUrl, ApiError } from "@/api/http";
import { appendFile } from "@/api/formdata";
import { theme } from "@/theme/theme";

type Card = {
  id?: number;
  name?: string;
  identity?: string;
  appearance?: string;
  personality?: string;
  background?: string;
  current_status?: string;
  language_style?: string;
  secret?: string;
  sample_dialogue?: string;
  aliases?: string[];
  tags?: string[];
  avatar_path?: string;
  [k: string]: unknown;
};

const FIELDS: { key: keyof Card; label: string; multiline?: boolean; placeholder?: string }[] = [
  { key: "name", label: "姓名" },
  { key: "identity", label: "身份" },
  { key: "appearance", label: "外貌", multiline: true },
  { key: "personality", label: "性格", multiline: true },
  { key: "background", label: "背景故事", multiline: true },
  { key: "current_status", label: "当前状态", multiline: true, placeholder: "目前在做什么、处境如何…" },
  { key: "language_style", label: "语言风格", multiline: true, placeholder: "说话方式、口头禅、语气…" },
  { key: "secret", label: "秘密", multiline: true, placeholder: "不轻易吐露的秘密，仅 GM 知晓" },
  { key: "sample_dialogue", label: "示例对话", multiline: true, placeholder: "{{user}}：你好\\n{{char}}：……" },
];

export default function CardEditScreen() {
  const { id } = useLocalSearchParams<{ id?: string }>();
  const cardId = id ? Number(id) : undefined;
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [card, setCard] = useState<Card>({});
  const [tagsText, setTagsText] = useState("");
  const [aliasesText, setAliasesText] = useState("");
  const [base, setBase] = useState("");
  const [loading, setLoading] = useState(!!cardId);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);

  const load = useCallback(async () => {
    setBase(await baseUrl().catch(() => ""));
    if (!cardId) return;
    setLoading(true);
    try {
      const r = await cards.getCharacter(cardId);
      const c = r?.card ?? {};
      setCard(c);
      setTagsText(Array.isArray(c.tags) ? c.tags.join(", ") : "");
      setAliasesText(Array.isArray(c.aliases) ? c.aliases.join(", ") : "");
    } catch (e) {
      if (e instanceof ApiError) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, [cardId]);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    if (!card.name?.trim()) {
      Alert.alert("缺少姓名", "请先为角色起个名字。");
      return;
    }
    setSaving(true);
    try {
      const tags = tagsText.split(",").map((t) => t.trim()).filter(Boolean);
      const aliases = aliasesText.split(",").map((t) => t.trim()).filter(Boolean);
      const r = await cards.upsertCharacter({ ...card, tags, aliases });
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      // capture new id so a freshly-created card can immediately take an avatar
      const newId = r?.card?.id ?? cardId;
      if (newId && !card.id) setCard((c) => ({ ...c, id: newId }));
      Alert.alert("已保存", "角色卡已写入你的卡库。", [{ text: "好", onPress: () => router.back() }]);
    } catch (e) {
      Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setSaving(false);
    }
  };

  const pickAvatar = async () => {
    const targetId = card.id ?? cardId;
    if (!targetId) {
      Alert.alert("请先保存", "先保存角色卡，再为它设置头像。");
      return;
    }
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      Alert.alert("需要相册权限", "请在系统设置中允许访问相册。");
      return;
    }
    const res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], quality: 0.85 });
    if (res.canceled || !res.assets?.[0]) return;
    const asset = res.assets[0];
    setUploading(true);
    try {
      const form = new FormData();
      await appendFile(form, "file", {
        uri: asset.uri,
        name: asset.fileName || "avatar.jpg",
        mimeType: asset.mimeType || "image/jpeg",
      });
      const r = await cards.uploadAvatar(targetId, form);
      if (r?.url) setCard((c) => ({ ...c, avatar_path: r.url }));
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e) {
      Alert.alert("上传失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setUploading(false);
    }
  };

  const avatarUri = card.avatar_path
    ? card.avatar_path.startsWith("http")
      ? card.avatar_path
      : base + card.avatar_path
    : null;

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>{cardId ? "Revise" : "Forge"}</Text>
          <Text style={styles.h1}>{cardId ? "修订角色" : "铸造角色"}</Text>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <ScrollView
            contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 50, gap: theme.space(4), paddingTop: theme.space(2) }}
            keyboardShouldPersistTaps="handled"
          >
            <Pressable onPress={pickAvatar} style={styles.avatarPick}>
              {avatarUri ? (
                <Image source={{ uri: avatarUri }} style={styles.avatar} contentFit="cover" transition={200} />
              ) : (
                <View style={[styles.avatar, styles.avatarFallback]}>
                  <Text style={styles.avatarGlyph}>{uploading ? "…" : "＋"}</Text>
                </View>
              )}
              <Text style={styles.avatarHint}>{uploading ? "上传中…" : "点按设置头像"}</Text>
            </Pressable>

            {FIELDS.map((f, i) => (
              <Animated.View key={String(f.key)} entering={FadeInDown.delay(i * 50).duration(360)}>
                <Field
                  label={f.label}
                  value={(card[f.key] as string) || ""}
                  onChangeText={(t) => setCard((c) => ({ ...c, [f.key]: t }))}
                  multiline={f.multiline}
                  placeholder={f.placeholder}
                  style={f.multiline ? { minHeight: 92, textAlignVertical: "top" } : undefined}
                />
              </Animated.View>
            ))}

            <Field label="标签（逗号分隔）" value={tagsText} onChangeText={setTagsText} placeholder="温柔, 剑客, 宿敌" autoCapitalize="none" />
            <Field label="别名（逗号分隔）" value={aliasesText} onChangeText={setAliasesText} placeholder="阿星, 小南, Master" autoCapitalize="none" />

            <RuneDivider />
            <EmberButton label={saving ? "镌刻中…" : "保存角色卡"} onPress={save} loading={saving} />
          </ScrollView>
        </KeyboardAvoidingView>
      )}
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  avatarPick: { alignItems: "center", gap: theme.space(2), paddingVertical: theme.space(2) },
  avatar: { width: 110, height: 110, borderRadius: theme.radius.lg, borderWidth: 1, borderColor: theme.color.surfaceLineStrong },
  avatarFallback: { backgroundColor: theme.color.bgInput, alignItems: "center", justifyContent: "center" },
  avatarGlyph: { fontSize: 40, color: theme.color.accent },
  avatarHint: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint },
});
