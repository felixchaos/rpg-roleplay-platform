/* 个人中心 · 用户设置(隐私 / 数据共享 / 账号安全 / 通知 / 数据所有权)。
   从 components/platform/me-pages.jsx 二次拆出,零行为变化。 */
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import Modal from '../Modal.jsx';
import { PromptModal, ConfirmModal, useAutoSave, useReactiveUser, SettingRow, SettingsToggle } from './shared.jsx';
import CSButton from '@cloudscape-design/components/button';
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSSpaceBetween from '@cloudscape-design/components/space-between';

function MeUserSettings() {
  const { t } = useTranslation();
  const user = useReactiveUser();
  const hasPassword = user.has_password !== false;
  // [round-3-P2] 原 useAutoSave(scope="me") + tog 只调 save(label):label 被当成 field、无 val
  //  → 走 useAutoSave 的「仅 toast 不落库」兼容分支,这些隐私开关全部只弹"已保存"却从不持久化。
  //  且 scope="me" 会把键写成 me.two_fa,与下面 loader 读取的扁平 p.two_fa 不符 → 双重失效。
  //  修:scope=null 写扁平键 + tog 传 (field, value) 真正落库。
  const save = useAutoSave(t('platform.me.settings.label', '用户设置'), null);
  const tog = (setter, field) => (v) => { setter(v); save(field, v); };
  // 初始值为 null，等后端拉取完成后再用真实值初始化，防止 mount 时以硬编码默认值覆盖已存设置
  // 只保留后端真的会读的开关:public_profile(公开成就墙,api/me/achievements.py 读)。
  // 以前还有「允许搜索」「匿名用量统计」「崩溃 / 错误报告」「个性化推荐」「二次验证(2FA)」
  // 「邮件通知」六个开关和「资料字段可见性」逐项配置,全站没有任何后端代码读这些键:没有用户
  // 搜索、没有用量上报、错误信息只在用户提交反馈时随包附带、没有推荐、没有 2FA 实现(开着还显示
  // 「Authenticator」,等于谎报账号有二次验证)、没有按偏好发的邮件、资料字段可见性写进
  // profile_extras.visibility 后无人读取。装饰性开关已下线(手机端 / iOS 同批)。
  const [publicProfile, setPublicProfile] = useStatePL(null);
  const [prefLoaded, setPrefLoaded] = useStatePL(false);

  // mount 时先从后端拉真实偏好值，再初始化各开关
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.getPreferences();
        if (cancelled) return;
        const p = r?.preferences || r || {};
        setPublicProfile(p.public_profile != null ? !!p.public_profile : false);
      } catch (_) {
        // 拉取失败：使用安全默认值
        if (!cancelled) setPublicProfile(false);
      } finally {
        if (!cancelled) setPrefLoaded(true);
      }
    })();
    return () => { cancelled = true; };
  }, []);
  const [confirmDelete, setConfirmDelete] = useStatePL(false);
  const [confirmDeact, setConfirmDeact] = useStatePL(false);
  const [busyDelete, setBusyDelete] = useStatePL(false);
  const [busyDeact, setBusyDeact] = useStatePL(false);
  const [busyRevokeAll, setBusyRevokeAll] = useStatePL(false);
  const [pwOpen, setPwOpen] = useStatePL(false);
  const [sessionsOpen, setSessionsOpen] = useStatePL(false);
  const [historyOpen, setHistoryOpen] = useStatePL(false);
  const [exportOpen, setExportOpen] = useStatePL(false);
  const [policyOpen, setPolicyOpen] = useStatePL(false);

  // task 49：sessions 初始值原是硬编码假行 [{device:"macOS·Chrome 134", ip:"127.0.0.1"}]，
  // 即使后端返回空也永远显示这条假记录。改为空数组 + mount 即拉真后端。
  const [sessions, setSessions] = useStatePL([]);
  const [loginHistory, setLoginHistory] = useStatePL([]);
  const [savesCount, setSavesCount] = useStatePL(null);

  // mount 即拉 sessions/login-history/saves count，供描述行使用真实数字
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.auth.sessionsList();
        const list = r?.sessions || r?.items || [];
        if (cancelled) return;
        setSessions(list.map(s => ({
          id: s.id || s.session_id,
          device: s.device || s.user_agent || "—",
          loc: s.location || s.loc || "—",
          ip: s.ip || s.remote_ip || "—",
          ts: window.__fmt?.ago(s.last_seen_at || s.created_at) || "—",
          last_seen_at: s.last_seen_at || s.created_at,
          current: !!s.current,
        })));
      } catch (_) {}
      try {
        const r = await window.api.auth.loginHistory();
        const list = r?.entries || r?.items || [];
        if (cancelled) return;
        setLoginHistory(list.map(s => ({
          ts: window.__fmt?.ago(s.at) || s.at,
          at: s.at,
          dev: s.user_agent || s.device || "—",
          ip: s.ip || "—",
          result: s.result || (s.ok ? "ok" : "blocked"),
        })));
      } catch (_) {}
      try {
        const r = await window.api.saves.list();
        const list = r?.items || r?.saves || [];
        if (!cancelled) setSavesCount(Array.isArray(list) ? list.length : 0);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const onChangePassword = async (vals) => {
    if (!vals?.next || vals.next !== vals.confirm) {
      window.__apiToast?.(t('platform.me.settings.pw_mismatch', '两次密码不一致'), { kind: "danger" });
      return;
    }
    try {
      await window.api.auth.changePassword({ current: vals.current, next: vals.next });
      window.__apiToast?.(t('platform.me.settings.pw_changed', '密码已修改'), { kind: "ok" });
      setPwOpen(false);
    } catch (e) {
      window.__apiToast?.(t('platform.me.settings.pw_change_failed', '修改失败'), { kind: "danger", detail: e?.message });
    }
  };

  const onRevokeSession = async (sid) => {
    try {
      await window.api.auth.sessionsRevoke(sid);
      window.__apiToast?.(t('platform.me.settings.session_revoked', '已下线'), { kind: "ok" });
      setSessions(s => s.filter(x => x.id !== sid));
    } catch (e) {
      window.__apiToast?.(t('platform.me.settings.session_revoke_failed', '下线失败'), { kind: "danger", detail: e?.message });
    }
  };

  const onRevokeAll = async () => {
    setBusyRevokeAll(true);
    try {
      await window.api.auth.revokeAllSessions();
      window.__apiToast?.(t('platform.me.settings.all_revoked', '已全部下线'), { kind: "ok" });
      setSessions(s => s.filter(x => x.current));
    } catch (e) {
      window.__apiToast?.(t('platform.me.settings.session_revoke_failed', '下线失败'), { kind: "danger", detail: e?.message });
    } finally {
      setBusyRevokeAll(false);
    }
  };

  const onExportData = async (vals) => {
    try {
      const r = await window.api.account.exportData(vals);
      window.__apiToast?.(t('platform.me.settings.export_requested', '已申请导出'), { kind: "ok", detail: r?.message || t('platform.me.settings.export_email_notice', '完成后会邮件通知') });
      setExportOpen(false);
    } catch (e) {
      window.__apiToast?.(t('platform.me.settings.export_failed', '申请失败'), { kind: "danger", detail: e?.message });
    }
  };

  const onDeactivate = async () => {
    setBusyDeact(true);
    try {
      await window.api.account.deactivate();
      window.__apiToast?.(t('platform.me.settings.deactivated', '账号已停用'), { kind: "ok" });
      setConfirmDeact(false);
      setTimeout(() => location.replace("Login.html"), 800);
    } catch (e) {
      window.__apiToast?.(t('platform.me.settings.deactivate_failed', '停用失败'), { kind: "danger", detail: e?.message });
      setBusyDeact(false);
    }
  };

  const onDeleteAccount = async () => {
    setBusyDelete(true);
    try {
      await window.api.account.deleteAccount({});
      window.__apiToast?.(t('platform.me.settings.account_deleted', '账号已删除'), { kind: "ok" });
      setConfirmDelete(false);
      setTimeout(() => location.replace("Login.html"), 800);
    } catch (e) {
      window.__apiToast?.(t('platform.me.settings.delete_failed', '删除失败'), { kind: "danger", detail: e?.message });
      setBusyDelete(false);
    }
  };

  // [round-4-P2] 移除原 7 个 useEffectPL「值变即 onSavePreference」持久化副作用:
  //   ① 与 round-3 起 tog 走 save(field,v) 真正落库形成【双写】(每次切换写两次后端);
  //   ② prefLoaded 翻 true 时 7 个 effect 全触发 → 每次进页都把 7 项偏好回写一遍(#39);
  //   ③ load() 拉取失败的 catch 设安全默认值后,effect 会把这些默认值写回后端、覆盖真实偏好(#7)。
  //   现单一持久化路径 = tog 内 save(field,v)(useAutoSave 防抖 + toast),仅用户实际切换才写。

  return (
    <CSSpaceBetween size="l" data-cap-anchor="me.settings">
      {/* 隐私 · 公开范围 */}
      <CSContainer header={<CSHeader variant="h2">{t('platform.me.settings.section_privacy', '隐私 · 公开范围')}</CSHeader>}>
        <CSSpaceBetween size="l">
          <SettingRow
            title={t('platform.me.settings.public_profile', '公开个人主页')}
            desc={t('platform.me.settings.public_profile_desc', '开启后，其他用户可以通过 @用户名 查看你的成就墙和最近活动。')}
            control={<SettingsToggle on={publicProfile} set={tog(setPublicProfile, "public_profile")} />}
          />
        </CSSpaceBetween>
      </CSContainer>

      {/* 数据共享 · 合规 */}
      <CSContainer header={<CSHeader variant="h2">{t('platform.me.settings.section_data', '数据共享 · 合规')}</CSHeader>}>
        <CSSpaceBetween size="l">
          <SettingRow
            title={t('platform.me.settings.gdpr', 'GDPR / 个人信息保护合规')}
            desc={t('platform.me.settings.gdpr_desc', '本平台不向第三方分享你的剧本内容、玩家变量或私聊。详见隐私政策。')}
            control={<CSButton iconName="file-open" onClick={(e) => { e.preventDefault(); setPolicyOpen(true); }}>{t('platform.me.settings.privacy_policy', '隐私政策')}</CSButton>}
          />
        </CSSpaceBetween>
      </CSContainer>

      {/* 账号 · 安全 */}
      <CSContainer header={<CSHeader variant="h2">{t('platform.me.settings.section_security', '账号 · 安全')}</CSHeader>}>
        <CSSpaceBetween size="l">
          <SettingRow
            title={hasPassword ? t('platform.me.settings.change_password', '修改密码') : t('platform.me.settings.set_password', '设置密码')}
            desc={hasPassword ? t('platform.me.settings.change_password_desc', '建议每 90 天更换一次，至少 12 位字符 + 大小写 + 数字。') : t('platform.me.settings.set_password_desc', '当前账号通过邮箱链接登录，尚未设置密码；可直接设置一组新密码。')}
            control={<CSButton iconName="lock-private" onClick={() => setPwOpen(true)}>{hasPassword ? t('platform.me.settings.change_password', '修改密码') : t('platform.me.settings.set_password', '设置密码')}</CSButton>}
          />
          {(() => {
            const nSess = sessions.length;
            const cur = sessions.find(s => s.current) || sessions[0];
            const sessDesc = nSess === 0
              ? t('platform.me.settings.sessions_none', '尚未拉取活跃会话。')
              : t('platform.me.settings.sessions_desc', { n: nSess, device: cur?.device, ts: cur?.ts, defaultValue: `当前 ${nSess} 个登录会话${cur ? ` · 最近：${cur.device}${cur.ts ? " · " + cur.ts : ""}` : ""}。` });
            const cutoff = Date.now() - 30 * 86400_000;
            const okIn30d = loginHistory.filter(h => {
              if (h.result !== "ok") return false;
              try { return new Date(h.at).getTime() >= cutoff; } catch { return false; }
            }).length;
            const blocked = loginHistory.filter(h => h.result !== "ok").length;
            const histDesc = loginHistory.length === 0
              ? t('platform.me.settings.history_none', '尚未拉取登录历史。')
              : t('platform.me.settings.history_desc', { ok: okIn30d, blocked, defaultValue: `最近 30 天 ${okIn30d} 次成功登录${blocked ? `，${blocked} 次被拦截` : "，无异常 IP"}。` });
            return <>
              <SettingRow
                title={t('platform.me.settings.active_sessions', '活跃会话')}
                desc={sessDesc}
                control={<CSButton iconName="visibility-on" onClick={() => setSessionsOpen(true)}>{t('platform.me.settings.view_sessions', '查看会话')}</CSButton>}
              />
              <SettingRow
                title={t('platform.me.settings.login_history', '登录历史')}
                desc={histDesc}
                control={<CSButton iconName="status-info" onClick={() => setHistoryOpen(true)}>{t('platform.me.settings.view_history', '查看日志')}</CSButton>}
              />
            </>;
          })()}
        </CSSpaceBetween>
      </CSContainer>

      {/* 数据所有权 */}
      <CSContainer header={<CSHeader variant="h2">{t('platform.me.settings.section_ownership', '数据所有权')}</CSHeader>}>
        <CSSpaceBetween size="l">
          <SettingRow
            title={t('platform.me.settings.export_data', '导出我的数据')}
            desc={t('platform.me.settings.export_data_desc', '打包导出全部剧本、存档、记忆、库资产、用量记录。生成后通过邮件发送下载链接。')}
            control={<CSButton iconName="download" onClick={() => setExportOpen(true)}>{t('platform.me.settings.export_btn', '申请导出')}</CSButton>}
          />
          <SettingRow
            title={t('platform.me.settings.deactivate', '停用账号')}
            desc={t('platform.me.settings.deactivate_desc', '停用后无法登录，剧本和存档保留 90 天，期间可随时恢复。')}
            control={<CSButton variant="normal" onClick={() => setConfirmDeact(true)}>{t('platform.me.settings.deactivate', '停用账号')}</CSButton>}
          />
          <SettingRow
            title={t('platform.me.settings.delete_account', '永久删除账号')}
            desc={t('platform.me.settings.delete_account_desc', '立刻删除全部账号信息、剧本、存档、库资产，无法恢复。')}
            control={<CSButton variant="normal" iconName="remove" onClick={() => setConfirmDelete(true)}>{t('platform.me.settings.delete_btn', '删除账号')}</CSButton>}
          />
        </CSSpaceBetween>
      </CSContainer>

      <ConfirmModal
        open={confirmDeact}
        title={t('platform.me.settings.deactivate_confirm_title', '停用账号？')}
        body={<>{t('platform.me.settings.deactivate_confirm_body', '账号停用 90 天内可登录恢复。期间剧本与存档保留但不可访问。')}</>}
        confirmLabel={t('platform.me.settings.deactivate_btn', '停用')}
        busy={busyDeact}
        onClose={() => setConfirmDeact(false)} onConfirm={onDeactivate}
      />
      <ConfirmModal
        open={confirmDelete}
        title={t('platform.me.settings.delete_confirm_title', '永久删除账号？')}
        body={<>{t('platform.me.settings.delete_confirm_body_pre', '这会')}<strong>{t('platform.me.settings.delete_confirm_now', '立刻')}</strong>{t('platform.me.settings.delete_confirm_body_mid', '删除你的账号、剧本、存档、库资产，')}<strong>{t('platform.me.settings.delete_confirm_irreversible', '无法恢复')}</strong>{t('platform.me.settings.delete_confirm_body_post', '。删除后无法用同一邮箱再注册（30 天冷冻期）。')}</>}
        danger confirmLabel={t('platform.me.settings.delete_confirm_btn', '确认删除')}
        busy={busyDelete}
        onClose={() => setConfirmDelete(false)} onConfirm={onDeleteAccount}
      />
      <PromptModal
        open={pwOpen}
        eyebrow={t('platform.me.settings.pw_eyebrow', '修改密码')}
        title={hasPassword ? t('platform.me.settings.pw_title_change', '设置新密码') : t('platform.me.settings.pw_title_set', '设置登录密码')}
        hint="POST /api/auth/password"
        fields={[
          ...(hasPassword ? [{ key: "current", label: t('platform.me.settings.pw_current', '当前密码'), required: true, type: "password" }] : []),
          { key: "next", label: t('platform.me.settings.pw_new', '新密码'), required: true, type: "password", hint: t('platform.me.settings.pw_hint', '至少 12 位 · 大小写 + 数字') },
          { key: "confirm", label: t('platform.me.settings.pw_confirm', '确认新密码'), required: true, type: "password" },
        ]}
        submitLabel={hasPassword ? t('platform.me.settings.change_password', '修改密码') : t('platform.me.settings.set_password', '设置密码')}
        onClose={() => setPwOpen(false)}
        onConfirm={onChangePassword}
      />
      <PromptModal
        open={exportOpen}
        eyebrow={t('platform.me.settings.export_eyebrow', '导出数据')}
        title={t('platform.me.settings.export_title', '选择要导出的内容')}
        hint="POST /api/account/export · 生成后通过邮件发送下载链接（链接 7 天有效）"
        fields={[
          { key: "scope", label: t('platform.me.settings.export_scope', '范围'), type: "select", default: "all",
            options: [
              { value: "all",      label: t('platform.me.settings.export_scope_all', '全部 · 剧本 · 存档 · 库 · 用量') },
              { value: "scripts",  label: t('platform.me.settings.export_scope_scripts', '仅剧本与章节') },
              { value: "saves",    label: t('platform.me.settings.export_scope_saves', '仅存档与分支') },
              { value: "library",  label: t('platform.me.settings.export_scope_library', '仅库资产') },
              { value: "usage",    label: t('platform.me.settings.export_scope_usage', '仅用量日志') },
            ] },
          { key: "format", label: t('platform.me.settings.export_format', '格式'), type: "select", default: "zip",
            options: [
              { value: "zip", label: t('platform.me.settings.export_format_zip', 'ZIP · 含 JSON + 附件') },
              { value: "json", label: t('platform.me.settings.export_format_json', 'JSON · 仅元数据') },
            ] },
          { key: "email", label: t('platform.me.settings.export_email', '接收邮箱'), required: true, default: "" },
        ]}
        submitLabel={t('platform.me.settings.export_btn', '申请导出')}
        onClose={() => setExportOpen(false)}
        onConfirm={onExportData}
      />
      {sessionsOpen && (
        <Modal
          open
          eyebrow={t('platform.me.settings.active_sessions', '活跃会话')}
          title={sessions.length === 0 ? t('platform.me.settings.sessions_empty', '暂无活跃会话') : t('platform.me.settings.sessions_title', { n: sessions.length, defaultValue: `${sessions.length} 个登录中` })}
          width={620}
          onClose={() => setSessionsOpen(false)}
          footer={<>
            <span className="muted-2" style={{fontSize: 11.5}}>POST /api/auth/sessions/revoke</span>
            <div style={{display: "flex", gap: 8}}>
              <button className="btn ghost" onClick={() => setSessionsOpen(false)}>{t('common.close', '关闭')}</button>
              <button className="btn danger" onClick={onRevokeAll} disabled={busyRevokeAll}><Icon name="close" size={12} /> {t('platform.me.settings.revoke_all', '全部下线（保留当前）')}</button>
            </div>
          </>}
        >
            <ul className="pl-session-list">
              {sessions.map((s, i) => (
                <li key={s.id || i}>
                  <div className="pl-session-dot"><Icon name={(s.device || "").includes("iOS") ? "user" : (s.device || "").includes("mac") ? "logo" : "world"} size={12} /></div>
                  <div className="pl-session-body">
                    <div>
                      <strong>{s.device}</strong>
                      {s.current && <span className="pill ok" style={{marginLeft: 6}}><span className="dot ok pulse" /> {t('platform.me.settings.session_current', '当前')}</span>}
                    </div>
                    <span className="muted-2 mono" style={{fontSize: 11}}>{s.loc} · {s.ip} · {s.ts}</span>
                  </div>
                  {!s.current && (
                    <button className="btn ghost" style={{height: 26, fontSize: 11.5}} onClick={() => onRevokeSession(s.id)}>
                      <Icon name="close" size={11} /> {t('platform.me.settings.force_logout', '强制下线')}
                    </button>
                  )}
                </li>
              ))}
            </ul>
        </Modal>
      )}
      {historyOpen && (
        <Modal
          open
          eyebrow={t('platform.me.settings.login_history_eyebrow', '登录日志')}
          title={t('platform.me.settings.login_history_title', { n: loginHistory.length, defaultValue: `最近登录 · ${loginHistory.length} 次` })}
          width={640}
          onClose={() => setHistoryOpen(false)}
          footer={<>
            <span className="muted-2" style={{fontSize: 11.5}}>GET /api/auth/login-history</span>
            <div style={{display: "flex", gap: 8}}>
              <button className="btn ghost" onClick={() => setHistoryOpen(false)}>{t('common.close', '关闭')}</button>
              <button className="btn ghost" onClick={() => {
                const url = window.api.base + "/api/v1/auth/login-history?format=csv";
                window.open(url, "_blank");
              }}><Icon name="download" size={12} /> {t('platform.me.settings.export_csv', '导出 CSV')}</button>
            </div>
          </>}
        >
            <ul className="pl-session-list">
              {loginHistory.length === 0 ? (
                <li className="muted" style={{padding: 16, textAlign: "center"}}>{t('platform.me.settings.history_empty', '暂无记录')}</li>
              ) : loginHistory.map((r, i) => (
                <li key={i} className="pl-history-row">
                  <span className="mono muted-2" style={{fontSize: 11, width: 92}}>{r.ts}</span>
                  <span style={{fontSize: 12.5, flex: 1, minWidth: 0}}>{r.dev}</span>
                  <span className="mono muted-2" style={{fontSize: 11}}>{r.ip}</span>
                  {r.result === "ok" ? (
                    <span className="pill ok" style={{fontSize: 10.5}}><span className="dot ok" /> {t('platform.me.settings.login_ok', '成功')}</span>
                  ) : (
                    <span className="pill danger" style={{fontSize: 10.5}}><span className="dot danger" /> {t('platform.me.settings.login_blocked', '已拦截')}</span>
                  )}
                </li>
              ))}
            </ul>
        </Modal>
      )}
      {policyOpen && (
        <Modal
          open
          eyebrow={t('platform.me.settings.policy_eyebrow', '隐私政策摘要')}
          title={t('platform.me.settings.policy_title', '我们如何处理你的数据')}
          width={680}
          onClose={() => setPolicyOpen(false)}
          footer={<>
            <a className="muted" style={{fontSize: 12}} href="#" onClick={(e) => e.preventDefault()}>{t('platform.me.settings.policy_full_link', '查看完整政策（外链）')}</a>
            <button className="btn primary" onClick={() => setPolicyOpen(false)}>{t('platform.me.settings.policy_read', '我已阅读')}</button>
          </>}
        >
            <div style={{fontSize: 13, lineHeight: 1.7, color: "var(--text-quiet)", maxHeight: 360, overflow: "auto"}}>
              <p><strong>{t('platform.me.settings.policy_p1_title', '1. 我们收集什么')}</strong>：{t('platform.me.settings.policy_p1_body', '账号信息（用户名、邮箱、可选手机）、设备指纹（用于会话）、用量遥测（仅在你开启时）。')}</p>
              <p><strong>{t('platform.me.settings.policy_p2_title', '2. 我们 不 收集什么')}</strong>：{t('platform.me.settings.policy_p2_body', '剧本正文、玩家变量、私聊、长期记忆、世界书条目——这些数据加密存储在你的工作区，团队 无 任何访问。')}</p>
              <p><strong>{t('platform.me.settings.policy_p3_title', '3. 与第三方')}</strong>：{t('platform.me.settings.policy_p3_body', '不向第三方分享剧本内容。模型 API 调用按你配置直接发往对应厂商（OpenAI / Anthropic 等），团队 不 代理也 不 留存。')}</p>
              <p><strong>{t('platform.me.settings.policy_p4_title', '4. 数据所有权')}</strong>：{t('platform.me.settings.policy_p4_body', '你可以随时通过『导出我的数据』申请完整归档；可随时『停用账号』（90 天保留）或『永久删除』（立刻执行）。')}</p>
              <p><strong>{t('platform.me.settings.policy_p5_title', '5. 合规')}</strong>：{t('platform.me.settings.policy_p5_body', '本平台符合 GDPR · 中国《个人信息保护法》· 加州 CCPA。')}</p>
            </div>
        </Modal>
      )}
    </CSSpaceBetween>
  );
}

export { MeUserSettings };
