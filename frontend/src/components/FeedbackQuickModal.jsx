/**
 * FeedbackQuickModal — 顶栏「反馈」快速反馈弹窗(Cloudscape Modal,取代旧 FeedbackDrawer)。
 * 定位:快速提交「新反馈」;完整历史 + 全字段在 /feedback 页(本弹窗底部「查看全部反馈」可跳)。
 * 复用后端契约 POST /api/feedback(同 FeedbackPage)。
 */
import React from 'react';
import CSModal from '@cloudscape-design/components/modal';
import CSButton from '@cloudscape-design/components/button';
import CSBox from '@cloudscape-design/components/box';
import CSAlert from '@cloudscape-design/components/alert';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSTextarea from '@cloudscape-design/components/textarea';
import CSCheckbox from '@cloudscape-design/components/checkbox';
import CSFormField from '@cloudscape-design/components/form-field';
import { sha256hex } from '../lib/crypto-safe.js';
import { plNavigate } from '../router.js';

const CONSENT_TEXT = '我已阅读 AUP §2.J,理解不得包含成人主题节选,同意(此操作记录我的同意)';
const AUP_LINK = 'https://play.stellatrix.icu/legal/aup#2J';
const MAX_FREE_TEXT = 10000;
const QQ_GROUP_NUMBER = '584876566';
const QQ_JOIN_URL = 'https://qm.qq.com/q/49Dqcr0aw0';
const QQ_QR_SRC = '/qq-group.jpg';

export function FeedbackQuickModal({ open, onClose }) {
  const [freeText, setFreeText] = React.useState('');
  const [includeRuntime, setIncludeRuntime] = React.useState(true);
  const [consent, setConsent] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [done, setDone] = React.useState(false);
  const [error, setError] = React.useState(null);

  // 关闭后复位(下次打开是干净表单)
  React.useEffect(() => {
    if (!open) {
      const t = setTimeout(() => { setFreeText(''); setConsent(false); setDone(false); setError(null); setBusy(false); }, 200);
      return () => clearTimeout(t);
    }
  }, [open]);

  const canSubmit = consent && freeText.trim().length > 0 && freeText.length <= MAX_FREE_TEXT && !busy;

  async function submit() {
    if (!canSubmit) return;
    setBusy(true); setError(null);
    try {
      const token = await sha256hex(CONSENT_TEXT);
      const excerpts = [];
      if (includeRuntime) {
        try {
          let freshHistory = null;
          try {
            const st = await window.api?.game?.state?.();
            if (st && Array.isArray(st.history)) freshHistory = st.history;
          } catch (_) {}
          const snap = window.__getRuntimeSnapshot && window.__getRuntimeSnapshot({ includeRecentDialog: true, recentDialog: freshHistory });
          if (snap && snap.__runtime__) excerpts.push(snap);
        } catch (_) {}
      }
      const res = await fetch('/api/feedback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
        body: JSON.stringify({ free_text: freeText, excerpts, consent_token: token, app_version: window.__APP_VERSION__ || '' }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      setDone(true); setFreeText(''); setConsent(false);
      window.__apiToast?.('反馈已提交,感谢!', { kind: 'ok', duration: 2200 });
    } catch (e) { setError(e?.message || '提交失败,请稍后重试'); }
    finally { setBusy(false); }
  }

  const gotoFull = () => { onClose?.(); plNavigate('feedback'); };

  return (
    <CSModal
      visible={!!open}
      onDismiss={onClose}
      header="快速反馈"
      footer={
        <CSBox float="right">
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton variant="link" onClick={gotoFull}>查看全部反馈 / 历史</CSButton>
            <CSButton onClick={onClose}>关闭</CSButton>
            <CSButton variant="primary" onClick={submit} loading={busy} disabled={!canSubmit}>提交反馈</CSButton>
          </CSSpaceBetween>
        </CSBox>
      }
    >
      <CSSpaceBetween size="m">
        {done
          ? <CSAlert type="success" header="已收到你的反馈">感谢反馈!可在「查看全部反馈 / 历史」里跟进处理进度,或继续补充。</CSAlert>
          : error && <CSAlert type="error" header="提交失败">{error}</CSAlert>}
        <CSFormField label="问题 / 建议" description={`最多 ${MAX_FREE_TEXT} 字 · 复现步骤 / 期望 / 实际 越具体越好`}
          errorText={freeText.length > MAX_FREE_TEXT ? `超过 ${MAX_FREE_TEXT} 字限制` : undefined}>
          <CSTextarea value={freeText} onChange={({ detail }) => setFreeText(detail.value)} placeholder="请描述你遇到的问题或建议…" rows={5} disabled={busy} autoFocus />
        </CSFormField>
        <CSCheckbox checked={includeRuntime} onChange={({ detail }) => setIncludeRuntime(detail.checked)} disabled={busy}>
          附带运行环境信息(页面 / 活动剧本存档 / 最近错误 / 最近对话,仅管理员可见,便于排查)
        </CSCheckbox>
        <CSFormField errorText={!consent && freeText.trim() ? '请先勾选同意以启用提交' : undefined}>
          <CSCheckbox checked={consent} onChange={({ detail }) => setConsent(detail.checked)} disabled={busy}>{CONSENT_TEXT}</CSCheckbox>
        </CSFormField>
        <CSBox fontSize="body-s" color="text-body-secondary">
          反馈渠道不得包含成人材料;违反按 <a href={AUP_LINK} target="_blank" rel="noopener noreferrer">AUP §2.J</a> 处理。需要附对话节选 / 查看历史请用「查看全部反馈」。
        </CSBox>

        {/* 玩家交流群 — 快速反馈也别丢这条 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 12px', border: '1px solid var(--line, #36322d)', borderRadius: 10, background: 'var(--panel, #211f1d)' }}>
          <img src={QQ_QR_SRC} alt={`QQ 群二维码 ${QQ_GROUP_NUMBER}`} loading="lazy" style={{ width: 64, height: 64, borderRadius: 8, border: '1px solid var(--line, #36322d)', flexShrink: 0 }} />
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>玩家交流群</div>
            <div style={{ fontSize: 12, color: 'var(--text-quiet, #968f85)', marginTop: 2 }}>想交流玩法 / 即时求助,欢迎加 QQ 群 {QQ_GROUP_NUMBER}。</div>
          </div>
          <CSButton variant="normal" iconName="external" href={QQ_JOIN_URL} target="_blank">加入群聊</CSButton>
        </div>
      </CSSpaceBetween>
    </CSModal>
  );
}

export default FeedbackQuickModal;
