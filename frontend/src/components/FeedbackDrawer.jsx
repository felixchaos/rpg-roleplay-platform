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
import { useTranslation } from 'react-i18next';
import CSModal        from '@cloudscape-design/components/modal';
import CSBox          from '@cloudscape-design/components/box';
import CSButton       from '@cloudscape-design/components/button';
import CSAlert        from '@cloudscape-design/components/alert';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSTextarea     from '@cloudscape-design/components/textarea';
import CSInput        from '@cloudscape-design/components/input';
import CSCheckbox     from '@cloudscape-design/components/checkbox';
import CSFormField    from '@cloudscape-design/components/form-field';
import CSContainer    from '@cloudscape-design/components/container';
import CSHeader       from '@cloudscape-design/components/header';
import CSExpandableSection from '@cloudscape-design/components/expandable-section';
// 法务/渠道常量 + 提交内核上提到 lib/feedback.js(语义统一 #22)。
// AUP / QQ / MAX_FREE_TEXT 各端逐字一致 → 共享;CONSENT_TEXT 本组件历史用全角标点版,
// 显示文案与其它端不同,刻意保留(提交时作为 consentText 传入 submitFeedback;
// 后端只校验 64-hex,不校验等于某文案的 SHA256,故 token 差异无副作用)。
import { AUP_LINK, MAX_FREE_TEXT, QQ_GROUP_NUMBER, QQ_JOIN_URL, QQ_QR_SRC, submitFeedback, feedbackDecisionLabel } from '../lib/feedback.js';
import { lsGet, lsSet } from '../lib/storage.js';
import { fetchIsSelfHost } from '../lib/deployment.js';

// ── 常量 ─────────────────────────────────────────────────────────────────────

const CONSENT_TEXT =
  '我已阅读 AUP §2.J，理解不得包含成人主题节选，同意（此操作记录我的同意）';

// ── SHA256 工具 ───────────────────────────────────────────────────────────────
// crypto.subtle 仅在安全上下文(HTTPS/localhost)可用;明文 HTTP LAN 访问下为
// undefined,旧实现会抛错使反馈提交失败。改用 crypto-safe 的降级封装(安全上下文
// 走真 SHA-256,否则确定性 64-hex 兜底,满足后端 consent_token 契约)。

// ── FeedbackDrawer ────────────────────────────────────────────────────────────

export function FeedbackDrawer({ open, onClose }) {
  const { t } = useTranslation();
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
  // 自部署(local/desktop)模式:反馈转发到中央服务器,显示「选填联系邮箱」用于收回执。
  const [selfHost, setSelfHost] = React.useState(false);
  const [contactEmail, setContactEmail] = React.useState('');
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
      const lastSeenStr = lsGet('feedback_last_seen_id') || '0';
      const lastSeen = parseInt(lastSeenStr, 10) || 0;
      const res = await fetch('/api/me/feedback?limit=50', { credentials: 'include' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data || !data.ok) throw new Error(data?.error || t('components.feedback_drawer.history.load_error'));
      const items = Array.isArray(data.items) ? data.items : [];
      setFeedbackHistory(items);
      setNewlyReviewed(items.filter(it => it.review_decision && it.id > lastSeen));
      setMaxReviewedId(items.reduce((m, it) => Math.max(m, it.id || 0), 0));
    } catch (e) {
      setHistoryError(e?.message || t('components.feedback_drawer.history.load_error'));
    } finally {
      setHistoryLoading(false);
    }
  }, [t]);

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
    setRecentTurns([]);  // 防跨存档残留上一次的对话节选
    setContactEmail('');
    try {
      const snap = window.__getRuntimeSnapshot && window.__getRuntimeSnapshot();
      setRuntimePreview(snap ? snap.__runtime__ : null);
    } catch (_) { setRuntimePreview(null); }
    // 探测部署模式:自部署时反馈走中央服务器,需要选填邮箱来收回执。
    // 判定集合住 lib/deployment.js(单一真相源,与后端 core.config.LOCAL_MODES 对齐)。
    (async () => { setSelfHost(await fetchIsSelfHost()); })();
    loadFeedbackHistory();
  }, [open, loadFeedbackHistory]);

  // 关闭时标记当前最大 feedback id 为已读 — 下次开抽屉就不再提示这些
  React.useEffect(() => {
    if (open) return;
    if (maxReviewedId > 0) {
      lsSet('feedback_last_seen_id', String(maxReviewedId));
    }
  }, [open, maxReviewedId]);

  // 加载当前会话最近 5 段对话摘要
  React.useEffect(() => {
    if (!open || !includeExcerpts) return;
    let cancelled = false;
    (async () => {
      try {
        // 后端 history 节点形如 {role:'user'|'assistant', content}（core.py:559-560 +
        // DB schema role in user/assistant/system/tool）。
        // 必须现拉一次 /api/state 拿权威最新对话 —— MOCK_STATE.history 只在页面 boot/
        // __refreshPlatform 时灌一次,游戏进行中不更新,直接读会拿到开局快照(陈旧)。
        // /state 失败或为空时才回退到 MOCK_STATE。
        let nodes = null;
        let saveId = '';
        try {
          const state = await window.api?.game?.state?.();
          nodes = state?.history || state?.branch_nodes || state?.turns || null;
          saveId = state?.save_id || state?._raw?.save_id || '';
        } catch (_) { /* 下面回退 */ }
        if (!Array.isArray(nodes) || nodes.length === 0) {
          if (window.MOCK_STATE && Array.isArray(window.MOCK_STATE.history)) nodes = window.MOCK_STATE.history;
          saveId = saveId || window.MOCK_STATE?._raw?.save_id || '';
        }
        // GM 消息 role 是 'assistant'(不是 'gm');保留 user/assistant/gm,丢掉 system/tool 与空内容。
        const recent = (Array.isArray(nodes) ? nodes : [])
          .filter((n) => n && (n.role === 'user' || n.role === 'assistant' || n.role === 'gm') && (n.content || n.text));
        const turns = recent.slice(-6).map((n, i) => ({
          idx: i,
          session_id: saveId,
          range: String(n.turn_index ?? n.turn ?? i),
          plaintext: ((n.content || n.text || '') + '').slice(0, 200),
          label: n.role === 'user' ? t('components.feedback_drawer.excerpt.role_player') : 'GM',
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
      // 已选对话节选;运行环境快照 + consent_token + POST /api/feedback 走共享内核
      // submitFeedback(语义统一 #22)。__runtime__ 由内核在 includeRuntime 时追加。
      const excerpts = includeExcerpts
        ? recentTurns
            .filter((t) => selectedExcerpts.includes(t.idx))
            .map(({ session_id, range, plaintext }) => ({ session_id, range, plaintext }))
        : [];
      await submitFeedback({ freeText, excerpts, consentText: CONSENT_TEXT, includeRuntime, includeRecentDialog: true, contactEmail: contactEmail.trim() });
      setDone(true);
      await loadFeedbackHistory();
    } catch (e) {
      setError(e?.message || t('components.feedback_drawer.submit_error'));
    } finally {
      setBusy(false);
    }
  }

  const feedbackStatusLabel = feedbackDecisionLabel;  // 语义统一 #26:共享 lib/feedback.js

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
      header={t('components.feedback_drawer.title')}
      footer={
        <CSBox float="right">
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton variant="link" onClick={onClose} disabled={busy}>
              {done ? t('common.close') : t('common.cancel')}
            </CSButton>
            {!done && (
              <CSButton
                variant="primary"
                onClick={handleSubmit}
                loading={busy}
                disabled={!canSubmit}
              >
                {t('components.feedback_drawer.submit_btn')}
              </CSButton>
            )}
          </CSSpaceBetween>
        </CSBox>
      }
    >
      <CSSpaceBetween size="m">
        {/* ── 红线警告 ── */}
        <CSAlert type="warning" header={t('components.feedback_drawer.aup_warning.header')}>
          {t('components.feedback_drawer.aup_warning.body')}{' '}
          <a href={AUP_LINK} target="_blank" rel="noopener noreferrer">AUP §2.J</a>。
        </CSAlert>

        {/* 反馈处理回执:管理员处理过(unacked)的反馈,关抽屉时清"已读" */}
        {newlyReviewed.length > 0 && (
          <CSAlert
            type={newlyReviewed.some(r => r.review_decision === 'nsfw_terminate') ? 'warning' : 'success'}
            header={t('components.feedback_drawer.reviewed_notice.header', { count: newlyReviewed.length })}>
            <CSSpaceBetween size="xxs">
              {newlyReviewed.slice(0, 5).map(r => (
                <CSBox key={r.id} fontSize="body-s">
                  <strong>#{r.id}</strong>
                  {' · '}
                  {r.review_decision === 'ok' ? t('components.feedback_drawer.reviewed_notice.decision_ok') :
                   r.review_decision === 'spam' ? t('components.feedback_drawer.reviewed_notice.decision_spam') :
                   r.review_decision === 'nsfw_terminate' ? t('components.feedback_drawer.reviewed_notice.decision_nsfw') :
                   r.review_decision}
                  {' · '}
                  <span style={{ color: 'var(--color-text-body-secondary)' }}>
                    {(r.free_text_preview || '').slice(0, 60)}{(r.free_text_preview || '').length > 60 ? '…' : ''}
                  </span>
                </CSBox>
              ))}
              {newlyReviewed.length > 5 && (
                <CSBox fontSize="body-s" color="text-body-secondary">
                  {t('components.feedback_drawer.reviewed_notice.more', { count: newlyReviewed.length - 5 })}
                </CSBox>
              )}
            </CSSpaceBetween>
          </CSAlert>
        )}

        {done ? (
          <CSAlert type="success" header={t('components.feedback_drawer.success.header')}>
            {t('components.feedback_drawer.success.body')}
          </CSAlert>
        ) : (
          <>
            {error && (
              <CSAlert type="error" header={t('components.feedback_drawer.error.header')}>
                {error}
              </CSAlert>
            )}

            {/* ── 自由文本 ── */}
            <CSFormField
              label={t('components.feedback_drawer.freetext.label')}
              description={t('components.feedback_drawer.freetext.description', { max: MAX_FREE_TEXT })}
              errorText={freeText.length > MAX_FREE_TEXT ? t('components.feedback_drawer.freetext.over_limit', { max: MAX_FREE_TEXT }) : undefined}
            >
              <CSTextarea
                value={freeText}
                onChange={({ detail }) => setFreeText(detail.value)}
                placeholder={t('components.feedback_drawer.freetext.placeholder')}
                rows={6}
                disabled={busy}
              />
            </CSFormField>

            {/* ── 自部署:选填联系邮箱(用于收处理回执)── */}
            {selfHost && (
              <CSFormField
                label={t('components.feedback_drawer.contact_email.label')}
                description={t('components.feedback_drawer.contact_email.description')}
              >
                <CSInput
                  value={contactEmail}
                  onChange={({ detail }) => setContactEmail(detail.value)}
                  placeholder="you@example.com"
                  type="email"
                  inputMode="email"
                  disabled={busy}
                />
              </CSFormField>
            )}

            {/* ── 运行环境切片(默认 ON,bug 排查必备)── */}
            <CSCheckbox
              checked={includeRuntime}
              onChange={({ detail }) => setIncludeRuntime(detail.checked)}
              disabled={busy}
            >
              {t('components.feedback_drawer.runtime.checkbox_label')}
            </CSCheckbox>
            {includeRuntime && runtimePreview && (
              <CSContainer
                header={<CSHeader variant="h3" description={t('components.feedback_drawer.runtime.preview_description')}>{t('components.feedback_drawer.runtime.preview_header')}</CSHeader>}>
                <CSBox fontSize="body-s" color="text-body-secondary">
                  <div>{t('components.feedback_drawer.runtime.page')}: <code>{runtimePreview.hash || runtimePreview.url || '—'}</code></div>
                  <div>{t('components.feedback_drawer.runtime.script_save')}: script={String(runtimePreview.active?.script_id ?? '—')} · save={String(runtimePreview.active?.save_id ?? '—')} · turn={String(runtimePreview.active?.turn ?? '—')}</div>
                  <div>{t('components.feedback_drawer.runtime.errors')}: {runtimePreview.errors?.length || 0} · {t('components.feedback_drawer.runtime.failed_api')}: {runtimePreview.api_failures?.length || 0}</div>
                  <div>{t('components.feedback_drawer.runtime.viewport')}: {runtimePreview.viewport} · {t('components.feedback_drawer.runtime.locale')}: {runtimePreview.locale} · {t('components.feedback_drawer.runtime.tz')}: {runtimePreview.tz}</div>
                </CSBox>
              </CSContainer>
            )}

            {/* ── 节选选项 ── */}
            <CSCheckbox
              checked={includeExcerpts}
              onChange={({ detail }) => setIncludeExcerpts(detail.checked)}
              disabled={busy}
            >
              {t('components.feedback_drawer.excerpt.checkbox_label')}
            </CSCheckbox>

            {includeExcerpts && (
              <CSContainer
                header={<CSHeader variant="h3">{t('components.feedback_drawer.excerpt.section_header')}</CSHeader>}
              >
                {recentTurns.length === 0 ? (
                  <CSBox color="text-body-secondary">{t('components.feedback_drawer.excerpt.empty')}</CSBox>
                ) : (
                  <CSSpaceBetween size="xs">
                    {recentTurns.map((turn) => (
                      <CSCheckbox
                        key={turn.idx}
                        checked={selectedExcerpts.includes(turn.idx)}
                        onChange={() => toggleExcerpt(turn.idx)}
                        disabled={busy}
                      >
                        <CSBox>
                          <strong>{turn.label}</strong>
                          <CSBox color="text-body-secondary" fontSize="body-s">
                            {turn.plaintext.slice(0, 80)}{turn.plaintext.length > 80 ? '…' : ''}
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
              errorText={!consent && freeText.trim() ? t('components.feedback_drawer.consent.error') : undefined}
            >
              <CSCheckbox
                checked={consent}
                onChange={({ detail }) => setConsent(detail.checked)}
                disabled={busy}
              >
                {t('components.feedback_drawer.consent.label')}
              </CSCheckbox>
            </CSFormField>
          </>
        )}

        {/* ── 玩家交流 QQ 群 ── */}
        <CSContainer header={<CSHeader variant="h3">{t('components.feedback_drawer.qq.header')}</CSHeader>}>
          <CSSpaceBetween size="s">
            <CSBox fontSize="body-s" color="text-body-secondary">
              {t('components.feedback_drawer.qq.description', { group: QQ_GROUP_NUMBER })}
            </CSBox>
            <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
              <img
                src={QQ_QR_SRC}
                alt={t('components.feedback_drawer.qq.qr_alt', { group: QQ_GROUP_NUMBER })}
                loading="lazy"
                style={{ width: 150, height: 'auto', borderRadius: 10, border: '1px solid var(--color-border-divider-default, #2a2e33)' }}
              />
              <CSSpaceBetween size="xs">
                <CSButton variant="primary" href={QQ_JOIN_URL} target="_blank" iconName="external">
                  {t('components.feedback_drawer.qq.join_btn')}
                </CSButton>
                <CSBox fontSize="body-s" color="text-body-secondary">
                  {t('components.feedback_drawer.qq.search_hint', { group: QQ_GROUP_NUMBER })}
                </CSBox>
              </CSSpaceBetween>
            </div>
          </CSSpaceBetween>
        </CSContainer>

        <CSExpandableSection
          variant="container"
          defaultExpanded={false}
          headerText={t('components.feedback_drawer.history.header')}
          headerCounter={feedbackHistory.length ? `(${feedbackHistory.length})` : undefined}
          headerActions={<CSButton iconName="refresh" onClick={loadFeedbackHistory} loading={historyLoading}>{t('common.refresh')}</CSButton>}
        >
          {historyError ? (
            <CSAlert type="error" header={t('components.feedback_drawer.history.error_header')}>{historyError}</CSAlert>
          ) : historyLoading && feedbackHistory.length === 0 ? (
            <CSBox color="text-body-secondary">{t('components.feedback_drawer.history.loading')}</CSBox>
          ) : feedbackHistory.length === 0 ? (
            <CSBox color="text-body-secondary">{t('components.feedback_drawer.history.empty')}</CSBox>
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
                      {t('components.feedback_drawer.history.submitted_at')}: {formatFeedbackTime(item.created_at)}
                      {item.reviewed_at ? ` · ${t('components.feedback_drawer.history.reviewed_at')}: ${formatFeedbackTime(item.reviewed_at)}` : ''}
                    </CSBox>
                    <CSBox fontSize="body-s">
                      {item.free_text_preview || t('components.feedback_drawer.history.no_text')}
                    </CSBox>
                    {item.admin_reply && (
                      <div style={{ marginTop: 4, padding: '6px 10px', borderRadius: 6, background: 'rgba(74,120,214,0.12)', borderLeft: '3px solid #4a78d6', fontSize: 13, lineHeight: 1.5 }}>
                        <strong>{t('components.feedback_drawer.history.admin_reply')}</strong>{item.replied_at ? ` · ${formatFeedbackTime(item.replied_at)}` : ''}
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
