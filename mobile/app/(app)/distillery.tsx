/**
 * Distillery — 蒸馏所. Persona-skill management. A "persona skill" here is a markdown
 * profile (uploaded .md or fetched from a public GitHub repo) that the engine distills
 * into a regular character card. Different from executable skill packs (those are admin-
 * only and live in the apparatus workbench).
 *
 * Aesthetic direction: refined Candlelit Grimoire. Each row is a sealed flask on the
 * distiller's rack — a small numeric tag, the distilled character name in display caps,
 * the source (github URL or upload note) in muted serif, and a quiet ⌫ eject. The top
 * of the rack carries two action wells: paste a GitHub URL, or pick a .md file from disk.
 *
 * Backend: GET /api/me/persona-skills · POST /api/me/persona-skills/import · POST
 * /api/me/persona-skills/{id}/delete.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { BlurView } from "expo-blur";
import * as DocumentPicker from "expo-document-picker";
import { File as ExpoFile } from "expo-file-system";
import Animated, { FadeIn, FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { EmberButton } from "@/components/ui";
import { personaSkills } from "@/api";
import { ApiError } from "@/api/http";
import { theme, palette } from "@/theme/theme";

type PersonaSkill = {
  id?: number | string;
  name?: string;
  source?: string;
  source_url?: string;
  source_type?: "upload" | "github";
  created_at?: string;
  card_id?: number;
  image_url?: string;
  [k: string]: unknown;
};

type ImportMode = "github" | "upload";

export default function DistilleryScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [items, setItems] = useState<PersonaSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [importOpen, setImportOpen] = useState(false);
  const [mode, setMode] = useState<ImportMode>("github");
  const [repoUrl, setRepoUrl] = useState("");
  const [pickedFile, setPickedFile] = useState<{ name: string; content: string } | null>(null);
  const [genImage, setGenImage] = useState(true);
  const [useLlm, setUseLlm] = useState(false);
  const [importing, setImporting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await personaSkills.list();
      setItems(r?.items ?? []);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const pickMarkdown = async () => {
    try {
      const res = await DocumentPicker.getDocumentAsync({
        type: ["text/markdown", "text/plain", "*/*"],
        copyToCacheDirectory: true,
      });
      if (res.canceled || !res.assets?.[0]) return;
      const asset = res.assets[0];
      if (!/\.(md|markdown|txt)$/i.test(asset.name || "")) {
        Alert.alert("文件类型不符", "请选择 .md 或 .markdown 文件。");
        return;
      }
      const content = await new ExpoFile(asset.uri).text();
      setPickedFile({ name: asset.name, content });
    } catch (e) {
      Alert.alert("读取失败", e instanceof Error ? e.message : "请重试");
    }
  };

  const submitImport = async () => {
    setImporting(true);
    try {
      const body: any = {
        source: mode,
        generate_image: genImage,
        use_llm: useLlm,
      };
      if (mode === "github") {
        const url = repoUrl.trim();
        if (!url) { Alert.alert("缺少 URL", "请粘贴一个 GitHub 公开仓库链接。"); setImporting(false); return; }
        body.repo_url = url;
      } else {
        if (!pickedFile) { Alert.alert("未选择文件", "请先选择一个 .md 卷轴。"); setImporting(false); return; }
        body.files = [pickedFile];
      }
      const r = await personaSkills.import(body);
      if (!r?.ok) {
        Alert.alert("导入失败", r?.error || "请检查 URL 或文件格式");
      } else {
        const name = r.card?.name || "新的人格";
        const imgNote = r.image_status === "queued" ? "（人设图已排入生成队列）" : "";
        Alert.alert("已蒸馏", `「${name}」已加入你的卡库。${imgNote}`);
        setImportOpen(false);
        setRepoUrl("");
        setPickedFile(null);
        load();
      }
    } catch (e) {
      Alert.alert("导入失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setImporting(false);
    }
  };

  const remove = (sk: PersonaSkill) => {
    const id = sk.id;
    if (!id) return;
    Alert.alert("蒸去此条", `从蒸馏所移除「${sk.name || id}」？`, [
      { text: "取消", style: "cancel" },
      {
        text: "蒸去",
        style: "destructive",
        onPress: async () => {
          try {
            await personaSkills.remove(id);
            load();
          } catch (e) {
            Alert.alert("失败", e instanceof ApiError ? e.message : "请重试");
          }
        },
      },
    ]);
  };

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Distillery</Text>
          <Text style={styles.h1}>蒸馏所 · 人格技能</Text>
        </View>
        <Pressable onPress={() => setImportOpen(true)} hitSlop={12} style={styles.addBtn}>
          <Text style={styles.addGlyph}>＋</Text>
        </Pressable>
      </View>

      {loading ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <ScrollView contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 60, gap: theme.space(4), paddingTop: theme.space(2) }}>
          <Animated.View entering={FadeIn.duration(500)}>
            <Text style={styles.introText}>
              将一段 markdown 人物档案蒸馏为一张角色卡。可粘 GitHub 公开仓库链接，也可从手机本地选 .md 卷轴。
            </Text>
          </Animated.View>

          {items.length === 0 ? (
            <View style={styles.empty}>
              <Text style={styles.emptyGlyph}>⚗</Text>
              <Text style={styles.emptyTitle}>蒸馏所空架</Text>
              <Text style={styles.emptyText}>右上角 ＋ 召唤一卷你心爱的人格档案，让司笔灵替你拆解。</Text>
            </View>
          ) : (
            items.map((sk, i) => {
              const sourceType = sk.source_type || (sk.source_url ? "github" : "upload");
              return (
                <Animated.View key={String(sk.id ?? i)} entering={FadeInDown.delay(i * 40).duration(360)} style={styles.flask}>
                  <View style={styles.flaskTag}>
                    <Text style={styles.flaskTagNum}>{String(i + 1).padStart(2, "0")}</Text>
                  </View>
                  <View style={{ flex: 1, gap: theme.space(1) }}>
                    <Text style={styles.flaskName} numberOfLines={1}>{sk.name || "未命名人格"}</Text>
                    <View style={styles.sourceRow}>
                      <Text style={styles.sourceTag}>
                        {sourceType === "github" ? "GitHub" : "Markdown"}
                      </Text>
                      <Text style={styles.sourceText} numberOfLines={1}>
                        {sk.source_url || sk.source || "已加载"}
                      </Text>
                    </View>
                  </View>
                  <Pressable onPress={() => remove(sk)} hitSlop={10}>
                    <Text style={styles.eject}>⌫</Text>
                  </Pressable>
                </Animated.View>
              );
            })
          )}
        </ScrollView>
      )}

      {/* Import sheet */}
      <Modal visible={importOpen} transparent animationType="slide" onRequestClose={() => setImportOpen(false)}>
        <Pressable style={styles.backdrop} onPress={() => setImportOpen(false)} />
        <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
          <BlurView intensity={36} tint="dark" style={styles.fill} />
          <View style={styles.grabber} />
          <Text style={styles.kicker}>Distillation</Text>
          <Text style={styles.sheetTitle}>召唤新人格</Text>

          <View style={styles.segRow}>
            <Pressable onPress={() => setMode("github")} style={[styles.segChip, mode === "github" && styles.segChipActive]}>
              <Text style={[styles.segChipLabel, mode === "github" && { color: theme.color.accentBright }]}>GitHub URL</Text>
            </Pressable>
            <Pressable onPress={() => setMode("upload")} style={[styles.segChip, mode === "upload" && styles.segChipActive]}>
              <Text style={[styles.segChipLabel, mode === "upload" && { color: theme.color.accentBright }]}>本地 .md</Text>
            </Pressable>
          </View>

          <ScrollView style={{ maxHeight: 380 }} contentContainerStyle={{ gap: theme.space(4), paddingTop: theme.space(2) }}>
            {mode === "github" ? (
              <View>
                <Text style={styles.fieldLabel}>GitHub 仓库地址</Text>
                <Text style={styles.fieldHint}>公开仓库即可。会取顶层的 README 或 character.md 作为蓝本。</Text>
                <TextInput
                  value={repoUrl}
                  onChangeText={setRepoUrl}
                  placeholder="https://github.com/owner/persona-repo"
                  placeholderTextColor={theme.color.textFaint}
                  style={styles.input}
                  autoCapitalize="none"
                  keyboardType="url"
                />
              </View>
            ) : (
              <View>
                <Text style={styles.fieldLabel}>本地 markdown 卷轴</Text>
                <Text style={styles.fieldHint}>支持 .md / .markdown / .txt。整段内容会被发到服务端。</Text>
                <Pressable onPress={pickMarkdown} style={({ pressed }) => [styles.pickerBox, pressed && { opacity: 0.85 }]}>
                  <Text style={styles.pickerGlyph}>{pickedFile ? "✓" : "✶"}</Text>
                  <Text style={styles.pickerLabel} numberOfLines={1}>
                    {pickedFile ? pickedFile.name : "点按选择 .md 文件"}
                  </Text>
                </Pressable>
              </View>
            )}

            <View style={styles.toggleRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.fieldLabel}>同时生成人设图</Text>
                <Text style={styles.fieldHint}>导入后排入图像生成队列。需要 image provider 已配置。</Text>
              </View>
              <Switch
                value={genImage}
                onValueChange={setGenImage}
                trackColor={{ false: theme.color.bgInput, true: theme.color.accentDeep }}
                thumbColor={genImage ? theme.color.accentBright : theme.color.textFaint}
              />
            </View>

            <View style={styles.toggleRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.fieldLabel}>用 LLM 蒸馏</Text>
                <Text style={styles.fieldHint}>关闭则只做基础解析；开启会用主 GM 模型重写字段。</Text>
              </View>
              <Switch
                value={useLlm}
                onValueChange={setUseLlm}
                trackColor={{ false: theme.color.bgInput, true: theme.color.accentDeep }}
                thumbColor={useLlm ? theme.color.accentBright : theme.color.textFaint}
              />
            </View>
          </ScrollView>

          <View style={{ flexDirection: "row", gap: theme.space(3), marginTop: theme.space(4) }}>
            <EmberButton label="取消" variant="ghost" onPress={() => setImportOpen(false)} style={{ flex: 1 }} />
            <EmberButton label="开始蒸馏" onPress={submitImport} loading={importing} style={{ flex: 1 }} />
          </View>
        </View>
      </Modal>
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

  introText: { fontFamily: theme.font.proseItalic, fontSize: theme.size.sm, color: theme.color.textFaint, lineHeight: 22 },

  empty: { alignItems: "center", paddingTop: theme.space(16), paddingHorizontal: theme.space(8), gap: theme.space(3) },
  emptyGlyph: { fontSize: 56, color: theme.color.accent, opacity: 0.5 },
  emptyTitle: { fontFamily: theme.font.display, fontSize: theme.size.lg, color: theme.color.textDim, letterSpacing: 1 },
  emptyText: { fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.textFaint, textAlign: "center", lineHeight: 22 },

  flask: {
    flexDirection: "row",
    alignItems: "center",
    gap: theme.space(3),
    padding: theme.space(4),
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.color.surfaceLine,
    backgroundColor: theme.color.bgCard,
  },
  flaskTag: { width: 36, height: 36, borderRadius: 18, borderWidth: 1, borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost, alignItems: "center", justifyContent: "center" },
  flaskTagNum: { fontFamily: theme.font.mono, fontSize: 13, color: theme.color.accent, letterSpacing: 1 },
  flaskName: { fontFamily: theme.font.display, fontSize: theme.size.md, color: theme.color.text, letterSpacing: 0.5 },
  sourceRow: { flexDirection: "row", alignItems: "center", gap: theme.space(2) },
  sourceTag: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1.5, textTransform: "uppercase", color: theme.color.accent, paddingHorizontal: 6, paddingVertical: 2, borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.accentSoft },
  sourceText: { flex: 1, fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint },
  eject: { fontSize: 22, color: theme.color.danger, paddingHorizontal: theme.space(2) },

  // Sheet
  fill: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: theme.color.scrim },
  sheet: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    maxHeight: "92%",
    backgroundColor: "rgba(18,14,10,0.96)",
    borderTopLeftRadius: theme.radius.xl, borderTopRightRadius: theme.radius.xl,
    borderWidth: 1, borderColor: theme.color.surfaceLineStrong,
    overflow: "hidden",
    paddingHorizontal: theme.space(5), paddingTop: theme.space(3),
  },
  grabber: { alignSelf: "center", width: 44, height: 4, borderRadius: 2, backgroundColor: theme.color.surfaceLineStrong, marginBottom: theme.space(2) },
  sheetTitle: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1, marginBottom: theme.space(4) },
  segRow: { flexDirection: "row", gap: theme.space(2) },
  segChip: { flex: 1, paddingVertical: theme.space(3), paddingHorizontal: theme.space(3), borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard, alignItems: "center" },
  segChipActive: { borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  segChipLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.textDim, letterSpacing: 0.5 },
  fieldLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.text },
  fieldHint: { fontFamily: theme.font.prose, fontSize: theme.size.xs, color: theme.color.textFaint, marginTop: 2, marginBottom: theme.space(2), lineHeight: 17 },
  input: { backgroundColor: theme.color.bgInput, borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine, paddingHorizontal: theme.space(3), paddingVertical: theme.space(3), color: theme.color.text, fontFamily: theme.font.mono, fontSize: theme.size.sm },
  pickerBox: { flexDirection: "row", alignItems: "center", gap: theme.space(3), paddingVertical: theme.space(4), paddingHorizontal: theme.space(4), borderRadius: theme.radius.md, borderWidth: 1, borderStyle: "dashed", borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  pickerGlyph: { fontSize: 24, color: theme.color.accent },
  pickerLabel: { flex: 1, fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.text },
  toggleRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3), paddingTop: theme.space(2) },
});
