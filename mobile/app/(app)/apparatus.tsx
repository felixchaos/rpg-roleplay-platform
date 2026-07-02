/**
 * Apparatus Workbench — hands-on management of the engine's external machinery: MCP
 * servers and Skill packs. The desktop hides this behind config files; here it's a live
 * bench. MCP servers list with alive-status runes you can start/stop/enable/delete, plus
 * a forge to add or amend one (stdio command + env, or http url + headers). A validate
 * action handshakes the server; admins also see captured stderr in a log scroll.
 *
 * Mutations re-read the runtime so the bench always reflects the broker's true state.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, Modal, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import { BlurView } from "expo-blur";
import Animated, { FadeIn, FadeInDown } from "react-native-reanimated";
import { GrimoireBackdrop } from "@/components/GrimoireBackdrop";
import { GrimoireDock, DOCK_HEIGHT } from "@/components/GrimoireDock";
import { EmberButton, IconLabelButton, RuneDivider } from "@/components/ui";
import { apparatus } from "@/api";
import { ApiError } from "@/api/http";
import { theme, palette } from "@/theme/theme";

type McpServer = {
  id?: string;
  name?: string;
  enabled?: boolean;
  alive?: boolean;
  tools_count?: number;
  command?: string;
  url?: string;
  transport?: "stdio" | "http";
  env?: Record<string, string>;
  headers?: Record<string, string>;
  cwd?: string;
  stderr?: string;
  server_info?: any;
  [k: string]: unknown;
};
type Skill = { id?: string; name?: string; description?: string; [k: string]: unknown };

type Draft = {
  id?: string;
  name: string;
  transport: "stdio" | "http";
  command: string;
  url: string;
  envText: string;
  headersText: string;
  cwd: string;
  enabled: boolean;
};

const emptyDraft = (): Draft => ({
  name: "",
  transport: "stdio",
  command: "",
  url: "",
  envText: "",
  headersText: "",
  cwd: "",
  enabled: true,
});

// KEY=VALUE per line → object. Empty/comment lines skipped.
function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of String(text || "").split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const m = line.match(/^([^=]+)=(.*)$/);
    if (m) out[m[1].trim()] = m[2];
  }
  return out;
}

// Best-effort JSON parse for headers. Falls back to KEY: VALUE per line.
function parseHeaders(text: string): Record<string, string> {
  const t = String(text || "").trim();
  if (!t) return {};
  if (t.startsWith("{")) {
    try {
      const o = JSON.parse(t);
      const out: Record<string, string> = {};
      for (const [k, v] of Object.entries(o || {})) out[String(k)] = String(v);
      return out;
    } catch {
      /* fall through to line form */
    }
  }
  const out: Record<string, string> = {};
  for (const raw of t.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const m = line.match(/^([^:]+):\s*(.*)$/);
    if (m) out[m[1].trim()] = m[2];
  }
  return out;
}

function envToText(env?: Record<string, string>): string {
  if (!env) return "";
  return Object.entries(env).map(([k, v]) => `${k}=${v}`).join("\n");
}

function headersToText(headers?: Record<string, string>): string {
  if (!headers || Object.keys(headers).length === 0) return "";
  return JSON.stringify(headers, null, 2);
}

export default function ApparatusScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [servers, setServers] = useState<McpServer[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [logFor, setLogFor] = useState<McpServer | null>(null);
  const [batchValidating, setBatchValidating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [m, s] = await Promise.allSettled([apparatus.mcpRuntime(), apparatus.skills()]);
      if (m.status === "fulfilled") {
        const r: any = m.value;
        // The runtime endpoint returns running[] (broker state) + the catalog. Servers in
        // the catalog carry the configured shape (env/headers/transport); running[] adds
        // alive/tools_count/stderr. Merge them by id so a single card has both.
        const running: any[] = r?.running ?? r?.servers ?? [];
        const catalog: any[] = r?.catalog ?? r?.items ?? r?.mcp?.servers ?? (Array.isArray(r) ? r : []);
        const merged: McpServer[] = [];
        const seen = new Set<string>();
        for (const c of catalog) {
          const id = String(c.id || c.name || "");
          if (!id) continue;
          seen.add(id);
          const rt = running.find((x: any) => String(x.id || x.name || "") === id) || {};
          merged.push({ ...c, ...rt });
        }
        // Some servers may appear only in running (legacy) — include those too.
        for (const rt of running) {
          const id = String(rt.id || rt.name || "");
          if (!id || seen.has(id)) continue;
          merged.push(rt);
        }
        setServers(merged.length > 0 ? merged : running);
      }
      if (s.status === "fulfilled") setSkills(s.value?.skills ?? []);
    } catch (e) {
      if (e instanceof ApiError && e.status !== 401) Alert.alert("加载失败", e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const act = async (id: string, fn: () => Promise<any>) => {
    setBusyId(id);
    try {
      await fn();
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      load();
    } catch (e) {
      Alert.alert("操作失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setBusyId(null);
    }
  };

  const openAdd = () => setDraft(emptyDraft());
  const openEdit = (s: McpServer) => {
    setDraft({
      id: s.id || s.name,
      name: s.name || "",
      transport: s.transport || (s.url ? "http" : "stdio"),
      command: s.command || "",
      url: s.url || "",
      envText: envToText(s.env),
      headersText: headersToText(s.headers),
      cwd: s.cwd || "",
      enabled: s.enabled !== false,
    });
  };

  const submitDraft = async () => {
    if (!draft) return;
    if (!draft.name.trim()) {
      Alert.alert("信息不全", "请填写装置名。");
      return;
    }
    const target = draft.transport === "http" ? draft.url : draft.command;
    if (!target.trim()) {
      Alert.alert("信息不全", draft.transport === "http" ? "请填写 URL。" : "请填写命令。");
      return;
    }
    setBusyId("__draft__");
    try {
      const body: any = {
        name: draft.name.trim(),
        transport: draft.transport,
        enabled: draft.enabled,
      };
      if (draft.id) body.id = draft.id;
      if (draft.transport === "http") body.url = draft.url.trim();
      else body.command = draft.command.trim();
      if (draft.cwd.trim()) body.cwd = draft.cwd.trim();
      const env = parseEnv(draft.envText);
      if (Object.keys(env).length) body.env = env;
      const headers = parseHeaders(draft.headersText);
      if (Object.keys(headers).length) body.headers = headers;
      await apparatus.upsertMcp(body);
      // For a fresh add, immediately try to validate so the user sees a real result
      if (!draft.id) {
        try { await apparatus.validateMcp(body.name); } catch (_) {}
      }
      setDraft(null);
      load();
    } catch (e) {
      Alert.alert(draft.id ? "保存失败" : "添加失败", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setBusyId(null);
    }
  };

  const deleteServer = (s: McpServer) => {
    const id = s.id || s.name || "";
    Alert.alert("移除装置", `删除 MCP 服务器「${s.name || id}」？`, [
      { text: "取消", style: "cancel" },
      { text: "删除", style: "destructive", onPress: () => act(id, () => apparatus.deleteMcp(id)) },
    ]);
  };

  const validate = async (s: McpServer) => {
    const id = s.id || s.name || "";
    setBusyId(`val:${id}`);
    try {
      const r = await apparatus.validateMcp(id);
      Alert.alert(r.ok ? "握手成功" : "握手失败", r.ok ? "MCP 已能正常列出工具。" : r.error || "请检查命令、env 或 URL。");
      load();
    } catch (e) {
      Alert.alert("验证异常", e instanceof ApiError ? e.message : "请重试");
    } finally {
      setBusyId(null);
    }
  };

  const validateAll = async () => {
    if (servers.length === 0) return;
    setBatchValidating(true);
    let ok = 0, fail = 0;
    for (const s of servers) {
      try {
        const r = await apparatus.validateMcp(s.id || s.name || "");
        if (r.ok) ok++; else fail++;
      } catch { fail++; }
    }
    setBatchValidating(false);
    Alert.alert("巡查完毕", `握手通过 ${ok} 台 · 失败 ${fail} 台`);
    load();
  };

  const runSkill = (sk: Skill) => {
    const id = sk.id || sk.name || "";
    act(`skill:${id}`, async () => {
      const r = await apparatus.runSkill(id);
      Alert.alert("已运行", typeof r?.result === "string" ? r.result.slice(0, 300) : "技能已执行。");
    });
  };

  return (
    <GrimoireBackdrop>
      <View style={[styles.header, { paddingTop: insets.top + theme.space(2) }]}>
        <View style={{ flex: 1 }}>
          <Text style={styles.kicker}>Apparatus</Text>
          <Text style={styles.h1}>装置工坊</Text>
        </View>
        <Pressable onPress={validateAll} hitSlop={6} disabled={batchValidating || servers.length === 0} style={[styles.chip, (servers.length === 0) && { opacity: 0.4 }]}>
          {batchValidating ? <ActivityIndicator size="small" color={theme.color.accent} /> : <Text style={styles.chipLabel}>巡查</Text>}
        </Pressable>
        <IconLabelButton glyph="＋" label="新增" onPress={openAdd} />
      </View>

      {loading ? (
        <ActivityIndicator color={theme.color.accent} style={{ marginTop: theme.space(20) }} />
      ) : (
        <ScrollView contentContainerStyle={{ paddingHorizontal: theme.space(6), paddingBottom: insets.bottom + DOCK_HEIGHT + 30, gap: theme.space(5), paddingTop: theme.space(2) }}>
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>MCP 服务器 · {servers.length}</Text>
            {servers.length === 0 ? (
              <Text style={styles.empty}>尚无 MCP 服务器。点右上角 ＋ 添加一个。</Text>
            ) : (
              servers.map((s, i) => {
                const id = s.id || s.name || String(i);
                const busy = busyId === id || busyId === `val:${id}`;
                const hasStderr = typeof s.stderr === "string" && s.stderr.length > 0;
                return (
                  <Animated.View key={id} entering={FadeInDown.delay(i * 40).duration(340)} style={styles.card}>
                    <View style={styles.cardHead}>
                      <View style={[styles.statusDot, { backgroundColor: s.alive ? palette.jade : theme.color.textFaint }]} />
                      <View style={{ flex: 1 }}>
                        <Text style={styles.cardName}>{s.name || id}</Text>
                        <Text style={styles.cardMeta} numberOfLines={1}>
                          {s.alive ? `运行中 · ${s.tools_count ?? 0} 工具` : "已停止"}
                          {" · "}{s.transport === "http" || s.url ? "HTTP" : "STDIO"}
                          {s.command ? ` · ${s.command}` : s.url ? ` · ${s.url}` : ""}
                        </Text>
                      </View>
                      <Switch
                        value={!!s.enabled}
                        onValueChange={(v) => act(id, () => apparatus.setMcpEnabled(id, v))}
                        disabled={busy}
                        trackColor={{ false: theme.color.bgInput, true: theme.color.accentDeep }}
                        thumbColor={s.enabled ? theme.color.accentBright : theme.color.textFaint}
                      />
                    </View>
                    <View style={styles.cardActions}>
                      {s.alive ? (
                        <Pressable onPress={() => act(id, () => apparatus.stopMcp(id))} disabled={busy} hitSlop={6}><Text style={styles.actionText}>停止</Text></Pressable>
                      ) : (
                        <Pressable onPress={() => act(id, () => apparatus.startMcp(id))} disabled={busy} hitSlop={6}><Text style={styles.actionText}>启动</Text></Pressable>
                      )}
                      <Pressable onPress={() => validate(s)} disabled={busy} hitSlop={6}><Text style={styles.actionText}>验证</Text></Pressable>
                      <Pressable onPress={() => openEdit(s)} disabled={busy} hitSlop={6}><Text style={styles.actionText}>调律</Text></Pressable>
                      {hasStderr ? (
                        <Pressable onPress={() => setLogFor(s)} disabled={busy} hitSlop={6}><Text style={styles.actionText}>日志</Text></Pressable>
                      ) : null}
                      <Pressable onPress={() => deleteServer(s)} disabled={busy} hitSlop={6}><Text style={[styles.actionText, { color: theme.color.danger }]}>删除</Text></Pressable>
                      {busy ? <ActivityIndicator size="small" color={theme.color.accent} /> : null}
                    </View>
                  </Animated.View>
                );
              })
            )}
          </View>

          <RuneDivider />

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>技能 · {skills.length}</Text>
            {skills.length === 0 ? (
              <Text style={styles.empty}>尚无技能包。技能包目前在桌面端导入。</Text>
            ) : (
              skills.map((sk, i) => {
                const id = sk.id || sk.name || String(i);
                const busy = busyId === `skill:${id}`;
                return (
                  <Animated.View key={id} entering={FadeInDown.delay(i * 40).duration(340)} style={styles.card}>
                    <View style={{ flex: 1, gap: 2 }}>
                      <Text style={styles.cardName}>{sk.name || id}</Text>
                      {sk.description ? <Text style={styles.cardMeta} numberOfLines={2}>{sk.description}</Text> : null}
                    </View>
                    <Pressable onPress={() => runSkill(sk)} disabled={busy} style={styles.runBtn}>
                      {busy ? <ActivityIndicator size="small" color={theme.color.bg} /> : <Text style={styles.runText}>运行</Text>}
                    </Pressable>
                  </Animated.View>
                );
              })
            )}
          </View>
        </ScrollView>
      )}

      {/* Draft Sheet — add / edit */}
      <Modal visible={!!draft} transparent animationType="slide" onRequestClose={() => setDraft(null)}>
        <Pressable style={styles.backdrop} onPress={() => setDraft(null)} />
        <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
          <BlurView intensity={36} tint="dark" style={styles.fill} />
          <View style={styles.grabber} />
          <Text style={styles.kicker}>{draft?.id ? "Amend" : "Forge"}</Text>
          <Text style={styles.sheetTitle}>{draft?.id ? "调律装置" : "铸入装置"}</Text>

          <ScrollView style={{ maxHeight: 520 }} contentContainerStyle={{ gap: theme.space(4) }}>
            <View>
              <Text style={styles.fieldLabel}>名称</Text>
              <Text style={styles.fieldHint}>唯一标识。建立后不要改名。</Text>
              <TextInput
                value={draft?.name ?? ""}
                onChangeText={(s) => draft && setDraft({ ...draft, name: s })}
                placeholder="my-tools"
                placeholderTextColor={theme.color.textFaint}
                style={styles.input}
                autoCapitalize="none"
              />
            </View>

            <View>
              <Text style={styles.fieldLabel}>连接方式</Text>
              <View style={styles.segRow}>
                <Pressable
                  onPress={() => draft && setDraft({ ...draft, transport: "stdio" })}
                  style={[styles.segChip, draft?.transport === "stdio" && styles.segChipActive]}
                >
                  <Text style={[styles.segChipLabel, draft?.transport === "stdio" && { color: theme.color.accentBright }]}>stdio</Text>
                </Pressable>
                <Pressable
                  onPress={() => draft && setDraft({ ...draft, transport: "http" })}
                  style={[styles.segChip, draft?.transport === "http" && styles.segChipActive]}
                >
                  <Text style={[styles.segChipLabel, draft?.transport === "http" && { color: theme.color.accentBright }]}>http</Text>
                </Pressable>
              </View>
            </View>

            {draft?.transport === "http" ? (
              <View>
                <Text style={styles.fieldLabel}>URL</Text>
                <Text style={styles.fieldHint}>远程 MCP 端点。</Text>
                <TextInput
                  value={draft?.url ?? ""}
                  onChangeText={(s) => draft && setDraft({ ...draft, url: s })}
                  placeholder="https://localhost:7300"
                  placeholderTextColor={theme.color.textFaint}
                  style={[styles.input, { fontFamily: theme.font.mono }]}
                  autoCapitalize="none"
                  keyboardType="url"
                />
              </View>
            ) : (
              <View>
                <Text style={styles.fieldLabel}>命令</Text>
                <Text style={styles.fieldHint}>启动 stdio MCP 的命令行。</Text>
                <TextInput
                  value={draft?.command ?? ""}
                  onChangeText={(s) => draft && setDraft({ ...draft, command: s })}
                  placeholder="npx -y @scope/server  或  uvx my-mcp"
                  placeholderTextColor={theme.color.textFaint}
                  style={[styles.input, { fontFamily: theme.font.mono }]}
                  autoCapitalize="none"
                />
              </View>
            )}

            {draft?.transport === "stdio" ? (
              <View>
                <Text style={styles.fieldLabel}>工作目录（可选）</Text>
                <Text style={styles.fieldHint}>留空则继承 server 进程的 cwd。</Text>
                <TextInput
                  value={draft?.cwd ?? ""}
                  onChangeText={(s) => draft && setDraft({ ...draft, cwd: s })}
                  placeholder="/srv/my-mcp"
                  placeholderTextColor={theme.color.textFaint}
                  style={[styles.input, { fontFamily: theme.font.mono }]}
                  autoCapitalize="none"
                />
              </View>
            ) : null}

            <View>
              <Text style={styles.fieldLabel}>环境变量</Text>
              <Text style={styles.fieldHint}>每行一条 KEY=VALUE。# 开头作注释。</Text>
              <TextInput
                value={draft?.envText ?? ""}
                onChangeText={(s) => draft && setDraft({ ...draft, envText: s })}
                placeholder={"GITHUB_TOKEN=ghp_xxx\nAPI_BASE=https://api.example.com"}
                placeholderTextColor={theme.color.textFaint}
                style={[styles.input, { fontFamily: theme.font.mono, fontSize: 12, minHeight: 88 }]}
                multiline
                autoCapitalize="none"
              />
            </View>

            {draft?.transport === "http" ? (
              <View>
                <Text style={styles.fieldLabel}>Headers（JSON 或 KEY: VALUE）</Text>
                <Text style={styles.fieldHint}>HTTP 模式才发送。可粘 JSON 也可逐行写。</Text>
                <TextInput
                  value={draft?.headersText ?? ""}
                  onChangeText={(s) => draft && setDraft({ ...draft, headersText: s })}
                  placeholder={'{"Authorization": "Bearer …", "X-Org": "acme"}'}
                  placeholderTextColor={theme.color.textFaint}
                  style={[styles.input, { fontFamily: theme.font.mono, fontSize: 12, minHeight: 88 }]}
                  multiline
                  autoCapitalize="none"
                />
              </View>
            ) : null}

            <View style={styles.toggleRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.fieldLabel}>启用</Text>
                <Text style={styles.fieldHint}>关闭则不进入工具池。</Text>
              </View>
              <Switch
                value={!!draft?.enabled}
                onValueChange={(v) => { if (draft) setDraft({ ...draft, enabled: v }); }}
                trackColor={{ false: theme.color.bgInput, true: theme.color.accentDeep }}
                thumbColor={draft?.enabled ? theme.color.accentBright : theme.color.textFaint}
              />
            </View>
          </ScrollView>

          <View style={{ flexDirection: "row", gap: theme.space(3), marginTop: theme.space(4) }}>
            <EmberButton label="取消" variant="ghost" onPress={() => setDraft(null)} style={{ flex: 1 }} />
            <EmberButton
              label={draft?.id ? "保存" : "铸入并验证"}
              onPress={submitDraft}
              loading={busyId === "__draft__"}
              style={{ flex: 1 }}
            />
          </View>
        </View>
      </Modal>

      {/* Log Sheet — admin stderr */}
      <Modal visible={!!logFor} transparent animationType="slide" onRequestClose={() => setLogFor(null)}>
        <Pressable style={styles.backdrop} onPress={() => setLogFor(null)} />
        <View style={[styles.sheet, { paddingBottom: insets.bottom + theme.space(4) }]}>
          <BlurView intensity={36} tint="dark" style={styles.fill} />
          <View style={styles.grabber} />
          <Text style={styles.kicker}>Smoke</Text>
          <Text style={styles.sheetTitle}>{logFor?.name || "stderr"}</Text>
          <ScrollView style={{ maxHeight: 520 }} contentContainerStyle={{ paddingVertical: theme.space(2) }}>
            <Text style={styles.logText} selectable>
              {logFor?.stderr || "无日志。"}
            </Text>
          </ScrollView>
          <View style={{ flexDirection: "row", gap: theme.space(3), marginTop: theme.space(4) }}>
            <EmberButton label="关闭" variant="ghost" onPress={() => setLogFor(null)} style={{ flex: 1 }} />
          </View>
        </View>
      </Modal>
      <GrimoireDock />
    </GrimoireBackdrop>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", alignItems: "center", paddingHorizontal: theme.space(4), paddingBottom: theme.space(3), gap: theme.space(2) },
  headBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  headGlyph: { fontSize: 34, color: theme.color.textDim, marginTop: -4 },
  kicker: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 4, textTransform: "uppercase", color: theme.color.accent },
  h1: { fontFamily: theme.font.display, fontSize: theme.size.xl, color: theme.color.text, letterSpacing: 1 },
  chip: { paddingHorizontal: theme.space(3), paddingVertical: theme.space(2), borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard, alignItems: "center", justifyContent: "center" },
  chipLabel: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1, color: theme.color.accent, textTransform: "uppercase" },
  addBtn: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderRadius: theme.radius.pill, borderWidth: 1, borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  addGlyph: { fontSize: 24, color: theme.color.accent, marginTop: -2 },

  section: { gap: theme.space(3) },
  sectionTitle: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 3, textTransform: "uppercase", color: theme.color.accent },
  card: { padding: theme.space(4), borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard, gap: theme.space(3) },
  cardHead: { flexDirection: "row", alignItems: "center", gap: theme.space(3) },
  statusDot: { width: 10, height: 10, borderRadius: 5 },
  cardName: { fontFamily: theme.font.proseSemi, fontSize: theme.size.md, color: theme.color.text },
  cardMeta: { fontFamily: theme.font.mono, fontSize: theme.size.xs, color: theme.color.textFaint },
  cardActions: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(4), alignItems: "center" },
  actionText: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.accent },
  runBtn: { paddingHorizontal: theme.space(4), paddingVertical: theme.space(2), borderRadius: theme.radius.pill, backgroundColor: theme.color.accent, alignItems: "center", justifyContent: "center" },
  runText: { fontFamily: theme.font.displaySemi, fontSize: theme.size.xs, letterSpacing: 1, color: theme.color.bg, textTransform: "uppercase" },
  empty: { fontFamily: theme.font.proseItalic, fontSize: theme.size.base, color: theme.color.textFaint, lineHeight: 22, paddingVertical: theme.space(2) },

  // Sheets
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
  fieldLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.base, color: theme.color.text },
  fieldHint: { fontFamily: theme.font.prose, fontSize: theme.size.xs, color: theme.color.textFaint, marginTop: 2, marginBottom: theme.space(2), lineHeight: 17 },
  input: { backgroundColor: theme.color.bgInput, borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine, paddingHorizontal: theme.space(3), paddingVertical: theme.space(3), color: theme.color.text, fontFamily: theme.font.prose, fontSize: theme.size.sm },
  segRow: { flexDirection: "row", gap: theme.space(2) },
  segChip: { flex: 1, paddingVertical: theme.space(2), paddingHorizontal: theme.space(3), borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.surfaceLine, backgroundColor: theme.color.bgCard, alignItems: "center" },
  segChipActive: { borderColor: theme.color.accentSoft, backgroundColor: theme.color.accentGhost },
  segChipLabel: { fontFamily: theme.font.proseSemi, fontSize: theme.size.sm, color: theme.color.textDim, letterSpacing: 0.5 },
  toggleRow: { flexDirection: "row", alignItems: "center", gap: theme.space(3) },

  logText: { fontFamily: theme.font.mono, fontSize: 11, lineHeight: 16, color: palette.parchmentDim },
});
