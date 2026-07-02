/**
 * Grimoire Index — the in-app help compendium. The web client ships 27 module docs from
 * a HelpDrawer; on mobile we carry a curated, offline subset as static entries (no help
 * API exists server-side — docs are bundled). An accordion of collapsible articles, each
 * a short orientation to one system, written to be read on a phone rather than ported
 * wholesale from desktop.
 */
import React, { useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Animated, { FadeIn, FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { theme } from "@/theme/theme";

type Doc = { glyph: string; title: string; body: string };

const DOCS: Doc[] = [
  {
    glyph: "❖",
    title: "酒馆模式 · 1:1 角色对话",
    body: "和某个角色直接对话，而非和 GM。导入 SillyTavern 角色卡（PNG/JSON）即可开聊，或在「新对话」里从你的卡库挑选。右上角 ❖ 打开角色面板，可编辑本对话的系统提示词、绑定 persona、开关沉浸模式。",
  },
  {
    glyph: "⚔",
    title: "游戏控制台 · 剧本世界",
    body: "把一本小说当作可玩世界。在「典籍」（📖）里选一部剧本开启新游戏，或继续已有存档。对话中右上角 ⚔ 打开试炼之书，查看角色状态、5E 战斗、时间线纪年。",
  },
  {
    glyph: "⌥",
    title: "分支存档 · 故事树",
    body: "每一回合都自动提交，像 Git 一样可回溯。对话中点 ⌥ 打开故事树：跳回任意节点重写走向，或从某处另辟新分支。原作不动，岔路各存各的。",
  },
  {
    glyph: "✦",
    title: "记忆典籍",
    body: "引擎每回合都会读取的持久记忆，分五层：固定（始终注入）、手记、事实、资源、能力。点 ✦ 增删条目——把你想让故事记住的事写进去。",
  },
  {
    glyph: "⊛",
    title: "世界典藏 · 实时状态",
    body: "故事的活状态：世相（地点/时刻/天候）、羁绊（NPC 关系）、世界线（剧情变量）。点 ⊛ 直接编辑——每次改动都会同步进引擎。",
  },
  {
    glyph: "◎",
    title: "映象之镜 · 生图",
    body: "对话中点 ◎ 描绘一个场景，引擎会异步生成画面，完成后显示。需要先在「设置 → 模型与密钥」配置支持生图的 provider。",
  },
  {
    glyph: "✶",
    title: "缪斯 · AI 帮回",
    body: "输入框旁的 ✶ 会以你的角色/persona 口吻草拟一条回复，填进输入框（不自动发送）——卡壳时借它起个头。",
  },
  {
    glyph: "⌬",
    title: "模型与密钥 · BYOK",
    body: "自带密钥（Bring Your Own Key）。在「设置 → 模型与密钥」为各 provider 填入 API Key，密钥加密存储。配好后可测试连接、为不同存档指定不同模型。",
  },
  {
    glyph: "◈",
    title: "图库 · Reliquary",
    body: "所有生成与上传的图像聚于此，按封面/头像/场景筛选。点开放大，长按销毁。",
  },
  {
    glyph: "⛨",
    title: "账号与隐私",
    body: "在「设置 → 账号」可改显示名号、导出全部数据、停用账号（可恢复）或请求注销（30 天宽限期，期间可撤回）。",
  },
];

function Article({ doc, index }: { doc: Doc; index: number }) {
  const [open, setOpen] = useState(false);
  return (
    <Animated.View entering={FadeInDown.delay(Math.min(index, 10) * 40).duration(360)} style={styles.article}>
      <Pressable onPress={() => setOpen((v) => !v)} style={styles.artHead}>
        <Text style={styles.artGlyph}>{doc.glyph}</Text>
        <Text style={styles.artTitle}>{doc.title}</Text>
        <Text style={styles.artChevron}>{open ? "▾" : "▸"}</Text>
      </Pressable>
      {open ? (
        <Animated.Text entering={FadeIn.duration(240)} style={styles.artBody}>
          {doc.body}
        </Animated.Text>
      ) : null}
    </Animated.View>
  );
}

export default function HelpScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <Pressable onPress={() => router.back()} hitSlop={12} style={styles.headBtn}>
          <Text style={styles.headGlyph}>‹</Text>
        </Pressable>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Grimoire Index</Text>
          <Text style={styles.h1}>帮助</Text>
        </View>
      </View>

      <ScrollView contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + 50, gap: theme.space(3), paddingTop: theme.space(2) }}>
        <Text style={styles.intro}>点开任一条目，了解该系统如何运作。</Text>
        {DOCS.map((d, i) => (
          <Article key={d.title} doc={d} index={i} />
        ))}
      </ScrollView>
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(1) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  intro: { fontFamily: theme.font.prose, fontSize: theme.size.sm, color: theme.color.textFaint, marginBottom: theme.space(2) },
  article: { borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard, overflow: "hidden" },
  artHead: { flexDirection: "row", alignItems: "center", gap: theme.space(3), padding: theme.space(4) },
  artGlyph: { fontSize: 18, color: theme.color.accent, width: 24, textAlign: "center" },
  artTitle: { flex: 1, fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.text },
  artChevron: { fontSize: 16, color: theme.color.textFaint },
  artBody: { fontFamily: theme.font.prose, fontSize: theme.size.base, color: theme.color.textDim, lineHeight: 24, paddingHorizontal: theme.space(4), paddingBottom: theme.space(4), paddingTop: 0 },
});
