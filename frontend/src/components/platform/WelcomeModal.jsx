// 使用须知 / 站内公告弹窗。纯机械从 platform-app.jsx 搬出,零行为变化。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import { plNavigate } from '../../router.js';
import Modal from '../Modal.jsx';
import {
  publishUser, useReactiveUser,
} from './shared.jsx';

/* ---------------------------- WELCOME MODAL -------------------- */
/* 站内更新公告(复用使用须知弹窗展示)。规则:进站弹一次 → 看过即记 localStorage → 绝不主动二次弹;
   只有点「使用须知」按钮才会再打开。发布新公告时改 version(旧 localStorage 不匹配 → 对所有人再弹一次)。 */
const SITE_ANNOUNCEMENT = {
  // 文案走 i18n(platform.shell.announce.*),在 WelcomeModal 渲染处用 t() 取;
  // version 改了即对所有人再弹一次(announcementUnseen 比对此值)。
  version: '2026-06-18-temporal-kb',
};
const ANNOUNCEMENT_SEEN_KEY = 'rpg_announcement_seen';
function announcementUnseen() {
  try { return localStorage.getItem(ANNOUNCEMENT_SEEN_KEY) !== SITE_ANNOUNCEMENT.version; }
  catch (_) { return false; }
}
function markAnnouncementSeen() {
  try { localStorage.setItem(ANNOUNCEMENT_SEEN_KEY, SITE_ANNOUNCEMENT.version); } catch (_) { /* 隐私模式忽略 */ }
}

/* 使用须知弹窗 — 新用户首次进入 Platform 时弹一次，也可从「📖 使用须知」按钮随时再看。
   onDismiss: 首次弹（firstTime=true）时调用后端写入 welcome_dismissed_at；
              手动再打开时（firstTime=false）直接关，不再重复写后端。
   顶部叠加「站内更新公告」(SITE_ANNOUNCEMENT),随弹窗一同展示。 */
function WelcomeModal({ open, firstTime = false, onClose }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useStatePL(false);
  const reactiveUserWM = useReactiveUser();
  // co_builder 复选框：默认勾选（= 参加），取消勾选写入 opt_out=true
  // 不用懒初始化——user 数据到达前 reactiveUserWM 为 null，捕获的 opt_out 是 undefined → 恒 true
  const [coBuilderChecked, setCoBuilderChecked] = useStatePL(true);
  useEffectPL(() => {
    if (reactiveUserWM?.co_builder_opt_out != null) {
      setCoBuilderChecked(!reactiveUserWM.co_builder_opt_out);
    }
  }, [reactiveUserWM]);
  // 是否展示 co_builder 区：仅 firstTime + 普通用户（非 admin/vip_user）+ 已通过白名单注册
  const isRegularUser = reactiveUserWM && reactiveUserWM.role === 'user';
  const showCoBuilder = firstTime && isRegularUser && reactiveUserWM?.is_co_builder === true;

  const handleClose = async () => {
    if (firstTime && !busy) {
      setBusy(true);
      try {
        await fetch('/api/me/welcome-dismiss', { method: 'PATCH', credentials: 'include' });
      } catch (_) { /* 非致命，忽略 */ }
      // 如果展示了 co_builder 区，顺便提交 opt_out 选择
      if (showCoBuilder) {
        try {
          await fetch('/api/me/profile', {
            method: 'PATCH',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ co_builder_opt_out: !coBuilderChecked }),
          });
          // 更新本地 user 状态，避免设置页显示旧值
          publishUser({ co_builder_opt_out: !coBuilderChecked });
        } catch (_) { /* 非致命 */ }
      }
      setBusy(false);
    }
    onClose();
  };

  const handleGoSettings = async () => {
    await handleClose();
    plNavigate('settings-models');
  };

  if (!open) return null;

  const Section = ({ title, body }) => (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 4 }}>{title}</div>
      <div style={{ fontSize: 13, lineHeight: 1.65, color: 'var(--text-quiet)' }}>{body}</div>
    </div>
  );

  return (
    <Modal
      open
      eyebrow={t('platform.welcome.eyebrow')}
      title={t('platform.welcome.title')}
      width={520}
      panelStyle={{ maxHeight: '90vh', overflowY: 'auto' }}
      closeDisabled={busy}
      onClose={handleClose}
      footer={<>
        <span />
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn ghost" onClick={handleGoSettings} disabled={busy}>
            {t('platform.welcome.go_settings')}
          </button>
          <button className="btn primary" onClick={handleClose} disabled={busy}>
            <Icon name="check" size={12} /> {t('platform.welcome.close_btn')}
          </button>
        </div>
      </>}
    >
        <div style={{ padding: '4px 0 16px' }}>
          {/* 站内更新公告(置顶醒目) */}
          <div style={{ background: 'rgba(196,155,78,0.10)', border: '1px solid rgba(196,155,78,0.35)', borderRadius: 8, padding: '10px 14px', marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--accent, #c49b4e)', marginBottom: 6 }}>
              {t('platform.shell.announce.update')} · {t('platform.shell.announce.title')}
            </div>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.7, color: 'var(--text-quiet)' }}>
              {[1, 2, 3].map((i) => <li key={i}>{t(`platform.shell.announce.line_${i}`)}</li>)}
            </ul>
            <div style={{ fontSize: 12, color: 'var(--text-quiet)', marginTop: 6 }}>{t('platform.shell.announce.note')}</div>
          </div>
          {/* 测试期免责 */}
          <div style={{ background: 'rgba(220,80,60,0.10)', border: '1px solid rgba(220,80,60,0.3)', borderRadius: 8, padding: '10px 14px', marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#e07060', marginBottom: 4 }}>
              {t('platform.welcome.beta_warning_title')}
            </div>
            <div style={{ fontSize: 13, lineHeight: 1.65, color: 'var(--text-quiet)' }}>
              {t('platform.welcome.beta_warning_body')}
            </div>
          </div>
          {/* 反馈流程 */}
          <Section
            title={t('platform.welcome.feedback_section_title')}
            body={t('platform.welcome.feedback_section_body')}
          />
          {/* API 说明 */}
          <Section
            title={t('platform.welcome.api_section_title')}
            body={t('platform.welcome.api_section_body')}
          />
          {/* Beta Co-builders 选择（仅首次弹 + 普通用户 + 通过白名单注册）*/}
          {showCoBuilder && (
            <div style={{ borderTop: '1px solid var(--line, #3a322b)', paddingTop: 14, marginTop: 4 }}>
              <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={coBuilderChecked}
                  onChange={(e) => setCoBuilderChecked(e.target.checked)}
                  style={{ marginTop: 2, flexShrink: 0, accentColor: 'var(--accent, #c49b4e)' }}
                />
                <span style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.55 }}>
                  {t('platform.welcome.co_builder_label')}
                </span>
              </label>
              <div style={{ fontSize: 11.5, color: 'var(--text-quiet)', marginTop: 5, paddingLeft: 24 }}>
                {t('platform.welcome.co_builder_hint')}
              </div>
            </div>
          )}
        </div>
    </Modal>
  );
}

export { WelcomeModal };
