/* 剧本导入向导 + 导入任务横幅 / 结果 / 预算视图。
   从 pages/scripts.jsx 拆出,JSX / props 流逐字节不变。 */

import React from 'react';
import { useState as useStatePL, useCallback as useCallbackPL } from 'react';
import { useTranslation } from 'react-i18next';
import { plNavigate } from '../../router.js';
import { credApiIdSet } from '../catalog-helpers.js';
import { isCredentialsError } from '../../lib/creds.js';
import { lsGetJSON, lsSetJSON, lsRemove } from '../../lib/storage.js';
import AgentModelPicker from '../AgentModelPicker.jsx';
import { SPLIT_RULES, IMPORT_JOB_TERMINAL_STATUSES } from './shared.js';
import { ImportJobBanner, ImportJobResult, ImportEstimateView } from './import-job-views.jsx';
import CSHeader from '@cloudscape-design/components/header';
import CSContainer from '@cloudscape-design/components/container';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSButton from '@cloudscape-design/components/button';
import CSBox from '@cloudscape-design/components/box';
import CSFormField from '@cloudscape-design/components/form-field';
import CSInput from '@cloudscape-design/components/input';
import CSSelect from '@cloudscape-design/components/select';
import CSToggle from '@cloudscape-design/components/toggle';
import CSFileUpload from '@cloudscape-design/components/file-upload';
import CSKeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import CSAlert from '@cloudscape-design/components/alert';
import CSProgressBar from '@cloudscape-design/components/progress-bar';
import CSColumnLayout from '@cloudscape-design/components/column-layout';

const PENDING_IMPORT_KEY = "rpg.import.pendingImport";
const PENDING_IMPORT_PIPELINE_KEY = "rpg.import.pendingPipeline";

// 统一到共享 isCredentialsError(lib/creds.js):它检测同样的 code/error_key/needs_credentials
// (含 payload)并额外覆盖错误信息字符串里夹带的信号,是原实现的超集。保留本地别名以免改全部调用点。
const isCredentialsRequiredError = isCredentialsError;

function isExpiredUploadError(e) {
  const text = [
    e?.message,
    e?.error,
    e?.detail,
    e?.payload?.error,
    e?.payload?.detail,
    e?.payload?.message,
  ].filter(Boolean).join(" ");
  return /upload_id.*(不存在|过期|expired|not found)|uploaded file.*(expired|missing)/i.test(text);
}

const IMPORT_STAGES = [
  { id: "split",    labelKey: "scripts.import.stage_split",    hintKey: "scripts.import.stage_split_hint",    tok_per_chap: 0 },
  { id: "save",     labelKey: "scripts.import.stage_save",     hintKey: "scripts.import.stage_save_hint",     tok_per_chap: 0 },
  // extract(知识库人物 arc 抽取)按 arc 算法真实标定:后端 extract/budget.py arc ≈ 1.16M/507章
  // ≈ 2280 tok/章(in+out)。原值 120 严重低估 ~18 倍(群反馈:400万字只预估十几万 token)。
  { id: "extract",  labelKey: "scripts.import.stage_extract",  hintKey: "scripts.import.stage_extract_hint",  tok_per_chap: 2280 },
  { id: "card",     labelKey: "scripts.import.stage_card",     hintKey: "scripts.import.stage_card_hint",     tok_per_chap: 60 },
  { id: "world",    labelKey: "scripts.import.stage_world",    hintKey: "scripts.import.stage_world_hint",    tok_per_chap: 90 },
  { id: "timeline", labelKey: "scripts.import.stage_timeline", hintKey: "scripts.import.stage_timeline_hint", tok_per_chap: 40 },
];

function ScriptsImportView({ embedded = false, onClose } = {}) {
  void onClose;
  const { t } = useTranslation();
  const [rule, setRule] = useStatePL("auto");
  const [pattern, setPattern] = useStatePL("");
  const [title, setTitle] = useStatePL("");
  const [job, setJob] = useStatePL(null); // { id, status, stages, currentStage, file, ... } | null
  const [estimate, setEstimate] = useStatePL(null);
  const [previewBusy, setPreviewBusy] = useStatePL(false);
  const [previewProgress, setPreviewProgress] = useStatePL({ value: 0, label: "" });
  const [importBusy, setImportBusy] = useStatePL(false);
  const [importProgress, setImportProgress] = useStatePL("");
  const [importPercent, setImportPercent] = useStatePL(0);
  const [selectedFile, setSelectedFile] = useStatePL(null);
  const [dragOver, setDragOver] = useStatePL(false);
  const [pendingImport, setPendingImport] = useStatePL(null);
  const [pendingPipeline, setPendingPipeline] = useStatePL(null);
  // 拆书流水线 LLM 选择(写入 user prefs.extractor.*,后端 _resolve_extractor_llm 读)
  const [extractApiId, setExtractApiId] = useStatePL('');
  const [extractModel, setExtractModel] = useStatePL('');
  // 完整流水线开关 — 之前 3 处 importPipeline() 都硬编码 true,UI 上没暴露
  // 让用户能关掉(只导入章节/索引,不调 LLM 生 NPC 角色卡/世界书)
  const [enableCards, setEnableCards] = useStatePL(true);
  const [enableWorldbook, setEnableWorldbook] = useStatePL(true);
  const [extractApis, setExtractApis] = useStatePL([]);
  const [credApiIds, setCredApiIds] = useStatePL(new Set()); // 用户已配 key 的 api_id 集合
  const [extractSaving, setExtractSaving] = useStatePL(false);
  // embedder preflight — 导入前检查向量嵌入是否已配置。
  // null = 未加载; {ok, effective_source, preflight:{...}} = 已加载。
  const [embedderStatus, setEmbedderStatus] = useStatePL(null);
  const fileInputRef = React.useRef(null);
  const tickRef = React.useRef(null);

  // 拉 catalog + user prefs + 已配凭证 + embedder preflight,预填提取模型选择
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [profile, models, creds, embedSt] = await Promise.all([
          window.api.account.profile().catch(() => ({})),
          window.api.models.list().catch(() => ({})),
          window.api.credentials.list().catch(() => ({ items: [] })),
          fetch(`${window.__API_BASE || ""}/api/me/embedder/status`, { credentials: 'include' })
            .then(r => r.json()).catch(() => null),
        ]);
        if (cancelled) return;
        const list = models?.models?.apis || (Array.isArray(models?.apis) ? models.apis : []) || [];
        setExtractApis(Array.isArray(list) ? list : []);
        // AgentPlatform 是 Vertex 的 SA 凭证 — UI 里用 vertex_ai
        // ids 曾是裸引用(未定义)→ ReferenceError 被下面的 catch(_){} 整段吞掉,
        // 于是 setExtractApiId / setExtractModel / setEmbedderStatus 全都没执行过。
        const ids = credApiIdSet(creds);
        setCredApiIds(ids);
        const p = (profile && profile.preferences) || {};
        // 默认值优先级:用户 prefs > deepseek(如果已配) > 用户第一个已配的 provider
        const preferred = p['extractor.api_id']
          || (ids.has('deepseek') ? 'deepseek' : null)
          || Array.from(ids)[0]
          || 'deepseek';
        setExtractApiId(preferred);
        setExtractModel(p['extractor.model_real_name'] || '');
        if (embedSt?.ok) setEmbedderStatus(embedSt);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const persistExtractor = async (apiId, model) => {
    if (!apiId || !model) return;
    setExtractSaving(true);
    try {
      await window.api.account.preferences({
        'extractor.api_id': apiId,
        'extractor.model_real_name': model,
      });
    } catch (_) {} finally { setExtractSaving(false); }
  };

  // Restore job from localStorage on mount (page-refresh resilient)
  React.useEffect(() => {
    const j = lsGetJSON("rpg.import.job", null);
    if (j && j.status === "running") setJob(j);
    else if (j && j.status === "estimating") setJob(j);
  }, []);

  // Persist job state
  React.useEffect(() => {
    if (job) lsSetJSON("rpg.import.job", job);
    else lsRemove("rpg.import.job");
  }, [job]);

  React.useEffect(() => {
    const item = lsGetJSON(PENDING_IMPORT_KEY, null);
    if (item && item.upload_id) setPendingImport(item);
    const pipe = lsGetJSON(PENDING_IMPORT_PIPELINE_KEY, null);
    if (pipe && pipe.script_id) setPendingPipeline(pipe);
  }, []);

  const persistPendingImport = useCallbackPL((item) => {
    if (!item || !item.upload_id) return;
    const payload = { ...item, updated_at: Date.now() };
    setPendingImport(payload);
    lsSetJSON(PENDING_IMPORT_KEY, payload);
  }, []);

  const clearPendingImport = useCallbackPL(() => {
    setPendingImport(null);
    lsRemove(PENDING_IMPORT_KEY);
  }, []);

  const persistPendingPipeline = useCallbackPL((item) => {
    if (!item || !item.script_id) return;
    const payload = { ...item, updated_at: Date.now() };
    setPendingPipeline(payload);
    lsSetJSON(PENDING_IMPORT_PIPELINE_KEY, payload);
  }, []);

  const clearPendingPipeline = useCallbackPL(() => {
    setPendingPipeline(null);
    lsRemove(PENDING_IMPORT_PIPELINE_KEY);
  }, []);

  const cancelUploadQuietly = useCallbackPL((uploadId) => {
    if (!uploadId) return;
    try { window.api.uploads.cancel(uploadId).catch(() => {}); } catch (_) {}
  }, []);

  const discardEstimate = useCallbackPL((notify = false) => {
    const oldUploadId = estimate?.upload_id;
    if (oldUploadId) cancelUploadQuietly(oldUploadId);
    setEstimate(null);
    setPreviewProgress({ value: 0, label: "" });
    if (notify) {
      window.__apiToast?.(t('scripts.import.preview_invalidated'), {
        kind: "info",
        detail: t('scripts.import.preview_invalidated_detail'),
        duration: 2600,
      });
    }
  }, [estimate, cancelUploadQuietly, t]);

  // 任务真实进度完全由 ImportJobBanner 内部订阅的 SSE 推上来。
  // wizard 这一层不再:
  //  - 轮询 jobStatus 然后把 stages 全部强写 done (那是撒谎)
  //  - 用 setInterval + Math.random 跑 mock tick (那是更撒谎)
  //  - 接 demo / 离线模式 (没有"离线"路径,要么真上传成功要么 toast 失败)
  // 这里只在 mount 时清掉历史残留 tickRef,防御性兜底。
  React.useEffect(() => {
    if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
    return () => { if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; } };
  }, []);

  // task 49：原 fakeFile = {chapters: 162, words: 410_000} 是凭空写的"示例规模"，
  // 不选文件时会展示出来误导用户。删除 fakeFile，未选文件时 startEstimate 直接
  // 提示"请先选择本地文件"，不假装真实，不生成假预算。

  const onPickFile = (file) => {
    if (!file) return;
    // task 141: 测试期只允许 .txt / .md 剧本文本,前端二次校验(配合后端 ext 白名单)
    const name = (file.name || "").toLowerCase();
    if (!/\.(txt|md)$/.test(name)) {
      window.__apiToast?.(t('scripts.page.file_type_unsupported'), { kind: "danger", detail: t('scripts.page.file_type_unsupported_detail'), duration: 2800 });
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      window.__apiToast?.(t('scripts.import.file_too_large'), { kind: "danger", detail: t('scripts.import.file_max_size'), duration: 2400 });
      return;
    }
    discardEstimate(false);
    clearPendingImport();
    setSelectedFile(file);
    setPreviewProgress({ value: 0, label: "" });
    if (!title) setTitle(file.name.replace(/\.(txt|md)$/i, ""));
  };

  const onDrop = (e) => {
    e.preventDefault(); setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) onPickFile(f);
  };

  const uploadFileChunks = async (file, onProgress) => {
    // 每片会 base64 编码后再 POST(膨胀 ~1.37×)+ JSON 包裹。512KB raw → ~700KB body,
    // 稳稳低于 nginx 默认 client_max_body_size=1MB。原来 1MB raw → ~1.4MB body 会被默认
    // nginx 直接拒/掐连接,浏览器表现为「网络异常 Failed to fetch」—— 自建/开源用户必踩。
    const CHUNK_SIZE = 512 * 1024;
    const totalBytes = file.size;
    const totalChunks = Math.max(1, Math.ceil(totalBytes / CHUNK_SIZE));
    onProgress?.({ stage: "init", done: 0, total: totalChunks, percent: 0 });
    const init = await window.api.uploads.init({
      filename: file.name,
      total_bytes: totalBytes,
      total_chunks: totalChunks,
    });
    const uploadId = init.upload_id || init.id;
    if (!uploadId) throw new Error(t('scripts.import.no_upload_id'));
    for (let i = 0; i < totalChunks; i++) {
      const blob = file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
      await window.api.uploads.chunk(uploadId, blob, i);
      onProgress?.({ stage: "chunk", done: i + 1, total: totalChunks, percent: Math.round(((i + 1) / totalChunks) * 100) });
    }
    onProgress?.({ stage: "finish", done: totalChunks, total: totalChunks, percent: 100 });
    await window.api.uploads.finish(uploadId, {});
    return uploadId;
  };

  // 只构造 label/hint 占位 — SSE 还没推第一帧时 banner 空表很难看。
  // 注意:这里绝对不允许塞 status/progress/tokens_used 的初值,以免在 SSE
  // 推 stage 真实状态前就显示出"已 running"或"已 done"的虚假进度。
  // 真实 status / stage_progress / stage_total / tokens 全部由 SSE 推上来。
  const buildRunningStages = (baseStages) => {
    const source = Array.isArray(baseStages) && baseStages.length
      ? baseStages
      : IMPORT_STAGES.map(s => ({
          id: s.id,
          label: t(s.labelKey),
          hint: t(s.hintKey),
        }));
    return source.map((s) => ({
      id: s.id,
      label: s.label,
      hint: s.hint,
    }));
  };

  const goApiSettings = () => {
    if (pendingImport) persistPendingImport(pendingImport);
    if (pendingPipeline) persistPendingPipeline(pendingPipeline);
    plNavigate("settings-models");
  };

  const resumePendingPipeline = async () => {
    if (!pendingPipeline?.script_id || importBusy) return;
    setImportBusy(true);
    setImportPercent(0);
    setImportProgress(t('scripts.import.pipeline_resuming'));
    try {
      const resp = await window.api.scripts.importPipeline(pendingPipeline.script_id, {
        enable_cards: enableCards,
        enable_worldbook: enableWorldbook,
        budget: pendingPipeline.budget || {},
      });
      if (!resp || resp.ok === false || !resp.job_id) {
        throw new Error((resp && (resp.error || resp.detail)) || t('scripts.import.api_fail'));
      }
      const stages = buildRunningStages(pendingPipeline.stages);
      setJob({
        id: resp.job_id,
        file: pendingPipeline.file || { name: pendingPipeline.file_name || pendingPipeline.title || "script" },
        title: pendingPipeline.title || pendingPipeline.file_name || "script",
        script_id: pendingPipeline.script_id,
        mode: pendingPipeline.mode || "",
        stages,
        totalTokens: pendingPipeline.totalTokens || 0,
        status: "running",
        started_at: Date.now(),
        real: true,
      });
      clearPendingPipeline();
      window.__apiToast?.(t('scripts.import.pipeline_resumed'), { kind: "ok", duration: 2600 });
      // 不写 setImportPercent(100):流水线是 banner 内部 SSE 接管,不再扯 importPercent。
      // 这里只关掉局部 wizard 的 busy 态,banner 单独显示。
    } catch (e) {
      if (isCredentialsRequiredError(e)) {
        const payload = e?.payload || {};
        persistPendingPipeline({ ...pendingPipeline, api_id: payload.api_id, model: payload.model, credential_api_id: payload.credential_api_id });
        window.__apiToast?.(t('scripts.import.api_key_required_title'), {
          kind: "warn",
          detail: t('scripts.import.api_key_required_toast'),
          duration: 5000,
        });
      } else {
        const detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('scripts.toast.unknown_error');
        window.__apiToast?.(t('scripts.import.pipeline_resume_fail'), { kind: "danger", detail, duration: 5000 });
      }
    } finally {
      setImportBusy(false);
      setImportPercent(0);
      setImportProgress("");
    }
  };

  const startEstimate = async () => {
    if (previewBusy || importBusy) return;
    setPreviewBusy(true);
    setEstimate(null);
    setPreviewProgress({ value: 0, label: t('scripts.import.preview_upload_init') });
    // task 49：不选文件时彻底不出预算（之前给假的 162 章 41 万字）
    if (!selectedFile) {
      setEstimate({
        file: null, chapters: 0, words: 0,
        stages: [], totalTokens: 0, totalSec: 0, cost: 0,
        model: "—",
        warnings: [t('scripts.import.warn_no_file')],
        previewError: t('scripts.import.no_file_selected'),
      });
      setPreviewBusy(false);
      setPreviewProgress({ value: 0, label: "" });
      return;
    }
    // 选了真实文件：必须打真后端；失败就给用户看清楚错误，绝不回退 fakeFile
    let result = null;
    let uploadId = null;
    try {
      uploadId = await uploadFileChunks(selectedFile, ({ stage, done, total, percent }) => {
        if (stage === "init") {
          setPreviewProgress({ value: 2, label: t('scripts.import.preview_upload_init') });
        } else if (stage === "chunk") {
          setPreviewProgress({
            value: Math.min(80, 2 + Math.round(percent * 0.78)),
            label: t('scripts.import.preview_upload_progress', { done, total }),
          });
        } else if (stage === "finish") {
          setPreviewProgress({ value: 84, label: t('scripts.import.preview_upload_finish') });
        }
      });
      setPreviewProgress({ value: 90, label: t('scripts.import.preview_analyzing') });
      const body = {
        upload_id: uploadId,
        split_rule: rule || "auto",
        custom_pattern: pattern || "",
        sample_limit: 20,
      };
      result = await window.api.scripts.preview(body);
      setPreviewProgress({ value: 100, label: t('scripts.import.preview_done') });
    } catch (e) {
      if (uploadId) { try { await window.api.uploads.cancel(uploadId); } catch (_) {} }
      let detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('scripts.toast.unknown_error');
      // 网络级失败(fetch 直接抛,没拿到响应)对自建/反代用户最常见的原因是反向代理
      // (nginx/caddy)的请求体积上限太小,或后端没起。给一句可操作的提示,别让用户只看到
      // 一个无解的「Failed to fetch」。
      const isNetErr = (e && (e.code === 'network' || e.status === 0)) || /Failed to fetch|NetworkError|网络异常/i.test(String(detail));
      if (isNetErr) {
        detail = `${detail} —— ${t('scripts.page.upload_net_error_hint')}`;
      }
      window.__apiToast?.(t('scripts.toast.preview_fail'), { kind: "danger", detail, duration: 8000 });
      setEstimate({
        file: { name: selectedFile.name, size: selectedFile.size, chapters: 0, words: 0 },
        chapters: 0, words: 0,
        stages: [], totalTokens: 0, totalSec: 0, cost: 0,
        model: "—",
        warnings: [t('scripts.import.preview_fail_detail', { detail })],
        previewError: detail,
      });
      setPreviewBusy(false);
      setPreviewProgress({ value: 0, label: "" });
      return;
    }
    // 成功路径：用后端真实数字
    const chapters = Number(result.total_chapters) || (Array.isArray(result.preview) ? result.preview.length : 0);
    const words = Number(result.total_words) || 0;
    const stages = IMPORT_STAGES.map(s => ({
      id: s.id, label: t(s.labelKey), hint: t(s.hintKey),
      tokens_est: s.tok_per_chap * Math.max(chapters, 1),
      time_est_sec: Math.round(s.tok_per_chap * Math.max(chapters, 1) / 800),
    }));
    const totalTokens = stages.reduce((a, s) => a + s.tokens_est, 0);
    const totalSec = stages.reduce((a, s) => a + s.time_est_sec, 0);
    const cost = totalTokens * 0.75 / 1_000_000;
    const warnings = [];
    if (Array.isArray(result.warnings)) warnings.push(...result.warnings);
    if (result.report && result.report.mode_label) {
      warnings.push(t('scripts.page.split_mode_warn', { mode: result.report.mode_label, conf: result.report.confidence ?? "—" }));
    }
    setEstimate({
      file: { name: selectedFile.name, size: selectedFile.size, chapters, words },
      chapters, words,
      stages, totalTokens, totalSec, cost,
      model: result.model || "GPT-4o · RPG 调优",
      preview: result.preview,
      report: result.report,
      warnings,
      upload_id: uploadId,
    });
    setPreviewBusy(false);
  };

  const startImport = async () => {
    // task 17: 真正打通分片上传 → /api/scripts/import 流水线。
    // 之前发的 init 字段 {size, kind, chunk_size} 全不对（后端要 total_bytes/total_chunks）→ 400。
    // 之前任何一步失败仍会创建 fake job 让 UI 假装在跑 → 用户误以为成功。
    // 现在：选了真实文件就必须真传成功；任一步失败 toast 报错并停止，不再造 job。
    if (importBusy) {
      window.__apiToast?.(t('scripts.import.import_busy'), { kind: "info" });
      return;
    }
    if (selectedFile) {
      if (!estimate || !Array.isArray(estimate.stages)) {
        window.__apiToast?.(t('scripts.import.preview_required'), { kind: "warn" });
        return;
      }
      let uploadId = estimate.upload_id || null;
      setImportBusy(true);
      setImportPercent(0);
      setImportProgress(uploadId ? t('scripts.import.import_reuse_upload') : t('scripts.import.upload_init'));
      try {
        // ── 阶段 A: 文件分片上传 — 这是前端唯一真知道进度的环节,占 0-30% ──
        if (!uploadId) {
          uploadId = await uploadFileChunks(selectedFile, ({ stage, done, total, percent }) => {
            if (stage === "init") {
              setImportPercent(1);
              setImportProgress(t('scripts.import.upload_init'));
            } else if (stage === "chunk") {
              // 0-30% 是文件 chunk;30% 之后交给后端 SSE 推 stage 进度,wizard 不再写死 milestone
              setImportPercent(Math.min(30, Math.round((percent || 0) * 0.30)));
              setImportProgress(t('scripts.import.upload_progress', { done, total }));
            } else if (stage === "finish") {
              setImportPercent(30);
              setImportProgress(t('scripts.import.upload_finish'));
            }
          });
        } else {
          // 复用 preview 已传完的 upload — 直接进入 import 创建
          setImportPercent(30);
        }
        // ── 阶段 B: 创建剧本 (importScript) — 不写 milestone 数字,只换文案 ──
        setImportProgress(t('scripts.import.import_creating'));
        const createScriptFromUpload = (nextUploadId) => window.api.scripts.importScript({
          upload_id: nextUploadId,
          title: title || selectedFile.name.replace(/\.(txt|md)$/i, ""),
          split_rule: rule || "auto",
          custom_pattern: pattern || "",
          require_llm_credentials: true,
        });
        const reuploadForExpiredUpload = async () => {
          setImportProgress(t('scripts.import.upload_expired_retry'));
          setImportPercent(0);
          return uploadFileChunks(selectedFile, ({ stage, done, total, percent }) => {
            if (stage === "init") {
              setImportPercent(1);
              setImportProgress(t('scripts.import.upload_init'));
            } else if (stage === "chunk") {
              setImportPercent(Math.min(30, Math.round((percent || 0) * 0.30)));
              setImportProgress(t('scripts.import.upload_progress', { done, total }));
            } else if (stage === "finish") {
              setImportPercent(30);
              setImportProgress(t('scripts.import.upload_finish'));
            }
          });
        };
        let importResp;
        try {
          importResp = await createScriptFromUpload(uploadId);
        } catch (e) {
          if (!isExpiredUploadError(e)) throw e;
          uploadId = await reuploadForExpiredUpload();
          importResp = await createScriptFromUpload(uploadId);
        }
        if (importResp && importResp.ok === false && isExpiredUploadError(importResp)) {
          uploadId = await reuploadForExpiredUpload();
          importResp = await createScriptFromUpload(uploadId);
        }
        if (!importResp || importResp.ok === false) {
          throw new Error((importResp && (importResp.error || importResp.detail)) || t('scripts.import.api_fail'));
        }
        const sc = importResp.script || {};
        // ── 阶段 C: importPipeline 启动 LLM 5-stage 流水线 ─────────────────
        // 之前这里失败被 console.warn 吞掉、wizard 仍然 toast"导入成功"。
        // 现在:启动失败必须 toast danger + 阻断 wizard,不允许进 banner 正常路径。
        let pipelineJobId = null;
        let pipelinePaused = null;
        try {
          setImportProgress(t('scripts.import.import_pipeline'));
          const pipelineResp = await window.api.scripts.importPipeline(sc.id, {
            enable_cards: enableCards,
            enable_worldbook: enableWorldbook,
            budget: estimate,
          });
          if (!pipelineResp || pipelineResp.ok === false || !pipelineResp.job_id) {
            throw new Error((pipelineResp && (pipelineResp.error || pipelineResp.detail)) || t('scripts.import.api_fail'));
          }
          pipelineJobId = pipelineResp.job_id;
        } catch (e) {
          if (isCredentialsRequiredError(e)) {
            const payload = e?.payload || {};
            const createdTitle = sc.title || title || estimate.file.name;
            const modeLabel = (() => { const _r = SPLIT_RULES.find(r => r.id === rule); return _r ? t(_r.labelKey) : rule; })();
            pipelinePaused = {
              script_id: sc.id,
              title: createdTitle,
              file: estimate.file,
              file_name: estimate.file?.name || createdTitle,
              mode: modeLabel,
              stages: estimate.stages,
              totalTokens: estimate.totalTokens,
              budget: estimate,
              api_id: payload.api_id,
              model: payload.model,
              credential_api_id: payload.credential_api_id,
              reason: "credentials_required",
              created_at: Date.now(),
            };
            persistPendingPipeline(pipelinePaused);
            window.__apiToast?.(t('scripts.import.api_key_required_title'), {
              kind: "warn",
              detail: t('scripts.import.api_key_required_toast'),
              duration: 6000,
            });
          } else {
            // 非 credentials 缺失的失败 — 流水线根本没起来,wizard 必须停。
            // 不再 console.warn 静默继续假装"导入成功"。
            const detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('scripts.toast.unknown_error');
            window.__apiToast?.(t('scripts.toast.import_fail'), {
              kind: "danger",
              detail,
              duration: 6000,
            });
            // 剧本壳已存在(章节/chunks 在 importScript 阶段建好了),用户可在 KbExtractPanel 手动重试
            try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
            setJob({
              id: "imp_dispatch_failed_" + sc.id,
              script_id: sc.id,
              title: sc.title || title || estimate.file.name,
              file: estimate.file,
              status: "partial",
              error: detail,
              stages: buildRunningStages(estimate.stages),
              started_at: Date.now(),
              finished_at: Date.now(),
              real: true,
              dispatch_failed: true,
            });
            setEstimate(null);
            return;
          }
        }
        if (pipelinePaused) {
          setJob(null);
          setEstimate(null);
          try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
          window.toast && window.toast(t('scripts.toast.import_ok'), {
            kind: "ok",
            detail: t('scripts.import.import_ok_needs_api', { id: sc.id, title: sc.title || "" }),
            duration: 5000,
          });
          return;
        }
        // ── 阶段 D: 流水线已派发,job_id 拿到了 — banner 内部订 SSE ──
        const stages = buildRunningStages(estimate.stages);
        const j = {
          id: pipelineJobId,
          file: estimate.file,
          title: sc.title || title || estimate.file.name,
          script_id: sc.id,
          mode: (() => { const _r = SPLIT_RULES.find(r => r.id === rule); return _r ? t(_r.labelKey) : rule; })(),
          stages,
          totalTokens: estimate.totalTokens,
          status: "running",
          started_at: Date.now(),
          real: true,
        };
        // 不写 setImportPercent(100):任务还没真完,只是后端已经接手。
        // 真正完成由 banner 内部 SSE on_done → setJob({status:'done'/'failed'/...}) 触发。
        setJob(j);
        setEstimate(null);
        // 通知外部 ScriptsPage 刷新真实列表
        try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
        // 这里只 toast"已派发后台",不是"导入完成" — 完成 toast 由 banner SSE done 时发。
        // 后端已派发流水线,但任务还在跑 — toast 用"导入进行中"(已有 key),
        // 不用 import_ok 那种"导入成功"的撒谎话术
        window.__apiToast?.(t('scripts.import.importing_bg'), {
          kind: "info",
          detail: t('scripts.toast.import_ok_detail', { id: sc.id, title: sc.title || "" }),
          duration: 3000,
        });
      } catch (e) {
        if (isCredentialsRequiredError(e)) {
          const payload = e?.payload || {};
          const draftTitle = title || selectedFile.name.replace(/\.(txt|md)$/i, "");
          persistPendingImport({
            upload_id: uploadId,
            title: draftTitle,
            file: estimate?.file || { name: selectedFile.name, size: selectedFile.size },
            file_name: estimate?.file?.name || selectedFile.name,
            split_rule: rule || "auto",
            custom_pattern: pattern || "",
            stages: estimate?.stages || [],
            totalTokens: estimate?.totalTokens || 0,
            budget: estimate || {},
            api_id: payload.api_id,
            model: payload.model,
            credential_api_id: payload.credential_api_id,
            reason: "credentials_required",
            created_at: Date.now(),
          });
          setJob(null);
          window.__apiToast?.(t('scripts.import.api_key_required_title'), {
            kind: "warn",
            detail: t('scripts.import.api_key_required_preimport_toast'),
            duration: 7000,
          });
          return;
        }
        // 取消任何已经初始化的 upload，让服务器释放临时块
        if (uploadId) { try { await window.api.uploads.cancel(uploadId); } catch (_) {} }
        const detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('scripts.toast.unknown_error');
        window.__apiToast?.(t('scripts.toast.import_fail'), { kind: "danger", detail, duration: 5000 });
        // 关键：不要建 fake job 让用户误以为在跑
        setJob(null);
        // estimate 保留，以便用户修改设置后重试
      } finally {
        setImportBusy(false);
        setImportProgress("");
        setImportPercent(0);
      }
      return;
    }
    // 没选文件：仅在 isMockEstimate（明确示例）下允许 demo job
    if (estimate && estimate.isMockEstimate) {
      window.__apiToast?.(t('scripts.toast.mock_warn'), { kind: "warn", detail: t('scripts.toast.mock_warn_detail'), duration: 3000 });
      return;
    }
    window.__apiToast?.(t('scripts.toast.select_file_first'), { kind: "warn" });
  };

  const resumePendingImport = async () => {
    if (!pendingImport?.upload_id || importBusy) return;
    setImportBusy(true);
    setImportPercent(0);
    setImportProgress(t('scripts.import.import_creating'));
    try {
      const importResp = await window.api.scripts.importScript({
        upload_id: pendingImport.upload_id,
        title: pendingImport.title || pendingImport.file_name || "",
        split_rule: pendingImport.split_rule || "auto",
        custom_pattern: pendingImport.custom_pattern || "",
        require_llm_credentials: true,
      });
      if (!importResp || importResp.ok === false) {
        const err = new Error((importResp && (importResp.error || importResp.detail)) || t('scripts.import.api_fail'));
        err.payload = importResp;
        throw err;
      }
      const sc = importResp.script || {};
      // 不写 setImportPercent(92):流水线进度由 banner 内部 SSE 接管。
      setImportProgress(t('scripts.import.import_pipeline'));
      const pipelineResp = await window.api.scripts.importPipeline(sc.id, {
        enable_cards: enableCards,
        enable_worldbook: enableWorldbook,
        budget: pendingImport.budget || {},
      });
      if (!pipelineResp || pipelineResp.ok === false || !pipelineResp.job_id) {
        throw new Error((pipelineResp && (pipelineResp.error || pipelineResp.detail)) || t('scripts.import.api_fail'));
      }
      const baseStages = pendingImport.stages || pendingImport.budget?.stages || [];
      const stages = buildRunningStages(baseStages);
      setJob({
        id: pipelineResp.job_id,
        file: pendingImport.file || { name: pendingImport.file_name || pendingImport.title || "script" },
        title: sc.title || pendingImport.title || pendingImport.file_name || "script",
        script_id: sc.id,
        mode: (() => { const _r = SPLIT_RULES.find(r => r.id === (pendingImport.split_rule || "auto")); return _r ? t(_r.labelKey) : (pendingImport.split_rule || "auto"); })(),
        stages,
        totalTokens: pendingImport.totalTokens || 0,
        status: "running",
        started_at: Date.now(),
        real: true,
      });
      clearPendingImport();
      clearPendingPipeline();
      try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
      // 同 startImport:派发成功仅"已开始",不是"已完成"
      window.__apiToast?.(t('scripts.import.importing_bg'), {
        kind: "info",
        detail: t('scripts.toast.import_ok_detail', { id: sc.id, title: sc.title || "" }),
        duration: 3000,
      });
    } catch (e) {
      if (isCredentialsRequiredError(e)) {
        const payload = e?.payload || {};
        persistPendingImport({
          ...pendingImport,
          api_id: payload.api_id,
          model: payload.model,
          credential_api_id: payload.credential_api_id,
        });
        window.__apiToast?.(t('scripts.import.api_key_required_title'), {
          kind: "warn",
          detail: t('scripts.import.api_key_required_preimport_toast'),
          duration: 6000,
        });
      } else {
        const detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('scripts.toast.unknown_error');
        if (isExpiredUploadError(e)) {
          clearPendingImport();
          window.__apiToast?.(t('scripts.import.saved_upload_expired'), {
            kind: "warn",
            detail: t('scripts.import.saved_upload_expired_detail'),
            duration: 7000,
          });
        } else {
        window.__apiToast?.(t('scripts.toast.import_fail'), { kind: "danger", detail, duration: 5000 });
        }
      }
    } finally {
      setImportBusy(false);
      setImportProgress("");
      setImportPercent(0);
    }
  };

  const cancelJob = async () => {
    if (!job) return;
    if (job.real) {
      try { await window.api.scripts.jobCancel(job.id); } catch (e) {}
    }
    setJob(j => ({ ...j, status: "cancelled", cancelled_at: Date.now() }));
    window.__apiToast?.(t('scripts.toast.import_cancelled'), {
      kind: "warn",
      detail: t('scripts.import.result_cancelled_detail', { id: job.id }),
      duration: 8000,
    });
  };

  const dismissJob = () => {
    setJob(null);
  };

  // banner 内部 SSE 每帧推上来,merge 进 job state — 注意不能覆盖 wizard 这边的
  // 元数据 (title / file / mode / stages 占位 label / hint) — 这些后端 SSE 不会推。
  const onJobSseUpdate = useCallbackPL((jb) => {
    if (!jb || typeof jb !== 'object') return;
    setJob((prev) => {
      if (!prev) return prev;
      // 把后端字段覆盖上去,但保留 wizard 注入的 file/title/mode/stages 占位。
      // stages: 后端会推 stages 数组(每个元素含 id/label/status/count?);如果有 → 取真值,
      // 否则保留前端占位(只有 id/label/hint)。这是唯一允许的 fallback,
      // 但 status 字段绝对不允许在 SSE 没推时被前端推断。
      const sseStages = Array.isArray(jb.stages) ? jb.stages : null;
      const mergedStages = sseStages && sseStages.length
        ? sseStages.map((s, i) => {
            const placeholder = (prev.stages && prev.stages[i]) || {};
            return {
              ...placeholder,  // label/hint 占位
              ...s,            // 后端真值 (status/count/...)
            };
          })
        : prev.stages;
      return {
        ...prev,
        ...jb,
        id: jb.job_id || jb.id || prev.id,
        stages: mergedStages,
      };
    });
  }, []);

  // SSE done event 触发 — 任务终态 toast。注意 jb 这里只有 {status} 字段,
  // 完整 job 在前面 update 帧已 merge 进 state,从最新 prev 读。
  const onJobSseDone = useCallbackPL(() => {
    setJob((prev) => {
      if (!prev) return prev;
      const stages = Array.isArray(prev.stages) ? prev.stages : [];
      const errored = stages.filter(s => s && (s.status === 'error' || s.status === 'failed'));
      const hasErr = errored.length > 0 || prev.status === 'failed' || prev.status === 'done_with_errors';
      const detail = errored.length
        ? errored.map(s => (s.id || s.label || '?') + ': ' + (s.error || t('scripts.toast.unknown_error'))).join('; ')
        : (prev.error || '');
      if (prev.status === 'cancelled') {
        window.__apiToast?.(t('scripts.toast.import_cancelled'), {
          kind: 'warn',
          detail: t('scripts.import.result_cancelled_detail', { id: prev.id || prev.job_id || '?' }),
          duration: 8000,
        });
      } else if (prev.status === 'failed') {
        window.__apiToast?.(t('scripts.toast.import_fail'), { kind: 'danger', detail: detail || t('scripts.toast.unknown_error'), duration: 5000 });
      } else if (hasErr) {
        window.__apiToast?.(t('scripts.toast.import_partial'), { kind: 'warn', detail, duration: 6000 });
      } else {
        window.__apiToast?.(t('scripts.toast.import_ok'), { kind: 'ok', detail: t('scripts.toast.import_ok_detail', { id: prev.script_id || '?', title: prev.title || '' }), duration: 3000 });
      }
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
      const finalStatus = IMPORT_JOB_TERMINAL_STATUSES.has(prev.status)
        ? prev.status
        : (hasErr ? 'done_with_errors' : 'done');
      return { ...prev, status: finalStatus, finished_at: Date.now() };
    });
  }, [t]);

  const onJobSseError = useCallbackPL(() => {
    window.__apiToast?.(t('scripts.toast.sse_disconnected'), { kind: 'warn', duration: 3000 });
  }, [t]);

  const ruleOpt = SPLIT_RULES.find(r => r.id === rule) || SPLIT_RULES[0];
  const ruleLabel = t(ruleOpt.labelKey);
  const fileName = (selectedFile && selectedFile.name) || (estimate && estimate.file && estimate.file.name) || null;
  const jobRunning = job && !IMPORT_JOB_TERMINAL_STATUSES.has(job.status);

  return (
    <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
      {/* 左:模块平铺 */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <CSSpaceBetween size="l">
          {jobRunning && (
            <ImportJobBanner
              job={job}
              onCancel={cancelJob}
              onUpdate={onJobSseUpdate}
              onDone={onJobSseDone}
              onError={onJobSseError}
            />
          )}
          {job && IMPORT_JOB_TERMINAL_STATUSES.has(job.status) && (
            <ImportJobResult job={job} onDismiss={dismissJob} onReuse={() => { setJob(null); setEstimate(null); }} />
          )}
          {pendingImport && !jobRunning && (
            <CSAlert
              type="warning"
              header={t('scripts.import.api_key_required_title')}
              action={
                <CSSpaceBetween direction="horizontal" size="xs">
                  <CSButton onClick={resumePendingImport} loading={importBusy} disabled={importBusy}>
                    {t('scripts.import.resume_import')}
                  </CSButton>
                  <CSButton variant="primary" iconName="settings" onClick={goApiSettings}>
                    {t('scripts.import.go_api_settings')}
                  </CSButton>
                </CSSpaceBetween>
              }
            >
              {t('scripts.import.api_key_required_preimport_body', {
                title: pendingImport.title || pendingImport.file_name || t('scripts.import.unnamed'),
                provider: pendingImport.credential_api_id || pendingImport.api_id || 'API',
              })}
            </CSAlert>
          )}
          {pendingPipeline && !jobRunning && (
            <CSAlert
              type="warning"
              header={t('scripts.import.api_key_required_title')}
              action={
                <CSSpaceBetween direction="horizontal" size="xs">
                  <CSButton onClick={resumePendingPipeline} loading={importBusy} disabled={importBusy}>
                    {t('scripts.import.resume_pipeline')}
                  </CSButton>
                  <CSButton variant="primary" iconName="settings" onClick={goApiSettings}>
                    {t('scripts.import.go_api_settings')}
                  </CSButton>
                </CSSpaceBetween>
              }
            >
              {t('scripts.import.api_key_required_body', {
                title: pendingPipeline.title || pendingPipeline.file_name || t('scripts.import.unnamed'),
                provider: pendingPipeline.credential_api_id || pendingPipeline.api_id || 'API',
              })}
            </CSAlert>
          )}

          <CSContainer header={<CSHeader variant="h2" description={t('scripts.import.basic_desc')}>{t('scripts.import.basic_title')}</CSHeader>}>
            <CSColumnLayout columns={2}>
              <CSFormField label={t('scripts.import.field_title')} description={t('scripts.import.field_title_desc')}>
                <CSInput value={title} onChange={({ detail }) => setTitle(detail.value)} placeholder={t('scripts.import.field_title_desc')} />
              </CSFormField>
              <CSFormField label={t('scripts.import.field_rule')}>
                <CSSelect selectedOption={{ value: ruleOpt.id, label: ruleLabel }}
                  options={SPLIT_RULES.map(r => ({ value: r.id, label: t(r.labelKey) }))}
                  onChange={({ detail }) => {
                    const nextRule = detail.selectedOption.value || "auto";
                    if (nextRule !== rule) discardEstimate(true);
                    setRule(nextRule);
                  }} />
              </CSFormField>
              <div style={{ gridColumn: '1 / -1' }}>
                <CSFormField label={t('scripts.import.field_custom_regex')} description={t('scripts.import.field_custom_regex_desc')}>
                  <CSInput value={pattern} onChange={({ detail }) => {
                    if (detail.value !== pattern && estimate) discardEstimate(false);
                    setPattern(detail.value);
                  }}
                    disabled={rule !== 'custom'} placeholder={t('scripts.import.field_custom_regex_placeholder')} />
                </CSFormField>
              </div>
            </CSColumnLayout>
          </CSContainer>

          {/* RAG / embedder 引导:导入后向量索引需要独立配置,与主 LLM Key 无关。
              确定性检查:embedderStatus 来自后端 /api/me/embedder/status preflight,
              不依赖 LLM 判断 — 有配 key + provider_ok 才是 ok。*/}
          {embedderStatus && embedderStatus.effective_source === 'none' && !embedderStatus.preflight?.ok && (
            <CSAlert
              type="info"
              header={t('scripts.import.embedder_not_configured_title', { defaultValue: '未配置 RAG / 向量嵌入模型（可选，但建议配置）' })}
              action={
                <CSButton iconName="settings" variant="primary" onClick={() => { plNavigate('settings-models'); }}>
                  {t('scripts.import.go_rag_settings', { defaultValue: '去设置 RAG 模型' })}
                </CSButton>
              }
            >
              {t('scripts.import.embedder_not_configured_body', {
                defaultValue:
                  '向量索引需要单独配置「向量嵌入（RAG）模型」，与主对话用的 LLM Key 是分开的两件事；没配它就无法生成向量索引，RAG 语义召回会退化为关键字匹配。\n注意：不是所有厂商都有向量嵌入接口——Anthropic、DeepSeek 没有。请在「设置 → RAG / 向量模型」配置一个支持 /embeddings 接口的 API Key（如 OpenAI、通义千问 Qwen、硅基流动、Cohere、Vertex 等）。',
              })}
            </CSAlert>
          )}
          {embedderStatus && embedderStatus.preflight?.last_error_hint && (
            <CSAlert
              type="warning"
              header={t('scripts.import.embedder_error_title', { defaultValue: '向量嵌入配置可能有问题' })}
              action={
                <CSButton iconName="settings" onClick={() => { plNavigate('settings-models'); }}>
                  {t('scripts.import.go_rag_settings', { defaultValue: '去 RAG 设置检查' })}
                </CSButton>
              }
            >
              {embedderStatus.preflight.last_error_hint}
            </CSAlert>
          )}

          {/* 拆书流水线 LLM 选择 — 写入 user prefs.extractor.*,可在「设置 → 模块模型」覆盖 */}
          <CSContainer header={<CSHeader variant="h2" description={t('scripts.page.extractor_model_desc')}>{t('scripts.page.extractor_model_title')}</CSHeader>}>
            {/* 统一共享组件:Provider+Model 选择 + 「未配 key」警告 + 写 user prefs.extractor.*,
                与「设置 → 按模块分配模型」的提取器、cards 的 card_import 同一实现。
                后端 import-pipeline 读 extractor.* prefs(不当场传参),所以这里只需持久化偏好。 */}
            <AgentModelPicker
              prefPrefix="extractor"
              preferProvider="deepseek"
              defaultModel={null}
              variant="bare"
              persistOnMount
              configHash="settings-models"
            />
          </CSContainer>

          {/* 完整流水线开关 — 之前两个 toggle 在代码里硬编码 true,UI 不暴露,
              用户压根不知道导入会自动跑 LLM 生 NPC 角色卡+世界书。现在显式给开关。 */}
          <CSContainer header={<CSHeader variant="h2"
            description={t('scripts.import.pipeline_options_desc')}>
            {t('scripts.import.pipeline_options_title')}
          </CSHeader>}>
            <CSColumnLayout columns={2}>
              <CSFormField label={t('scripts.import.enable_cards_label')}
                description={t('scripts.import.enable_cards_desc')}>
                <CSToggle checked={enableCards} onChange={({ detail }) => setEnableCards(detail.checked)}>
                  {enableCards ? t('common.enabled') : t('common.disabled')}
                </CSToggle>
              </CSFormField>
              <CSFormField label={t('scripts.import.enable_worldbook_label')}
                description={t('scripts.import.enable_worldbook_desc')}>
                <CSToggle checked={enableWorldbook} onChange={({ detail }) => setEnableWorldbook(detail.checked)}>
                  {enableWorldbook ? t('common.enabled') : t('common.disabled')}
                </CSToggle>
              </CSFormField>
            </CSColumnLayout>
            {(!enableCards || !enableWorldbook) && (
              <CSBox fontSize="body-s" color="text-status-warning" padding={{ top: 'xs' }}>
                {t('scripts.import.partial_pipeline_warn')}
              </CSBox>
            )}
          </CSContainer>

          <CSContainer header={<CSHeader variant="h2" description={t('scripts.import.file_desc')}>{t('scripts.import.file_title')}</CSHeader>}>
            <CSFileUpload
              value={selectedFile ? [selectedFile] : []}
              onChange={({ detail }) => {
                const f = detail.value?.[0];
                if (f) onPickFile(f);
                else {
                  discardEstimate(false);
                  clearPendingImport();
                  setSelectedFile(null);
                }
              }}
              accept=".txt,.md"
              showFileSize
              constraintText={t('scripts.import.file_constraint')}
              i18nStrings={{
                uploadButtonText: () => t('scripts.import.file_btn'),
                dropzoneText: () => t('scripts.import.file_drop'),
                removeFileAriaLabel: (i) => t('scripts.import.file_remove', { i: i + 1 }),
                limitShowFewer: t('scripts.import.file_collapse'),
                limitShowMore: t('scripts.import.file_expand'),
                errorIconAriaLabel: t('scripts.import.file_error'),
              }}
            />
          </CSContainer>

          {estimate && !job && (
            <ImportEstimateView estimate={estimate} rule={rule} hideActions />
          )}
        </CSSpaceBetween>
      </div>

      {/* 右:概要 + 主操作(sticky) */}
      <div style={{ width: 320, flexShrink: 0, position: 'sticky', top: 72 }}>
        <CSContainer header={<CSHeader variant="h2">{t('scripts.import.summary_title')}</CSHeader>}>
          <CSSpaceBetween size="m">
            <CSKeyValuePairs columns={1} items={[
              { label: t('scripts.import.summary_file'), value: fileName || '—' },
              { label: t('scripts.import.field_rule'), value: ruleLabel },
              ...(estimate ? [
                { label: t('scripts.my.chapters'), value: String(estimate.chapters) },
                { label: t('scripts.my.words'), value: `${(estimate.words / 10000).toFixed(1)} ${t('scripts.my.wan')}` },
                { label: t('scripts.import.est_cost'), value: <CSBox color="text-status-info" fontWeight="bold">${estimate.cost.toFixed(2)}</CSBox> },
                { label: t('scripts.import.est_time'), value: t('scripts.import.est_time_val', { min: Math.round(estimate.totalSec / 60) }) },
              ] : []),
            ]} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {!estimate && (
                <CSButton variant="primary" iconName="search" loading={previewBusy} disabled={!selectedFile || !!job || importBusy} onClick={startEstimate}>
                  {previewBusy ? t('scripts.import.calculating') : t('scripts.import.preview_split')}
                </CSButton>
              )}
              {previewBusy && (
                <CSProgressBar
                  value={previewProgress.value || 0}
                  label={t('scripts.import.preview_progress')}
                  additionalInfo={previewProgress.label}
                  status="in-progress"
                />
              )}
              {estimate && !job && (
                <>
                  <CSButton variant="primary" iconName="check" loading={importBusy} disabled={importBusy} onClick={startImport}>
                    {importBusy ? t('scripts.import.import_creating') : t('scripts.import.confirm_import_bg')}
                  </CSButton>
                  <CSButton disabled={importBusy} onClick={() => discardEstimate(false)}>{t('scripts.import.re_estimate')}</CSButton>
                </>
              )}
              {importBusy && (
                <CSProgressBar
                  value={importPercent || 0}
                  label={t('scripts.import.import_progress')}
                  additionalInfo={importProgress || t('scripts.import.importing_bg')}
                  status="in-progress"
                />
              )}
              {jobRunning && <CSBox color="text-body-secondary" fontSize="body-s">{t('scripts.import.importing_bg')}</CSBox>}
              {onClose && <CSButton variant="link" onClick={onClose}>{t('common.close')}</CSButton>}
            </div>
          </CSSpaceBetween>
        </CSContainer>
      </div>
    </div>
  );
}

export { ScriptsImportView, ImportJobBanner, ImportJobResult, ImportEstimateView };
