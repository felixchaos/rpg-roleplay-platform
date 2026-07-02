/**
 * Persona Manager — your roster of player identities. Each persona is who *you* are
 * when you step into a story. List, forge new ones, or revise existing. Fields mirror
 * the backend persona card (name / identity / appearance / personality / background /
 * speech_style). Two-pane feel on one screen: a list that slides aside into an editor.
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
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import Animated, { FadeInDown, FadeIn } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { GrimoireDock, DOCK_HEIGHT } from "@/components/GrimoireDock";
import { EmberButton, Field, IconLabelButton, RuneDivider } from "@/components/ui";
import { cards } from "@/api";
import { ApiError } from "@/api/http";
import { theme } from "@/theme/theme";

type Persona = {
  id?: number;
  name?: string;
  identity?: string;
  appearance?: string;
  personality?: string;
  background?: string;
  speech_style?: string;
  [k: string]: unknown;
};

const FIELDS: { key: keyof Persona; label: string; multiline?: boolean }[] = [
  { key: "name", label: "称谓" },
  { key: "identity", label: "身份" },
  { key: "appearance", label: "外貌", multiline: true },
  { key: "personality", label: "性格", multiline: true },
  { key: "background", label: "背景", multiline: true },
  { key: "speech_style", label: "言谈风格" },
];

export default function PersonasScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [list, setList] = useState<Persona[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<Persona | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await cards.personas();
      setList(r?.items ?? []);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    if (!editing?.name?.trim()) {
      Alert.alert("缺少称谓", "请至少为这个身份起个名字。");
      return;
    }
    setSaving(true);
    try {
      await cards.upsertPersona(editing as Record<string, unknown>);
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      setEditing(null);
      load();
    } catch (e) {
      Alert.alert("保存失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setSaving(false);
    }
  };

  const remove = (p: Persona) => {
    Alert.alert("删除身份", `确定删除「${p.name}」？此操作不可撤销。`, [
      { text: "取消", style: "cancel" },
      {
        text: "删除",
        style: "destructive",
        onPress: async () => {
          try {
            if (p.id != null) await cards.removePersona(p.id);
            load();
          } catch (e) {
            Alert.alert("删除失败", e instanceof ApiError ? e.message : "请重试");
          }
        },
      },
    ]);
  };

  // ---- editor pane ----
  if (editing) {
    return (
      <GrimoireBackdrop>
        <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
          <Pressable onPress={() => setEditing(null)} hitSlop={12} style={styles.headBtn}>
            <Text style={styles.headGlyph}>‹</Text>
          </Pressable>
          <View style={{ flex: 1 }}>
            <Text style={styles.kicker}>{editing.id ? "Revise" : "Forge"}</Text>
            <Text style={styles.h1}>{editing.id ? "修订身份" : "铸造身份"}</Text>
          </View>
        </View>
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <ScrollView
            contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 40, gap: theme.space(4), paddingTop: theme.space(2) }}
            keyboardShouldPersistTaps="handled"
          >
            {FIELDS.map((f, i) => (
              <Animated.View key={String(f.key)} entering={FadeInDown.delay(i * 50).duration(360)}>
                <Field
                  label={f.label}
                  value={(editing[f.key] as string) || ""}
                  onChangeText={(t) => setEditing((prev) => ({ ...prev, [f.key]: t }))}
                  multiline={f.multiline}
                  style={f.multiline ? { minHeight: 92, textAlignVertical: "top" } : undefined}
                />
              </Animated.View>
            ))}
            <RuneDivider />
            <EmberButton label={saving ? "镌刻中…" : "保存身份"} onPress={save} loading={saving} />
          </ScrollView>
        </KeyboardAvoidingView>
      </GrimoireBackdrop>
    );
  }

  // ---- list pane ----
  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Personas</Text>
          <Text style={styles.h1}>我的身份</Text>
        </View>
        <IconLabelButton glyph="＋" label="新建" onPress={() => setEditing({})} />
      </View>

      <ScrollView
        contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + DOCK_HEIGHT + 30, gap: theme.space(3), paddingTop: theme.space(2) }}
      >
        {loading && list.length === 0 ? (
          <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
        ) : list.length === 0 ? (
          <View style={styles.empty}>
            <Text style={styles.emptyGlyph}>𓂀</Text>
            <Text style={styles.emptyText}>你还没有任何身份。铸造一个，决定你在故事中是谁。</Text>
          </View>
        ) : (
          list.map((p, i) => (
            <Animated.View key={String(p.id ?? i)} entering={FadeIn.delay(i * 45).duration(340)}>
              <Pressable onPress={() => setEditing({ ...p })} onLongPress={() => remove(p)} style={({ pressed }) => [styles.row, pressed && { backgroundColor: theme.color.bgElevated }]}>
                <View style={styles.sigil}>
                  <Text style={styles.sigilText}>{(p.name || "?").trim().charAt(0).toUpperCase()}</Text>
                </View>
                <View style={{ flex: 1, gap: theme.space(1) }}>
                  <Text style={styles.name} numberOfLines={1}>{p.name || "无名"}</Text>
                  <Text style={styles.identity} numberOfLines={1}>{p.identity || p.personality || "—"}</Text>
                </View>
                <Text style={styles.edit}>编辑</Text>
              </Pressable>
            </Animated.View>
          ))
        )}
        {list.length > 0 ? <Text style={styles.hint}>长按一项可删除</Text> : null}
      </ScrollView>
      <GrimoireDock />
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  addBtn: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  addGlyph: { fontSize: 24, color: theme.color.accent, marginTop: -2 },
  row: { flexDirection: "row", alignItems: "center", gap: theme.space(4), padding: theme.space(3), borderRadius: theme.radius.lg, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard },
  sigil: { width: 52, height: 52, borderRadius: theme.radius.md, backgroundColor: theme.color.bgInput, borderWidth: 1, borderColor: theme.color.surfaceLineStrong, alignItems: "center", justifyContent: "center" },
  sigilText: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.accent },
  name: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  identity: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint },
  edit: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1.5, color: theme.color.accent, textTransform: "uppercase" },
  hint: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, textAlign: "center", marginTop: theme.space(3) },
  empty: { alignItems: "center", paddingTop: theme.space(28), paddingHorizontal: theme.space(10), gap: theme.space(3) },
  emptyGlyph: { fontSize: 48, color: theme.color.accent, opacity: 0.6 },
  emptyText: { fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.textFaint, textAlign: "center", lineHeight: 23 },
});
