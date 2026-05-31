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

// ── 常量 ─────────────────────────────────────────────────────────────────────

const CONSENT_TEXT =
  '我已阅读 AUP §2.J，理解不得包含成人主题节选，同意（此操作记录我的同意）';

const AUP_LINK = 'https://play.stellatrix.icu/legal/aup#2J';

const MAX_FREE_TEXT = 10000;

// ── SHA256 工具 ───────────────────────────────────────────────────────────────

async function sha256hex(text) {
  const encoder = new TextEncoder();
  const data = encoder.encode(text);
  const buf = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

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

  // 打开时重置状态
  React.useEffect(() => {
    if (!open) return;
    setFreeText('');
    setIncludeExcerpts(false);
    setSelectedExcerpts([]);
    setConsent(false);
    setBusy(false);
    setDone(false);
    setError(null);
  }, [open]);

  // 加载当前会话最近 5 段对话摘要
  React.useEffect(() => {
    if (!open || !includeExcerpts) return;
    let cancelled = false;
    (async () => {
      try {
        // 从游戏 state 拉最近对话，适配现有 window.api 结构
        const state = await window.api?.getState?.();
        const nodes = state?.branch_nodes || state?.turns || [];
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
    } catch (e) {
      setError(e?.message || '提交失败，请稍后重试');
    } finally {
      setBusy(false);
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
