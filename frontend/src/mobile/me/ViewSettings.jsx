/* MobileMe · VIEW 账户设置 Settings —— 从 pages/MobileMe.jsx 拆出。 */
import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import { fmtAgo } from './helpers.js';
import { PageHead, Toggle, SetRow, ActionBtn, Input, Select, ConfirmSheet } from './shared.jsx';

/* ═══════════════════════════════════════════════════════════════════
   VIEW: 账户设置 Settings
   ═══════════════════════════════════════════════════════════════════ */
function ViewSettings({ nav, user }) {
  const { t } = useTranslation();
  const hasPassword = user.has_password !== false;

  /* 偏好开关:只保留后端真的会读的 public_profile(公开成就墙)。「允许被搜索」「二次验证」
     「邮件通知」「匿名使用数据」「崩溃报告」和「资料字段可见性」全站没有后端读方,是装饰,已下线
     (理由见 web components/platform/MeUserSettings.jsx,iOS 同批)。 */
  const [prefLoaded, setPrefLoaded] = useState(false);
  const [publicProfile, setPublicProfile] = useState(null);

  /* 会话/历史 */
  const [sessions, setSessions] = useState([]);
  const [loginHistory, setLoginHistory] = useState([]);

  /* 子视图 */
  const [subView, setSubView] = useState(null); // 'sessions'|'history'|'pw'|'personas'|'export'|'policy'|'delete-confirm'|'deact-confirm'

  /* 表单状态 */
  const [pwForm, setPwForm] = useState({ current: '', next: '', confirm: '' });
  const [savingPw, setSavingPw] = useState(false);
  const [exportForm, setExportForm] = useState({ scope: 'all', format: 'zip', email: '' });
  const [exportBusy, setExportBusy] = useState(false);
  const [deleteConfirmText, setDeleteConfirmText] = useState('');
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deactBusy, setDeactBusy] = useState(false);
  const [revokeAllBusy, setRevokeAllBusy] = useState(false);

  /* 人格 */
  const [personas, setPersonas] = useState(null);
  const [personaEdit, setPersonaEdit] = useState(null); // null | persona obj
  const [personaSaving, setPersonaSaving] = useState(false);

  /* 加载偏好 */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      // 读偏好走 GET(与 web 同一个 getPreferences)。以前这里调的是写接口 preferences() 不带参数
      // (等于 POST 一个空补丁再拿返回值当读取)。
      try {
        const r = await window.api.account.getPreferences();
        if (cancelled) return;
        const p = r?.preferences || {};
        setPublicProfile(p.public_profile != null ? !!p.public_profile : false);
      } catch (_) {
        if (!cancelled) setPublicProfile(false);
      } finally { if (!cancelled) setPrefLoaded(true); }
    })();
    return () => { cancelled = true; };
  }, []);

  /* 加载会话/登录历史 */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.auth.sessionsList();
        const list = r?.sessions || r?.items || [];
        if (!cancelled) setSessions(list.map(s => ({
          id: s.id || s.session_id,
          device: s.device || s.user_agent || '—',
          loc: s.location || s.loc || '—',
          ip: s.ip || s.remote_ip || '—',
          ts: fmtAgo(s.last_seen_at || s.created_at),
          current: !!s.current,
        })));
      } catch (_) {}
      try {
        const r = await window.api.auth.loginHistory();
        const list = r?.entries || r?.items || [];
        if (!cancelled) setLoginHistory(list.map(s => ({
          ts: fmtAgo(s.at),
          at: s.at,
          dev: s.user_agent || s.device || '—',
          ip: s.ip || '—',
          result: s.result || (s.ok ? 'ok' : 'blocked'),
        })));
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  /* 加载人格 */
  useEffect(() => {
    if (subView !== 'personas') return;
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.personas.list();
        if (!cancelled) setPersonas(r?.personas || r?.items || []);
      } catch (_) { if (!cancelled) setPersonas([]); }
    })();
    return () => { cancelled = true; };
  }, [subView]);

  /* 偏好持久化 */
  const savePref = useCallback(async (key, val) => {
    try { await window.api.account.preferences({ [key]: val }); } catch (_) {}
  }, []);

  // 只在用户真的拨动开关时写(与 web 同口径)。以前用「值变即写」的 effect:进页加载完就把读到的值
  // 原样回写一遍,读取失败时更是把默认值 false 写回去,把用户已开的公开主页悄悄关掉。
  const onTogglePublicProfile = (v) => { setPublicProfile(v); savePref('public_profile', v); };

  const nSess = sessions.length;
  const curSess = sessions.find(s => s.current) || sessions[0];
  const sessDesc = nSess === 0
    ? t('mobile.me.settings.no_sessions')
    : curSess
      ? t('mobile.me.settings.sessions_desc_with_ts', { count: nSess, ts: curSess.ts })
      : t('mobile.me.settings.sessions_desc', { count: nSess });

  const cutoff = Date.now() - 30 * 86_400_000;
  const okIn30d = loginHistory.filter(h => h.result === 'ok' && (() => { try { return new Date(h.at).getTime() >= cutoff; } catch { return false; } })()).length;
  const blocked = loginHistory.filter(h => h.result !== 'ok').length;
  const histDesc = loginHistory.length === 0
    ? t('mobile.me.settings.no_login_history')
    : blocked
      ? t('mobile.me.settings.login_history_desc_blocked', { ok: okIn30d, blocked })
      : t('mobile.me.settings.login_history_desc', { ok: okIn30d });

  const onRevokeSession = async (sid) => {
    try {
      await window.api.auth.sessionsRevoke(sid);
      setSessions(s => s.filter(x => x.id !== sid));
      nav.toast(t('mobile.me.settings.session_revoked'), 'ok', 'check');
    } catch (e) { nav.toast(t('mobile.me.settings.session_revoke_failed'), 'danger', 'warn'); }
  };

  const onRevokeAll = async () => {
    setRevokeAllBusy(true);
    try {
      await window.api.auth.revokeAllSessions();
      setSessions(s => s.filter(x => x.current));
      nav.toast(t('mobile.me.settings.all_revoked'), 'ok', 'check');
    } catch (e) { nav.toast(t('mobile.me.op_failed'), 'danger', 'warn'); }
    finally { setRevokeAllBusy(false); }
  };

  const onChangePassword = async () => {
    if (hasPassword && !pwForm.current) { nav.toast(t('mobile.me.settings.pw_enter_current'), 'danger', 'warn'); return; }
    if (!pwForm.next) { nav.toast(t('mobile.me.settings.pw_enter_new'), 'danger', 'warn'); return; }
    if (pwForm.next !== pwForm.confirm) { nav.toast(t('mobile.me.settings.pw_mismatch'), 'danger', 'warn'); return; }
    setSavingPw(true);
    try {
      await window.api.auth.changePassword({ current: pwForm.current, next: pwForm.next });
      nav.toast(t('mobile.me.settings.pw_changed'), 'ok', 'check');
      setSubView(null); setPwForm({ current: '', next: '', confirm: '' });
    } catch (e) { nav.toast(t('mobile.me.settings.pw_change_failed', { msg: e?.message || '' }), 'danger', 'warn'); }
    finally { setSavingPw(false); }
  };

  const onExportData = async () => {
    setExportBusy(true);
    try {
      const r = await window.api.account.exportData(exportForm);
      nav.toast(t('mobile.me.settings.export_requested'), 'ok', 'check');
      setSubView(null);
    } catch (e) { nav.toast(t('mobile.me.settings.export_failed'), 'danger', 'warn'); }
    finally { setExportBusy(false); }
  };

  const onDeleteAccount = async () => {
    setDeleteBusy(true);
    try {
      await window.api.account.requestDelete();
      nav.toast(t('mobile.me.settings.delete_requested'), 'ok', 'check');
      setSubView(null);
    } catch (e) { nav.toast(t('mobile.me.op_failed_msg', { msg: e?.message || '' }), 'danger', 'warn'); }
    finally { setDeleteBusy(false); }
  };

  const onDeactivate = async () => {
    setDeactBusy(true);
    try {
      await window.api.account.deactivate?.();
      nav.toast(t('mobile.me.settings.deactivated'), 'ok', 'check');
      setSubView(null);
    } catch (e) { nav.toast(t('mobile.me.op_failed_msg', { msg: e?.message || '' }), 'danger', 'warn'); }
    finally { setDeactBusy(false); }
  };

  const onPersonaSave = async () => {
    if (!personaEdit) return;
    setPersonaSaving(true);
    try {
      await window.api.account.personas.upsert(personaEdit);
      const r = await window.api.account.personas.list();
      setPersonas(r?.personas || r?.items || []);
      setPersonaEdit(null);
      nav.toast(t('mobile.me.settings.persona_saved'), 'ok', 'check');
    } catch (e) { nav.toast(t('mobile.me.edit.save_error', { msg: '' }), 'danger', 'warn'); }
    finally { setPersonaSaving(false); }
  };

  const onPersonaDelete = async (id) => {
    try {
      await window.api.account.personas.remove(id);
      setPersonas(ps => ps.filter(p => p.id !== id));
      nav.toast(t('mobile.me.settings.persona_deleted'), 'ok', 'check');
    } catch (e) { nav.toast(t('mobile.me.settings.delete_failed'), 'danger', 'warn'); }
  };

  /* ── 子视图渲染 ─── */
  if (subView === 'sessions') return (
    <>
      <PageHead title={t('mobile.me.settings.sessions_title')} sub={t('mobile.me.settings.sessions_count', { n: nSess })} onBack={() => setSubView(null)} />
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {sessions.length === 0 ? (
            <div className="pl-empty">{t('mobile.me.settings.no_sessions')}</div>
          ) : sessions.map((s, i) => (
            <div key={s.id || i} className="pl-row" style={{ margin: '0 0 6px' }}>
              <span className={'pl-row-ic' + (s.current ? ' accent' : '')}><Icon name="world" size={17} /></span>
              <span className="pl-row-tx">
                <strong style={{ fontSize: 13 }}>{s.device}{s.current && <span style={{ marginLeft: 6, fontSize: 10.5, padding: '1px 6px', borderRadius: 999, background: 'var(--ok-soft)', color: 'var(--ok)', border: '1px solid rgba(126,184,142,0.3)' }}>{t('mobile.me.settings.current_session')}</span>}</strong>
                <span className="mono">{s.loc} · {s.ip} · {s.ts}</span>
              </span>
              {!s.current && (
                <button onClick={() => onRevokeSession(s.id)} style={{ flexShrink: 0, height: 30, padding: '0 10px', borderRadius: 8, fontSize: 12, background: 'var(--danger-soft)', color: 'var(--danger)', border: '1px solid rgba(200,103,93,0.3)' }}>
                  {t('mobile.me.settings.revoke')}
                </button>
              )}
            </div>
          ))}
          {nSess > 1 && (
            <button onClick={onRevokeAll} disabled={revokeAllBusy} className="pl-btn-ghost" style={{ marginTop: 12, width: '100%' }}>
              <Icon name="logout" size={15} />{revokeAllBusy ? t('mobile.me.processing') : t('mobile.me.settings.revoke_all')}
            </button>
          )}
        </div>
      </div>
    </>
  );

  if (subView === 'history') return (
    <>
      <PageHead title={t('mobile.me.settings.history_title')} sub={t('mobile.me.settings.history_count', { n: loginHistory.length })} onBack={() => setSubView(null)} />
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {loginHistory.length === 0 ? <div className="pl-empty">{t('mobile.me.settings.no_login_history')}</div> :
            loginHistory.map((r, i) => (
              <div key={i} className="pl-row" style={{ margin: '0 0 5px' }}>
                <span className={'pl-row-ic ' + (r.result === 'ok' ? 'ok' : 'warn')}><Icon name={r.result === 'ok' ? 'check' : 'shield'} size={16} /></span>
                <span className="pl-row-tx">
                  <strong style={{ fontSize: 12.5 }}>{r.dev}</strong>
                  <span className="mono">{r.ip} · {r.ts}</span>
                </span>
                <span style={{ flexShrink: 0, fontSize: 11, padding: '2px 8px', borderRadius: 999, background: r.result === 'ok' ? 'var(--ok-soft)' : 'var(--danger-soft)', color: r.result === 'ok' ? 'var(--ok)' : 'var(--danger)', border: '1px solid ' + (r.result === 'ok' ? 'rgba(126,184,142,0.3)' : 'rgba(200,103,93,0.3)') }}>
                  {r.result === 'ok' ? t('mobile.me.settings.login_ok') : t('mobile.me.settings.login_blocked')}
                </span>
              </div>
            ))
          }
        </div>
      </div>
    </>
  );

  if (subView === 'pw') return (
    <>
      <PageHead title={hasPassword ? t('mobile.me.settings.change_password') : t('mobile.me.settings.set_password')} onBack={() => setSubView(null)} />
      <div className="pl-body tabbed">
        <div className="pl-pad">
          <div className="pl-sec" style={{ paddingTop: 8 }}>
            {hasPassword && (
              <Input label={t('mobile.me.settings.pw_current')} type="password" value={pwForm.current} onChange={v => setPwForm(f => ({ ...f, current: v }))} />
            )}
            <Input label={t('mobile.me.settings.pw_new')} hint={t('mobile.me.settings.pw_new_hint')} type="password" value={pwForm.next} onChange={v => setPwForm(f => ({ ...f, next: v }))} />
            <Input label={t('mobile.me.settings.pw_confirm')} type="password" value={pwForm.confirm} onChange={v => setPwForm(f => ({ ...f, confirm: v }))} />
          </div>
          <div style={{ display: 'flex', gap: 10, paddingTop: 8 }}>
            <button onClick={() => setSubView(null)} style={{ flex: 1, height: 46, borderRadius: 12, fontSize: 14, background: 'var(--panel-2)', border: '1px solid var(--line)', color: 'var(--text-quiet)' }}>{t('common.cancel')}</button>
            <button onClick={onChangePassword} disabled={savingPw} style={{ flex: 2, height: 46, borderRadius: 12, fontSize: 14, fontWeight: 600, background: 'var(--accent)', border: 'none', color: '#fff8f3', opacity: savingPw ? 0.7 : 1 }}>
              {savingPw ? t('mobile.me.settings.pw_changing') : (hasPassword ? t('mobile.me.settings.change_password') : t('mobile.me.settings.set_password'))}
            </button>
          </div>
        </div>
      </div>
    </>
  );

  if (subView === 'personas') return (
    <>
      <PageHead
        title={t('mobile.me.settings.personas_title')}
        onBack={() => { setSubView(null); setPersonaEdit(null); }}
        actions={
          <button className="pl-headbtn" onClick={() => setPersonaEdit({ id: '', name: '', description: '', prompt: '' })} aria-label={t('mobile.me.settings.persona_new')}>
            <Icon name="plus" size={18} />
          </button>
        }
      />
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {personaEdit && (
            <div style={{ background: 'var(--panel)', border: '1px solid var(--accent-edge)', borderRadius: 14, padding: '14px 14px 10px', marginBottom: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent)', marginBottom: 12 }}>{personaEdit.id ? t('mobile.me.settings.persona_edit') : t('mobile.me.settings.persona_new')}</div>
              <Input label={t('mobile.me.settings.persona_name')} value={personaEdit.name || ''} onChange={v => setPersonaEdit(p => ({ ...p, name: v }))} />
              <Input label={t('mobile.me.settings.persona_desc')} value={personaEdit.description || ''} onChange={v => setPersonaEdit(p => ({ ...p, description: v }))} />
              <Input label={t('mobile.me.settings.persona_prompt')} multiline value={personaEdit.prompt || ''} onChange={v => setPersonaEdit(p => ({ ...p, prompt: v }))} rows={4} />
              <div style={{ display: 'flex', gap: 9 }}>
                <button onClick={() => setPersonaEdit(null)} style={{ flex: 1, height: 40, borderRadius: 10, fontSize: 13, background: 'var(--panel-2)', border: '1px solid var(--line)', color: 'var(--text-quiet)' }}>{t('common.cancel')}</button>
                <button onClick={onPersonaSave} disabled={personaSaving} style={{ flex: 2, height: 40, borderRadius: 10, fontSize: 13, fontWeight: 600, background: 'var(--accent)', border: 'none', color: '#fff8f3', opacity: personaSaving ? 0.7 : 1 }}>
                  {personaSaving ? t('mobile.me.edit.saving') : t('common.save')}
                </button>
              </div>
            </div>
          )}
          {personas === null ? (
            <div className="pl-empty">{t('common.loading')}</div>
          ) : personas.length === 0 ? (
            <div className="pl-empty">{t('mobile.me.settings.no_personas')}</div>
          ) : personas.map(p => (
            <div key={p.id} className="pl-row" style={{ margin: '0 0 6px', alignItems: 'flex-start' }}>
              <span className="pl-row-ic"><Icon name="user" size={17} /></span>
              <span className="pl-row-tx">
                <strong>{p.name || t('mobile.me.settings.persona_unnamed')}</strong>
                {p.description && <span style={{ fontSize: 12 }}>{p.description}</span>}
                {p.prompt && <span className="mono" style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 2, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{p.prompt}</span>}
              </span>
              <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                <button onClick={() => setPersonaEdit({ ...p })} style={{ width: 32, height: 32, borderRadius: 8, background: 'var(--panel-2)', border: '1px solid var(--line-soft)', color: 'var(--muted)', display: 'grid', placeItems: 'center' }}>
                  <Icon name="edit" size={14} />
                </button>
                <button onClick={() => onPersonaDelete(p.id)} style={{ width: 32, height: 32, borderRadius: 8, background: 'var(--danger-soft)', border: '1px solid rgba(200,103,93,0.3)', color: 'var(--danger)', display: 'grid', placeItems: 'center' }}>
                  <Icon name="trash" size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );

  if (subView === 'export') return (
    <>
      <PageHead title={t('mobile.me.settings.export_title')} onBack={() => setSubView(null)} />
      <div className="pl-body tabbed">
        <div className="pl-pad">
          <div className="pl-sec" style={{ paddingTop: 8 }}>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 14, lineHeight: 1.7 }}>
              {t('mobile.me.settings.export_desc')}
            </div>
            <Select label={t('mobile.me.settings.export_scope')} value={exportForm.scope} onChange={v => setExportForm(f => ({ ...f, scope: v }))}
              options={[{ value: 'all', label: t('mobile.me.settings.export_scope_all') }, { value: 'scripts', label: t('mobile.me.settings.export_scope_scripts') }, { value: 'saves', label: t('mobile.me.settings.export_scope_saves') }, { value: 'library', label: t('mobile.me.settings.export_scope_library') }, { value: 'usage', label: t('mobile.me.settings.export_scope_usage') }]} />
            <Select label={t('mobile.me.settings.export_format')} value={exportForm.format} onChange={v => setExportForm(f => ({ ...f, format: v }))}
              options={[{ value: 'zip', label: t('mobile.me.settings.export_format_zip') }, { value: 'json', label: t('mobile.me.settings.export_format_json') }]} />
            <Input label={t('mobile.me.settings.export_email')} type="email" value={exportForm.email} onChange={v => setExportForm(f => ({ ...f, email: v }))} placeholder={t('mobile.me.settings.export_email_placeholder')} />
          </div>
          <div style={{ display: 'flex', gap: 10, paddingTop: 8 }}>
            <button onClick={() => setSubView(null)} style={{ flex: 1, height: 46, borderRadius: 12, fontSize: 14, background: 'var(--panel-2)', border: '1px solid var(--line)', color: 'var(--text-quiet)' }}>{t('common.cancel')}</button>
            <button onClick={onExportData} disabled={exportBusy} style={{ flex: 2, height: 46, borderRadius: 12, fontSize: 14, fontWeight: 600, background: 'var(--accent)', border: 'none', color: '#fff8f3', opacity: exportBusy ? 0.7 : 1 }}>
              {exportBusy ? t('mobile.me.settings.export_requesting') : t('mobile.me.settings.export_request_btn')}
            </button>
          </div>
        </div>
      </div>
    </>
  );

  if (subView === 'policy') return (
    <>
      <PageHead title={t('mobile.me.settings.policy_title')} onBack={() => setSubView(null)} />
      <div className="pl-body tabbed">
        <div className="pl-pad">
          <div style={{ fontSize: 13.5, lineHeight: 1.8, color: 'var(--text-quiet)' }}>
            <p><strong style={{ color: 'var(--text)' }}>{t('mobile.me.settings.policy_1_title')}</strong><br />{t('mobile.me.settings.policy_1_body')}</p>
            <p><strong style={{ color: 'var(--text)' }}>{t('mobile.me.settings.policy_2_title')}</strong><br />{t('mobile.me.settings.policy_2_body')}</p>
            <p><strong style={{ color: 'var(--text)' }}>{t('mobile.me.settings.policy_3_title')}</strong><br />{t('mobile.me.settings.policy_3_body')}</p>
            <p><strong style={{ color: 'var(--text)' }}>{t('mobile.me.settings.policy_4_title')}</strong><br />{t('mobile.me.settings.policy_4_body')}</p>
            <p><strong style={{ color: 'var(--text)' }}>{t('mobile.me.settings.policy_5_title')}</strong><br />{t('mobile.me.settings.policy_5_body')}</p>
          </div>
          <button onClick={() => setSubView(null)} className="pl-btn-primary" style={{ width: '100%', marginTop: 20 }}>{t('mobile.me.settings.policy_read')}</button>
        </div>
      </div>
    </>
  );

  /* ── 主设置页 ─── */
  const DELETE_CONFIRM_PHRASE = t('mobile.me.settings.delete_confirm_phrase');
  return (
    <>
      <PageHead title={t('mobile.me.settings.title')} onBack={() => nav.go('me')} />
      <div className="pl-body tabbed">
        <div className="pl-pad">

          {/* 隐私 · 公开范围 */}
          <div className="pl-sec">
            <div className="pl-sec-head"><h2>{t('mobile.me.settings.privacy_section')}</h2></div>
            <SetRow label={t('mobile.me.settings.public_profile')} desc={t('mobile.me.settings.public_profile_desc')}>
              <Toggle on={!!publicProfile} onChange={onTogglePublicProfile} disabled={!prefLoaded} />
            </SetRow>
          </div>

          {/* 账号 · 安全 */}
          <div className="pl-sec">
            <div className="pl-sec-head"><h2>{t('mobile.me.settings.security_section')}</h2></div>
            <SetRow label={hasPassword ? t('mobile.me.settings.change_password') : t('mobile.me.settings.set_password')} desc={hasPassword ? t('mobile.me.settings.pw_desc_change') : t('mobile.me.settings.pw_desc_set')}>
              <ActionBtn label={hasPassword ? t('mobile.me.settings.change_password') : t('mobile.me.settings.set_password')} icon="lock" onClick={() => setSubView('pw')} />
            </SetRow>
            <SetRow label={t('mobile.me.settings.active_sessions')} desc={sessDesc}>
              <ActionBtn label={t('mobile.me.settings.view_sessions')} icon="eye" onClick={() => setSubView('sessions')} />
            </SetRow>
            <SetRow label={t('mobile.me.settings.login_history_label')} desc={histDesc}>
              <ActionBtn label={t('mobile.me.settings.view_history')} icon="history" onClick={() => setSubView('history')} />
            </SetRow>
          </div>

          {/* 人格 Persona */}
          <div className="pl-sec">
            <div className="pl-sec-head"><h2>{t('mobile.me.settings.personas_section')}</h2></div>
            <SetRow label={t('mobile.me.settings.my_persona')} desc={t('mobile.me.settings.my_persona_desc')}>
              <ActionBtn label={t('mobile.me.settings.manage_persona')} icon="user" onClick={() => setSubView('personas')} />
            </SetRow>
          </div>

          {/* 数据共享 */}
          <div className="pl-sec">
            <div className="pl-sec-head"><h2>{t('mobile.me.settings.data_sharing_section')}</h2></div>
            <SetRow label="GDPR / Privacy Policy">
              <ActionBtn label={t('mobile.me.settings.view_policy')} icon="file" onClick={() => setSubView('policy')} />
            </SetRow>
          </div>

          {/* 数据所有权 */}
          <div className="pl-sec">
            <div className="pl-sec-head"><h2>{t('mobile.me.settings.data_ownership_section')}</h2></div>
            <SetRow label={t('mobile.me.settings.export_my_data')} desc={t('mobile.me.settings.export_my_data_desc')}>
              <ActionBtn label={t('mobile.me.settings.export_request_btn')} icon="download" onClick={() => setSubView('export')} />
            </SetRow>
            <SetRow label={t('mobile.me.settings.deactivate')} desc={t('mobile.me.settings.deactivate_desc')}>
              <ActionBtn label={t('mobile.me.settings.deactivate')} onClick={() => setSubView('deact-confirm')} />
            </SetRow>
            <SetRow label={t('mobile.me.settings.delete_account')} desc={t('mobile.me.settings.delete_account_desc')} danger>
              <ActionBtn label={t('mobile.me.settings.delete_account_btn')} icon="trash" danger onClick={() => setSubView('delete-confirm')} />
            </SetRow>
          </div>

        </div>
      </div>

      {/* 停用确认 Sheet */}
      <ConfirmSheet
        open={subView === 'deact-confirm'}
        title={t('mobile.me.settings.deact_confirm_title')}
        body={t('mobile.me.settings.deact_confirm_body')}
        confirmLabel={t('mobile.me.settings.deactivate')}
        onClose={() => setSubView(null)}
        onConfirm={onDeactivate}
        loading={deactBusy}
      />

      {/* 删除确认 Sheet */}
      {subView === 'delete-confirm' && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(10,9,8,0.6)', display: 'flex', alignItems: 'flex-end' }}>
          <div style={{ width: '100%', background: 'var(--panel)', borderRadius: '20px 20px 0 0', padding: '20px 18px calc(var(--safe-bottom,20px) + 16px)', borderTop: '1px solid var(--line)' }}>
            <div style={{ width: 36, height: 4, borderRadius: 2, background: 'var(--line-strong)', margin: '0 auto 16px' }} />
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6, color: 'var(--danger)' }}>{t('mobile.me.settings.delete_confirm_title')}</div>
            <div style={{ fontSize: 13, color: 'var(--text-quiet)', marginBottom: 16, lineHeight: 1.7 }}>
              {t('mobile.me.settings.delete_confirm_body')}<br />
              {t('mobile.me.settings.delete_confirm_body2')}
            </div>
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>{t('mobile.me.settings.delete_confirm_prompt', { phrase: DELETE_CONFIRM_PHRASE })}</div>
              <input
                value={deleteConfirmText} onChange={e => setDeleteConfirmText(e.target.value)}
                placeholder={DELETE_CONFIRM_PHRASE}
                style={{ width: '100%', background: 'var(--panel-2)', border: '1px solid var(--danger)', borderRadius: 10, color: 'var(--text)', fontSize: 16, padding: '10px 12px', outline: 'none', boxSizing: 'border-box' }}
              />
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button onClick={() => { setSubView(null); setDeleteConfirmText(''); }} style={{ flex: 1, height: 46, borderRadius: 12, fontSize: 14, background: 'var(--panel-2)', border: '1px solid var(--line)', color: 'var(--text-quiet)' }}>{t('common.cancel')}</button>
              <button
                onClick={onDeleteAccount} disabled={deleteConfirmText !== DELETE_CONFIRM_PHRASE || deleteBusy}
                style={{ flex: 1, height: 46, borderRadius: 12, fontSize: 14, fontWeight: 600, background: 'var(--danger)', border: 'none', color: '#fff', opacity: (deleteConfirmText !== DELETE_CONFIRM_PHRASE || deleteBusy) ? 0.45 : 1 }}
              >
                {deleteBusy ? t('mobile.me.processing') : t('mobile.me.settings.delete_permanent_btn')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export { ViewSettings };
