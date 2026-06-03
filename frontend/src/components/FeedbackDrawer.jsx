/**
 * FeedbackDrawer.jsx — 用户反馈侧抽屉 (FB-01/02/07/08)
 *
 * 暴露:
 *   <FeedbackDrawer open onClose />  — 直接使用
 *   window.__openFeedback()          — 全局快捷打开
 *   <FeedbackDrawerRoot />           — 挂到根节点一次即可（监听全局事件）
 *
 * 注意: 组件已完整实现，但**未接入** platform-app.jsx（留给后续 T2-style 合并）。
 *
 * consent_token: 将同意文案做 SHA256，通过 SubtleCrypto API 计算，随 POST 发送。
 *   文案锁定为 CONSENT_TEXT 常量，升版时同步修改此常量即可。
 */
import React from 'react';
import CSModal        from '@cloudscape-design/components/modal';
import CSBox          from '@cloudscape-design/components/box';
import CSButton       from '@cloudscape-design/components/button';
import CSAlert        from '@cloudscape-design/components/alert';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSTextarea     from '@cloudscape-design/components/textarea';
import CSCheckbox     from '@cloudscape-design/components/checkbox';
import CSFormField    from '@cloudscape-design/components/form-field';
import CSContainer    from '@cloudscape-design/components/container';
import CSHeader       from '@cloudscape-design/components/header';
import CSExpandableSection from '@cloudscape-design/components/expandable-section';
import { sha256hex } from '../lib/crypto-safe.js';

// ── 常量 ─────────────────────────────────────────────────────────────────────

const CONSENT_TEXT =
  '我已阅读 AUP §2.J，理解不得包含成人主题节选，同意（此操作记录我的同意）';

const AUP_LINK = 'https://play.stellatrix.icu/legal/aup#2J';

const MAX_FREE_TEXT = 10000;

// 玩家交流 QQ 群
const QQ_GROUP_NUMBER = '584876566';
const QQ_JOIN_URL     = 'https://qm.qq.com/q/49Dqcr0aw0';
const QQ_QR_SRC       = '/qq-group.jpg';

// ── SHA256 工具 ───────────────────────────────────────────────────────────────
// crypto.subtle 仅在安全上下文(HTTPS/localhost)可用;明文 HTTP LAN 访问下为
// undefined,旧实现会抛错使反馈提交失败。改用 crypto-safe 的降级封装(安全上下文
// 走真 SHA-256,否则确定性 64-hex 兜底,满足后端 consent_token 契约)。

// ── FeedbackDrawer ────────────────────────────────────────────────────────────

export function FeedbackDrawer({ open, onClose }) {
  const [freeText, setFreeText]           = React.useState('');
  const [includeExcerpts, setIncludeExcerpts] = React.useState(false);
  const [selectedExcerpts, setSelectedExcerpts] = React.useState([]);  // indices
  const [recentTurns, setRecentTurns]     = React.useState([]);
  const [consent, setConsent]             = React.useState(false);
  const [busy, setBusy]                   = React.useState(false);
  const [done, setDone]                   = React.useState(false);
  const [error, setError]                 = React.useState(null);
  // 默认勾上"附带运行环境信息":这是给小范围内测时管理员排 bug 用的,
  // 不写就只有 free_text → 一句话bug → 无法定位
  const [includeRuntime, setIncludeRuntime] = React.useState(true);
  const [runtimePreview, setRuntimePreview] = React.useState(null);
  // 反馈处理回执:上次开抽屉以来管理员处理过的反馈
  const [newlyReviewed, setNewlyReviewed] = React.useState([]);
  const [feedbackHistory, setFeedbackHistory] = React.useState([]);
  const [historyLoading, setHistoryLoading] = React.useState(false);
  const [historyError, setHistoryError] = React.useState(null);
  const [maxReviewedId, setMaxReviewedId] = React.useState(0);

  const loadFeedbackHistory = React.useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const lastSeenStr = localStorage.getItem('feedback_last_seen_id') || '0';
      const lastSeen = parseInt(lastSeenStr, 10) || 0;
      const res = await fetch('/api/me/feedback?limit=50', { credentials: 'include' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data || !data.ok) throw new Error(data?.error || '读取历史反馈失败');
      const items = Array.isArray(data.items) ? data.items : [];
      setFeedbackHistory(items);
      setNewlyReviewed(items.filter(it => it.review_decision && it.id > lastSeen));
      setMaxReviewedId(items.reduce((m, it) => Math.max(m, it.id || 0), 0));
    } catch (e) {
      setHistoryError(e?.message || '读取历史反馈失败');
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  // 打开时重置状态 + 取一份运行环境快照预览(只读,实际提交时再取一次保证新鲜)
  React.useEffect(() => {
    if (!open) return;
    setFreeText('');
    setIncludeExcerpts(false);
    setSelectedExcerpts([]);
    setConsent(false);
    setBusy(false);
    setDone(false);
    setError(null);
    try {
      const snap = window.__getRuntimeSnapshot && window.__getRuntimeSnapshot();
      setRuntimePreview(snap ? snap.__runtime__ : null);
    } catch (_) { setRuntimePreview(null); }
    loadFeedbackHistory();
  }, [open, loadFeedbackHistory]);

  // 关闭时标记当前最大 feedback id 为已读 — 下次开抽屉就不再提示这些
  React.useEffect(() => {
    if (open) return;
    if (maxReviewedId > 0) {
      try { localStorage.setItem('feedback_last_seen_id', String(maxReviewedId)); } catch (_) {}
    }
  }, [open, maxReviewedId]);

  // 加载当前会话最近 5 段对话摘要
  React.useEffect(() => {
    if (!open || !includeExcerpts) return;
    let cancelled = false;
    (async () => {
      try {
        // 从游戏 state 拉最近对话，适配现有 window.api 结构
        const state = await window.api?.game?.state?.();
        const nodes = state?.history || state?.branch_nodes || state?.turns || [];
        const recent = nodes.slice(-10).filter((n) => n.role === 'gm' || n.role === 'user');
        const turns = recent.slice(-5).map((n, i) => ({
          idx: i,
          session_id: state?.save_id || '',
          range: `${n.turn_index ?? i}`,
          plaintext: ((n.content || n.text || '') + '').slice(0, 200),
          label: `第 ${n.turn_index ?? i + 1} 回合 (${n.role === 'gm' ? 'GM' : '玩家'})`,
        }));
        if (!cancelled) setRecentTurns(turns);
      } catch (_) {
        if (!cancelled) setRecentTurns([]);
      }
    })();
    return () => { cancelled = true; };
  }, [open, includeExcerpts]);

  function toggleExcerpt(idx) {
    setSelectedExcerpts((prev) =>
      prev.includes(idx) ? prev.filter((i) => i !== idx) : [...prev, idx]
    );
  }

  const canSubmit = consent && freeText.trim().length > 0 && !busy && !done;

  async function handleSubmit() {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const token = await sha256hex(CONSENT_TEXT);
      const excerpts = includeExcerpts
        ? recentTurns
            .filter((t) => selectedExcerpts.includes(t.idx))
            .map(({ session_id, range, plaintext }) => ({ session_id, range, plaintext }))
        : [];

      // 附带运行环境切片(以特殊 entry 形式塞进 excerpts 数组,后端无需 schema 变更
      // 就能存到 excerpts_jsonb;admin UI 识别 __runtime__ key 单独展示)。
      // 用户勾"包含运行环境"的同时,也连带带最近 3 轮对话 — 跟 errors/api 同档保守披露
      if (includeRuntime) {
        try {
          const snap = window.__getRuntimeSnapshot && window.__getRuntimeSnapshot({ includeRecentDialog: true });
          if (snap && snap.__runtime__) excerpts.push(snap);
        } catch (_) {}
      }

      const appVersion = window.__APP_VERSION__ || '';
      const res = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          free_text: freeText,
          excerpts,
          consent_token: token,
          app_version: appVersion,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      }
      setDone(true);
      await loadFeedbackHistory();
    } catch (e) {
      setError(e?.message || '提交失败，请稍后重试');
    } finally {
      setBusy(false);
    }
  }

  function feedbackStatusLabel(decision) {
    if (!decision) return '待处理';
    if (decision === 'ok') return '已采纳';
    if (decision === 'spam') return '未采纳';
    if (decision === 'nsfw_terminate') return '违规处理';
    return decision;
  }

  function feedbackStatusType(decision) {
    if (!decision) return 'info';
    if (decision === 'ok') return 'success';
    if (decision === 'spam') return 'warning';
    return 'error';
  }

  function formatFeedbackTime(ts) {
    if (!ts) return '—';
    try {
      return new Date(ts).toLocaleString();
    } catch (_) {
      return ts;
    }
  }

  return (
    <CSModal
      visible={open}
      onDismiss={onClose}
      size="medium"
      header="提交反馈"
      footer={
        <CSBox float="right">
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton variant="link" onClick={onClose} disabled={busy}>
              {done ? '关闭' : '取消'}
            </CSButton>
            {!done && (
              <CSButton
                variant="primary"
                onClick={handleSubmit}
                loading={busy}
                disabled={!canSubmit}
              >
                提交
              </CSButton>
            )}
          </CSSpaceBetween>
        </CSBox>
      }
    >
      <CSSpaceBetween size="m">
        {/* ── 红线警告 ── */}
        <CSAlert type="warning" header="反馈渠道内容限制">
          反馈渠道不得包含性、露骨性、NSFW 或其他成人专属材料（无论你是否年满 18 周岁）。
          违反将导致永久终止账号并加入禁注表。详见{' '}
          <a href={AUP_LINK} target="_blank" rel="noopener noreferrer">AUP §2.J</a>。
        </CSAlert>

        {/* 反馈处理回执:管理员处理过(unacked)的反馈,关抽屉时清"已读" */}
        {newlyReviewed.length > 0 && (
          <CSAlert
            type={newlyReviewed.some(r => r.review_decision === 'nsfw_terminate') ? 'warning' : 'success'}
            header={`管理员已处理你的 ${newlyReviewed.length} 条反馈`}>
            <CSSpaceBetween size="xxs">
              {newlyReviewed.slice(0, 5).map(r => (
                <CSBox key={r.id} fontSize="body-s">
                  <strong>#{r.id}</strong>
                  {' · '}
                  {r.review_decision === 'ok' ? '✓ 已采纳' :
                   r.review_decision === 'spam' ? '✗ 标为 spam' :
                   r.review_decision === 'nsfw_terminate' ? '⚠ NSFW 违规' :
                   r.review_decision}
                  {' · '}
                  <span style={{ color: 'var(--color-text-body-secondary)' }}>
                    {(r.free_text_preview || '').slice(0, 60)}{(r.free_text_preview || '').length > 60 ? '…' : ''}
                  </span>
                </CSBox>
              ))}
              {newlyReviewed.length > 5 && (
                <CSBox fontSize="body-s" color="text-body-secondary">
                  还有 {newlyReviewed.length - 5} 条…
                </CSBox>
              )}
            </CSSpaceBetween>
          </CSAlert>
        )}

        {done ? (
          <CSAlert type="success" header="已收到您的反馈">
            感谢您的反馈！我们会在审核后处理。
          </CSAlert>
        ) : (
          <>
            {error && (
              <CSAlert type="error" header="提交失败">
                {error}
              </CSAlert>
            )}

            {/* ── 自由文本 ── */}
            <CSFormField
              label="问题 / 建议"
              description={`最多 ${MAX_FREE_TEXT} 字`}
              errorText={freeText.length > MAX_FREE_TEXT ? `超过 ${MAX_FREE_TEXT} 字限制` : undefined}
            >
              <CSTextarea
                value={freeText}
                onChange={({ detail }) => setFreeText(detail.value)}
                placeholder="请描述您遇到的问题或建议…"
                rows={6}
                disabled={busy}
              />
            </CSFormField>

            {/* ── 运行环境切片(默认 ON,bug 排查必备)── */}
            <CSCheckbox
              checked={includeRuntime}
              onChange={({ detail }) => setIncludeRuntime(detail.checked)}
              disabled={busy}
            >
              附带运行环境信息(强烈建议:页面 URL + 活动剧本/存档 + 最近错误 + 最近 3 轮对话,只发给管理员)
            </CSCheckbox>
            {includeRuntime && runtimePreview && (
              <CSContainer
                header={<CSHeader variant="h3" description="只对管理员可见,不会公开">运行环境切片预览</CSHeader>}>
                <CSBox fontSize="body-s" color="text-body-secondary">
                  <div>页面: <code>{runtimePreview.hash || runtimePreview.url || '—'}</code></div>
                  <div>剧本/存档: script={String(runtimePreview.active?.script_id ?? '—')} · save={String(runtimePreview.active?.save_id ?? '—')} · turn={String(runtimePreview.active?.turn ?? '—')}</div>
                  <div>错误堆栈: {runtimePreview.errors?.length || 0} 条 · 失败 API: {runtimePreview.api_failures?.length || 0} 条</div>
                  <div>视窗: {runtimePreview.viewport} · 语言: {runtimePreview.locale} · 时区: {runtimePreview.tz}</div>
                </CSBox>
              </CSContainer>
            )}

            {/* ── 节选选项 ── */}
            <CSCheckbox
              checked={includeExcerpts}
              onChange={({ detail }) => setIncludeExcerpts(detail.checked)}
              disabled={busy}
            >
              包含对话节选
            </CSCheckbox>

            {includeExcerpts && (
              <CSContainer
                header={<CSHeader variant="h3">选择要包含的对话节选（最多 5 段）</CSHeader>}
              >
                {recentTurns.length === 0 ? (
                  <CSBox color="text-body-secondary">暂无可用对话节选</CSBox>
                ) : (
                  <CSSpaceBetween size="xs">
                    {recentTurns.map((t) => (
                      <CSCheckbox
                        key={t.idx}
                        checked={selectedExcerpts.includes(t.idx)}
                        onChange={() => toggleExcerpt(t.idx)}
                        disabled={busy}
                      >
                        <CSBox>
                          <strong>{t.label}</strong>
                          <CSBox color="text-body-secondary" fontSize="body-s">
                            {t.plaintext.slice(0, 80)}{t.plaintext.length > 80 ? '…' : ''}
                          </CSBox>
                        </CSBox>
                      </CSCheckbox>
                    ))}
                  </CSSpaceBetween>
                )}
              </CSContainer>
            )}

            {/* ── 同意复选框 ── */}
            <CSFormField
              errorText={!consent && freeText.trim() ? '请先勾选同意以启用提交' : undefined}
            >
              <CSCheckbox
                checked={consent}
                onChange={({ detail }) => setConsent(detail.checked)}
                disabled={busy}
              >
                {CONSENT_TEXT}
              </CSCheckbox>
            </CSFormField>
          </>
        )}

        {/* ── 玩家交流 QQ 群 ── */}
        <CSContainer header={<CSHeader variant="h3">玩家交流群</CSHeader>}>
          <CSSpaceBetween size="s">
            <CSBox fontSize="body-s" color="text-body-secondary">
              遇到问题、想交流玩法，欢迎加入玩家 QQ 群（群号 {QQ_GROUP_NUMBER}）。
            </CSBox>
            <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
              <img
                src={QQ_QR_SRC}
                alt={`QQ 群二维码 ${QQ_GROUP_NUMBER}`}
                loading="lazy"
                style={{ width: 150, height: 'auto', borderRadius: 10, border: '1px solid var(--color-border-divider-default, #2a2e33)' }}
              />
              <CSSpaceBetween size="xs">
                <CSButton variant="primary" href={QQ_JOIN_URL} target="_blank" iconName="external">
                  用 QQ 加入群聊
                </CSButton>
                <CSBox fontSize="body-s" color="text-body-secondary">
                  或在 QQ 中搜索群号 {QQ_GROUP_NUMBER}
                </CSBox>
              </CSSpaceBetween>
            </div>
          </CSSpaceBetween>
        </CSContainer>

        <CSExpandableSection
          variant="container"
          defaultExpanded={false}
          headerText="历史反馈"
          headerCounter={feedbackHistory.length ? `(${feedbackHistory.length})` : undefined}
          headerActions={<CSButton iconName="refresh" onClick={loadFeedbackHistory} loading={historyLoading}>刷新</CSButton>}
        >
          {historyError ? (
            <CSAlert type="error" header="历史反馈读取失败">{historyError}</CSAlert>
          ) : historyLoading && feedbackHistory.length === 0 ? (
            <CSBox color="text-body-secondary">正在读取历史反馈…</CSBox>
          ) : feedbackHistory.length === 0 ? (
            <CSBox color="text-body-secondary">暂无历史反馈</CSBox>
          ) : (
            <CSSpaceBetween size="xs">
              {feedbackHistory.map((item) => (
                <CSAlert
                  key={item.id}
                  type={feedbackStatusType(item.review_decision)}
                  header={`#${item.id} · ${feedbackStatusLabel(item.review_decision)}`}
                >
                  <CSSpaceBetween size="xxs">
                    <CSBox fontSize="body-s" color="text-body-secondary">
                      提交: {formatFeedbackTime(item.created_at)}
                      {item.reviewed_at ? ` · 处理: ${formatFeedbackTime(item.reviewed_at)}` : ''}
                    </CSBox>
                    <CSBox fontSize="body-s">
                      {item.free_text_preview || '（无文字内容）'}
                    </CSBox>
                    {item.admin_reply && (
                      <div style={{ marginTop: 4, padding: '6px 10px', borderRadius: 6, background: 'rgba(74,120,214,0.12)', borderLeft: '3px solid #4a78d6', fontSize: 13, lineHeight: 1.5 }}>
                        <strong>官方回复</strong>{item.replied_at ? ` · ${formatFeedbackTime(item.replied_at)}` : ''}
                        <div style={{ marginTop: 2, whiteSpace: 'pre-wrap' }}>{item.admin_reply}</div>
                      </div>
                    )}
                  </CSSpaceBetween>
                </CSAlert>
              ))}
            </CSSpaceBetween>
          )}
        </CSExpandableSection>
      </CSSpaceBetween>
    </CSModal>
  );
}

// ── FeedbackDrawerRoot — 挂全局，监听 window.__openFeedback ─────────────────

const OPEN_EVENT = 'feedback:open';

export function FeedbackDrawerRoot() {
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => {
    window.__openFeedback = () => {
      window.dispatchEvent(new CustomEvent(OPEN_EVENT));
    };
    const handler = () => setOpen(true);
    window.addEventListener(OPEN_EVENT, handler);
    return () => {
      window.removeEventListener(OPEN_EVENT, handler);
      delete window.__openFeedback;
    };
  }, []);

  return <FeedbackDrawer open={open} onClose={() => setOpen(false)} />;
}

export default FeedbackDrawer;
