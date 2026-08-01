// login-app.jsx — 独立 Login 页主组件
//
// 设计基线:
//   1. 视觉系统严格对齐 platform.css 里既有的 `.pl-auth-*` 命名空间(暖灰深色 +
//      陶土橙 + Noto Serif SC 标题 + Noto Sans SC 正文)
//   2. **表单字段由后端 GET /api/v1/auth/schema 决定**,不在前端硬编码
//      — 加字段只需后端改 schema(rust/crates/rpg-routes/src/auth.rs::api_auth_schema)
//   3. 已登录用户直接 location.replace(?next=... 或 Platform.html),避免回环
//
// 与原 platform-app.jsx 内 AuthPage 的区别:
//   - 不依赖 PlatformShell 的 toast / nav 注入
//   - 字段循环渲染,不再写死 `username/password/display_name`
//   - 可作为 Vite 独立入口,跟 PlatformApp 完全解耦

import React from 'react';
import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { __resolveNextOrDefault } from './components/login/nextTarget.js';
import { InviteScreen } from './components/login/InviteScreen.jsx';
import { VerifyForm, MagicOtpForm, NeedsProfileForm, CodeLoginForm, ForgotForm, ResetForm, MainAuthForm } from './components/login/AuthForms.jsx';
function LoginApp() {
  const { t } = useTranslation();
  const [mode, setMode] = useState('login');     // 'login' | 'code-login' | 'register' | 'verify' | 'forgot' | 'reset' | 'magic-otp' | 'needs-profile'
  const [schema, setSchema] = useState(null);    // { login: [...], register: [...], notes: {...} }
  const [schemaErr, setSchemaErr] = useState('');
  const [values, setValues] = useState({});      // {[fieldKey]: string}
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [notice, setNotice] = useState('');
  // verify step state
  const [pendingEmail, setPendingEmail] = useState('');      // masked email for display
  const [pendingEmailRaw, setPendingEmailRaw] = useState(''); // real email for API calls
  const [verifyCode, setVerifyCode] = useState('');
  const [resendCooldown, setResendCooldown] = useState(0);   // seconds remaining
  // passwordless login state
  const [loginCodeEmail, setLoginCodeEmail] = useState('');
  const [loginCodeEmailMask, setLoginCodeEmailMask] = useState('');
  const [loginCodeSent, setLoginCodeSent] = useState(false);
  const [loginCode, setLoginCode] = useState('');
  // magic-link OTP state
  const [magicEmail, setMagicEmail] = useState('');
  const [magicCode, setMagicCode] = useState('');
  // profile completion state
  const [profileUsername, setProfileUsername] = useState('');
  const [profileDisplayName, setProfileDisplayName] = useState('');
  // forgot/reset state
  const [forgotEmail, setForgotEmail] = useState('');
  const [resetToken, setResetToken] = useState('');
  const [resetPw, setResetPw] = useState('');
  const [resetPwConfirm, setResetPwConfirm] = useState('');
  // Cloudflare Turnstile 人机验证（仅当后端透出 sitekey 时启用）
  const [tsToken, setTsToken] = useState('');
  const tsRef = useRef(null);
  const tsWidgetId = useRef(null);
  // 自部署「邀请链接」轻量注册: ?invite=TOKEN → 用户名+密码即可加入(无邮箱)
  const [inviteToken, setInviteToken] = useState('');
  const [inviteUsername, setInviteUsername] = useState('');
  const [invitePassword, setInvitePassword] = useState('');
  const [inviteAge, setInviteAge] = useState(false);
  const [inviteBusy, setInviteBusy] = useState(false);
  const [inviteErr, setInviteErr] = useState('');

  // 1) 已登录直接走开 — 不要让用户重复登录
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await window.api?.auth.me();
        if (!cancelled && me && me.user) {
          location.replace(__resolveNextOrDefault());
        }
      } catch (_) { /* 未登录,正常停留 */ }
    })();
    return () => { cancelled = true; };
  }, []);

  // 2) 拉表单 schema
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const base = window.__API_BASE || '';
        const r = await fetch(`${base}/api/v1/auth/schema`, { credentials: 'include' });
        const j = await r.json();
        if (!cancelled) setSchema(j);
      } catch (e) {
        if (!cancelled) setSchemaErr(e?.message || t('auth.schema_fail'));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // 2b) 检测邮件链接中的 #reset?token=... 跳转重置模式
  useEffect(() => {
    try {
      const hash = location.hash; // e.g. #reset?token=abc123
      if (hash.startsWith('#reset')) {
        const qs = new URLSearchParams(hash.slice(hash.indexOf('?') + 1));
        const tok = qs.get('token') || '';
        if (tok) {
          setResetToken(tok);
          setMode('reset');
        }
      }
    } catch (_) {}
  }, []);

  // 2b2) 自部署邀请链接: ?invite=TOKEN → 切到轻量注册屏(用户名+密码加入)
  useEffect(() => {
    try {
      const tok = new URLSearchParams(location.search).get('invite') || '';
      if (tok) setInviteToken(tok);
    } catch (_) {}
  }, []);

  // 2c) 检测 landing magic-link: ?magic=TOKEN&email=EMAIL
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const qs = new URLSearchParams(location.search);
        const magicToken = qs.get('magic') || '';
        const emailParam = qs.get('email') || '';
        if (!magicToken || !emailParam) return;
        setBusy(true);
        setNotice(t('auth.app.magic_verifying'));
        const base = window.__API_BASE || '';
        const r = await fetch(`${base}/api/auth/magic-consume`, {
          method: 'POST',
          credentials: 'include',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({magic_token: magicToken, email: emailParam}),
        });
        const j = await r.json();
        if (cancelled) return;
        if (j.ok && (j.session_token || j.user_id)) {
          // task: magic link 直接登录(不再发 OTP — magic_token 本身就是认证)。
          // 后端已 set-cookie + 返 needs_profile → 跳 Platform(如需补昵称 Welcome modal 会触发)
          setNotice(t('auth.app.magic_login_ok'));
          setErr('');
          // 清掉 magic 参数防回退按钮重放
          try { history.replaceState(null, '', location.pathname); } catch (_) {}
          setTimeout(() => { location.href = (j.needs_profile ? '/profile-setup' : '/profile'); }, 500);
        } else if (j.ok && j.next === 'otp') {
          // 旧版后端兼容(部署期回退)
          setMagicEmail(j.email || emailParam);
          setMagicCode('');
          setErr('');
          setNotice(t('auth.app.magic_otp_sent', { email: j.email || emailParam }));
          setMode('magic-otp');
        } else {
          setErr(j.error || t('auth.app.magic_link_invalid'));
          setNotice('');
        }
      } catch (e) {
        if (!cancelled) setErr(e?.message || t('auth.app.magic_link_fail'));
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 2d) Cloudflare Turnstile：后端透出 sitekey 时加载脚本（一次）
  const turnstileSitekey = schema?.notes?.turnstile_sitekey || '';
  useEffect(() => {
    if (!turnstileSitekey) return;
    if (window.turnstile) return;
    if (document.querySelector('script[data-cf-turnstile]')) return;
    const s = document.createElement('script');
    s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    s.async = true; s.defer = true;
    s.setAttribute('data-cf-turnstile', '1');
    document.head.appendChild(s);
  }, [turnstileSitekey]);

  // 2e) 注册表单可见时渲染挂件；离开注册态时销毁挂件并清 token
  useEffect(() => {
    if (!turnstileSitekey || mode !== 'register') { setTsToken(''); return; }
    let cancelled = false;
    let tries = 0;
    const tick = () => {
      if (cancelled) return;
      if (!window.turnstile || !tsRef.current) {
        if (tries++ < 100) setTimeout(tick, 100);  // 等脚本就绪（最长 ~10s）
        return;
      }
      if (tsWidgetId.current != null) return;       // 已渲染，避免重复
      try {
        tsWidgetId.current = window.turnstile.render(tsRef.current, {
          sitekey: turnstileSitekey,
          callback: (token) => setTsToken(token || ''),
          'expired-callback': () => setTsToken(''),
          'error-callback': () => setTsToken(''),
        });
      } catch (_) { /* 脚本未就绪/重复渲染，忽略 */ }
    };
    tick();
    return () => {
      cancelled = true;
      try {
        if (tsWidgetId.current != null && window.turnstile) {
          window.turnstile.remove(tsWidgetId.current);
        }
      } catch (_) {}
      tsWidgetId.current = null;
      setTsToken('');
    };
  }, [turnstileSitekey, mode]);

  const fields = ['verify', 'code-login', 'forgot', 'reset'].includes(mode) ? [] : (schema?.[mode] || []);
  const minPw = schema?.notes?.min_password_length || 8;
  const inviteOnly = !!schema?.notes?.invite_only;

  const setField = (k, v) => setValues((prev) => ({ ...prev, [k]: v }));

  // 后端 error_key → 友好文案映射(后端 400 时查 'auth.*' key)
  // 前端 field key → 同样文案(前端预校验 boolean 字段时查 'terms_accepted' / 'age_confirmed')
  const CONSENT_ERRORS = {
    'auth.terms_not_accepted': t('auth.terms_not_accepted'),
    'auth.age_not_confirmed': t('auth.age_not_confirmed'),
    'terms_accepted': t('auth.terms_not_accepted'),
    'age_confirmed': t('auth.age_not_confirmed'),
  };

  // 倒计时 effect
  React.useEffect(() => {
    if (resendCooldown <= 0) return;
    const t = setTimeout(() => setResendCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [resendCooldown]);

  const requestLoginCode = async (email, { resend = false } = {}) => {
    const cleanEmail = String(email || '').trim();
    if (!cleanEmail || !cleanEmail.includes('@')) {
      setErr(t('auth.login_code.email_required'));
      return;
    }
    setBusy(true);
    setErr(''); setNotice('');
    try {
      const j = await window.api.auth.loginCodeRequest({ email: cleanEmail });
      if (!j || j.ok === false) throw new Error(j?.error || t('auth.login_code.send_fail'));
      // 邮箱没注册:后端不会发信,这里必须把人领去注册,否则他会对着验证码框等一封
      // 永远不会到的邮件(群反馈 08-01)。邮箱带过去预填,少让人重打一遍。
      if (j.registered === false) {
        setLoginCodeSent(false);
        setLoginCode('');
        setValues((v) => ({ ...v, email: cleanEmail }));
        setMode('register');
        setErr('');
        setNotice(t('auth.login_code.not_registered', {
          defaultValue: '这个邮箱还没有账号，已为你转到注册页（邮箱已填好）。',
        }));
        return;
      }
      setLoginCodeEmail(cleanEmail);
      setLoginCodeEmailMask(j.email_mask || cleanEmail);
      setLoginCodeSent(true);
      setLoginCode('');
      setResendCooldown(60);
      setNotice(resend ? t('auth.verify.resend_ok') : t('auth.login_code.sent_notice', { mask: j.email_mask || cleanEmail }));
    } catch (e) {
      setErr(e?.message || t('auth.login_code.send_fail'));
    } finally {
      setBusy(false);
    }
  };

  const handleResend = async () => {
    if (resendCooldown > 0 || busy) return;
    if (mode === 'code-login') {
      await requestLoginCode(loginCodeEmail, { resend: true });
      return;
    }
    setBusy(true);
    setErr('');
    try {
      const base = window.__API_BASE || '';
      const r = await fetch(`${base}/api/v1/auth/resend-code`, {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email: pendingEmailRaw}),
      });
      const j = await r.json();
      if (j.ok) {
        setNotice(t('auth.verify.resend_ok'));
        setResendCooldown(60);
      } else {
        setErr(j.error || t('auth.verify.resend_fail'));
      }
    } catch (e) {
      setErr(e?.message || t('auth.verify.resend_fail'));
    } finally {
      setBusy(false);
    }
  };

  const handleVerify = async (e, codeOverride) => {
    e?.preventDefault?.();
    if (busy) return;
    const code = String(codeOverride ?? verifyCode).trim();
    if (code.length !== 6 || !/^\d{6}$/.test(code)) {
      setErr(t('auth.verify.code_invalid'));
      return;
    }
    setBusy(true);
    setErr(''); setNotice('');
    try {
      const base = window.__API_BASE || '';
      const r = await fetch(`${base}/api/v1/auth/verify-email`, {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email: pendingEmailRaw, code}),
      });
      const j = await r.json();
      if (j.ok) {
        setNotice(t('auth.verify.verify_ok'));
        setTimeout(() => location.replace(__resolveNextOrDefault()), 300);
      } else {
        setErr(j.error || t('auth.verify.verify_fail'));
      }
    } catch (e) {
      setErr(e?.message || t('auth.request_fail'));
    } finally {
      setBusy(false);
    }
  };

  const handleLoginCodeVerify = async (e, codeOverride) => {
    e?.preventDefault?.();
    if (busy) return;
    const code = String(codeOverride ?? loginCode).trim();
    if (code.length !== 6 || !/^\d{6}$/.test(code)) {
      setErr(t('auth.verify.code_invalid'));
      return;
    }
    setBusy(true);
    setErr(''); setNotice('');
    try {
      const j = await window.api.auth.loginCodeVerify({ email: loginCodeEmail, code });
      if (!j || j.ok === false) throw new Error(j?.error || t('auth.login_code.verify_fail'));
      setNotice(t('auth.login_code.verify_ok'));
      setTimeout(() => location.replace(__resolveNextOrDefault()), 200);
    } catch (e) {
      setErr(e?.message || t('auth.login_code.verify_fail'));
    } finally {
      setBusy(false);
    }
  };

  const handleMagicOtpVerify = async (e, codeOverride) => {
    e?.preventDefault?.();
    if (busy) return;
    const code = String(codeOverride ?? magicCode).trim();
    if (code.length !== 6 || !/^\d{6}$/.test(code)) {
      setErr(t('auth.verify.code_invalid'));
      return;
    }
    setBusy(true);
    setErr(''); setNotice('');
    try {
      const base = window.__API_BASE || '';
      const r = await fetch(`${base}/api/auth/passwordless-verify`, {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email: magicEmail, code}),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || t('auth.app.magic_otp_verify_fail'));
      if (j.needs_profile) {
        setMode('needs-profile');
        setNotice(t('auth.app.magic_otp_needs_profile'));
      } else {
        setNotice(t('auth.app.login_redirect'));
        setTimeout(() => location.replace(__resolveNextOrDefault()), 300);
      }
    } catch (e) {
      setErr(e?.message || t('auth.app.magic_otp_verify_fail_retry'));
    } finally {
      setBusy(false);
    }
  };

  const handleProfileSubmit = async (e) => {
    e?.preventDefault?.();
    if (busy) return;
    const uname = profileUsername.trim();
    const dname = profileDisplayName.trim();
    if (!uname && !dname) {
      setErr(t('auth.app.profile_required'));
      return;
    }
    setBusy(true);
    setErr(''); setNotice('');
    try {
      const base = window.__API_BASE || '';
      const r = await fetch(`${base}/api/me/profile`, {
        method: 'PATCH',
        credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          ...(uname ? {username: uname} : {}),
          ...(dname ? {display_name: dname} : {}),
        }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error || t('auth.app.profile_save_fail'));
      setNotice(t('auth.app.profile_save_ok'));
      setTimeout(() => location.replace(__resolveNextOrDefault()), 300);
    } catch (e) {
      setErr(e?.message || t('auth.app.profile_save_fail_retry'));
    } finally {
      setBusy(false);
    }
  };

  const handleForgot = async (e) => {
    e.preventDefault();
    if (busy) return;
    const email = forgotEmail.trim();
    if (!email || !email.includes('@')) {
      setErr(t('auth.forgot_email_required'));
      return;
    }
    setBusy(true);
    setErr(''); setNotice('');
    try {
      const base = window.__API_BASE || '';
      await fetch(`${base}/api/auth/forgot-password`, {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email}),
      });
      // 不论结果都显示成功(防枚举)
      setNotice(t('auth.forgot_sent'));
    } catch (_) {
      setNotice(t('auth.forgot_sent'));
    } finally {
      setBusy(false);
    }
  };

  const handleReset = async (e) => {
    e.preventDefault();
    if (busy) return;
    if (resetPw.length < (schema?.notes?.min_password_length || 8)) {
      setErr(t('auth.field_min_length', { label: t('auth.reset_new_pw'), min: schema?.notes?.min_password_length || 8 }));
      return;
    }
    if (resetPw !== resetPwConfirm) {
      setErr(t('auth.reset_pw_mismatch'));
      return;
    }
    setBusy(true);
    setErr(''); setNotice('');
    try {
      const base = window.__API_BASE || '';
      const r = await fetch(`${base}/api/auth/reset-password`, {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({token: resetToken, password: resetPw}),
      });
      const j = await r.json();
      if (j.ok) {
        setNotice(t('auth.reset_success'));
        setTimeout(() => { setMode('login'); setErr(''); setNotice(''); }, 1800);
      } else {
        const errKey = j.error_key || (j.detail && j.detail.error_key);
        if (errKey === 'auth.reset_token_used') setErr(t('auth.reset_token_used'));
        else setErr(t('auth.reset_token_invalid_or_expired'));
      }
    } catch (_) {
      setErr(t('auth.reset_token_invalid_or_expired'));
    } finally {
      setBusy(false);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setErr(''); setNotice('');

    // 必填校验(前端 + 后端会再校验一次)
    for (const f of fields) {
      if (f.type === 'boolean') {
        // checkbox: 必填时要求勾选
        if (f.required && !values[f.key]) {
          const friendly = CONSENT_ERRORS[f.key] || t('auth.checkbox_fallback', { label: f.label });
          setErr(friendly);
          return;
        }
        continue;
      }
      const v = (values[f.key] || '').trim();
      if (f.required && !v) {
        setErr(t('auth.field_required', { label: f.label }));
        return;
      }
      if (f.min_length && v.length > 0 && v.length < f.min_length) {
        setErr(t('auth.field_min_length', { label: f.label, min: f.min_length }));
        return;
      }
    }

    // 注册：人机验证未完成则前端先拦（后端会再校验一次）
    if (mode === 'register' && turnstileSitekey && !tsToken) {
      setErr(t('auth.captcha_required', { defaultValue: '请先完成人机验证后再提交' }));
      return;
    }

    setBusy(true);
    try {
      const body = {};
      for (const f of fields) {
        if (f.type === 'boolean') {
          // boolean 字段：必填直接发；可选且未勾选则跳过
          if (f.required || values[f.key]) body[f.key] = !!values[f.key];
          continue;
        }
        const v = (values[f.key] || '').trim();
        // 可选字段空值不发,让后端兜底
        if (!f.required && !v) continue;
        // password 不 trim 末尾的空白(用户允许密码带空格),用 raw
        body[f.key] = f.type === 'password' ? (values[f.key] || '') : v;
      }

      if (mode === 'register') {
        if (turnstileSitekey) body.turnstile_token = tsToken;
        const base = window.__API_BASE || '';
        const r = await fetch(`${base}/api/v1/auth/register`, {
          method: 'POST',
          credentials: 'include',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body),
        });
        const j = await r.json();
        if (!j.ok) throw new Error(j.error || t('auth.register_fail'));
        // 本地/自托管模式：后端已自动完成注册并登录(免邮箱验证)→ 直接进入,不走验证码页
        if (j.auto_verified) {
          setNotice(t('auth.login_ok'));
          setTimeout(() => location.replace(__resolveNextOrDefault()), 200);
          return;
        }
        // server 模式两步流程：进入验证码步骤
        setPendingEmail(j.email_mask || body.email || '');
        setPendingEmailRaw(body.email || '');
        setVerifyCode('');
        setResendCooldown(60);
        setMode('verify');
        setNotice(t('auth.verify.sent_notice', { mask: j.email_mask }));
      } else {
        await window.api.auth.login(body);
        setNotice(t('auth.login_ok'));
        setTimeout(() => location.replace(__resolveNextOrDefault()), 200);
      }
    } catch (e) {
      // 后端返回 error_key 时展示对应文案
      const errKey = e?.detail?.error_key || e?.error_key;
      if (errKey && CONSENT_ERRORS[errKey]) {
        setErr(CONSENT_ERRORS[errKey]);
      } else {
        setErr(e?.message || t('auth.request_fail'));
      }
    } finally {
      setBusy(false);
      // Turnstile token 单次有效：每次提交后重置挂件，失败重试时才有新 token
      if (mode === 'register' && turnstileSitekey && tsWidgetId.current != null && window.turnstile) {
        try { window.turnstile.reset(tsWidgetId.current); } catch (_) {}
        setTsToken('');
      }
    }
  };

  const submitInvite = async (e) => {
    e?.preventDefault?.();
    setInviteErr('');
    if (!inviteUsername.trim()) { setInviteErr(t('auth.invite.need_username')); return; }
    if ((invitePassword || '').length < 8) { setInviteErr(t('auth.invite.weak_password')); return; }
    if (!inviteAge) { setInviteErr(t('auth.invite.need_age')); return; }
    setInviteBusy(true);
    try {
      const base = window.__API_BASE || '';
      const r = await fetch(`${base}/api/local/register`, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ invite: inviteToken, username: inviteUsername.trim(), password: invitePassword, age_confirmed: true }),
      });
      const j = await r.json();
      if (j.ok) {
        try { history.replaceState(null, '', location.pathname); } catch (_) {}
        location.href = j.next || '/Platform.html';
      } else { setInviteErr(j.error || t('auth.invite.failed')); }
    } catch (err) { setInviteErr(err?.message || t('auth.invite.failed')); }
    finally { setInviteBusy(false); }
  };

  // 邀请链接屏:URL 带 ?invite= 时短路渲染 —— 用户名 + 密码 + 18+ 即可加入(自部署多用户)。
  if (inviteToken) {
    return (
      <InviteScreen submitInvite={submitInvite} inviteUsername={inviteUsername} setInviteUsername={setInviteUsername}
        invitePassword={invitePassword} setInvitePassword={setInvitePassword} inviteAge={inviteAge}
        setInviteAge={setInviteAge} inviteErr={inviteErr} inviteBusy={inviteBusy} />
    );
  }

  return (
    <div className="pl-auth-wrap">
      <div className="pl-auth">
        <div style={{display: 'flex', alignItems: 'center', gap: 12}}>
          <div className="pl-auth-mark" aria-hidden="true">
            {/* 简易标志,等价 platform-app 里 <Icon name="logo"/> 的占位 */}
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 19V5l8 4 8-4v14" />
              <path d="M4 14l8 4 8-4" />
            </svg>
          </div>
          <div>
            <h1>RPG Roleplay</h1>
            <div className="pl-auth-sub">{t('auth.subtitle')}</div>
          </div>
        </div>

        {mode !== 'verify' && mode !== 'forgot' && mode !== 'reset' && mode !== 'magic-otp' && mode !== 'needs-profile' && (
          <div className="pl-auth-tabs" role="tablist">
            <button type="button" role="tab"
                    className={mode === 'login' ? 'active' : ''}
                    aria-selected={mode === 'login'}
                    onClick={() => { setMode('login'); setErr(''); setNotice(''); }}>{t('auth.login_tab')}</button>
            <button type="button" role="tab"
                    className={mode === 'code-login' ? 'active' : ''}
                    aria-selected={mode === 'code-login'}
                    onClick={() => { setMode('code-login'); setErr(''); setNotice(''); }}>{t('auth.login_code_tab')}</button>
            <button type="button" role="tab"
                    className={mode === 'register' ? 'active' : ''}
                    aria-selected={mode === 'register'}
                    onClick={() => { setMode('register'); setErr(''); setNotice(''); }}
                    disabled={inviteOnly}
                    data-tip={inviteOnly ? t('auth.invite_only_tip') : undefined}>{t('auth.register_tab')}</button>
          </div>
        )}

        {/* ── 验证码步骤 ─────────────────────────────────────────────── */}
        {mode === 'verify' && (
          <VerifyForm handleVerify={handleVerify} pendingEmail={pendingEmail} verifyCode={verifyCode} setVerifyCode={setVerifyCode}
            busy={busy} err={err} notice={notice} resendCooldown={resendCooldown}
            setMode={setMode} setErr={setErr} setNotice={setNotice} handleResend={handleResend} />
        )}

        {/* ── Magic-link OTP 步骤 ────────────────────────────────────── */}
        {mode === 'magic-otp' && (
          <MagicOtpForm handleMagicOtpVerify={handleMagicOtpVerify} magicEmail={magicEmail}
            magicCode={magicCode} setMagicCode={setMagicCode} busy={busy} err={err} notice={notice} />
        )}

        {/* ── 首次注册补昵称 ──────────────────────────────────────────── */}
        {mode === 'needs-profile' && (
          <NeedsProfileForm handleProfileSubmit={handleProfileSubmit} profileUsername={profileUsername}
            setProfileUsername={setProfileUsername} profileDisplayName={profileDisplayName}
            setProfileDisplayName={setProfileDisplayName} busy={busy} err={err} notice={notice} />
        )}

        {/* ── 邮箱验证码登录 ─────────────────────────────────────────── */}
        {mode === 'code-login' && (
          <CodeLoginForm loginCodeSent={loginCodeSent} handleLoginCodeVerify={handleLoginCodeVerify} requestLoginCode={requestLoginCode}
            loginCodeEmail={loginCodeEmail} setLoginCodeEmail={setLoginCodeEmail} loginCodeEmailMask={loginCodeEmailMask}
            loginCode={loginCode} setLoginCode={setLoginCode} busy={busy} err={err} notice={notice} resendCooldown={resendCooldown}
            setLoginCodeSent={setLoginCodeSent} setErr={setErr} setNotice={setNotice} handleResend={handleResend} />
        )}

        {/* ── 忘记密码表单 ─────────────────────────────────────────── */}
        {mode === 'forgot' && (
          <ForgotForm handleForgot={handleForgot} forgotEmail={forgotEmail} setForgotEmail={setForgotEmail}
            busy={busy} err={err} notice={notice} setMode={setMode} setErr={setErr} setNotice={setNotice} />
        )}

        {/* ── 重置密码表单 ─────────────────────────────────────────── */}
        {mode === 'reset' && (
          <ResetForm handleReset={handleReset} resetPw={resetPw} setResetPw={setResetPw}
            resetPwConfirm={resetPwConfirm} setResetPwConfirm={setResetPwConfirm} busy={busy} err={err} notice={notice} />
        )}

        {/* ── 登录 / 注册表单 ────────────────────────────────────────── */}
        {mode !== 'verify' && mode !== 'code-login' && mode !== 'forgot' && mode !== 'reset' && mode !== 'magic-otp' && mode !== 'needs-profile' && <MainAuthForm
          submit={submit} schemaErr={schemaErr} schema={schema} fields={fields} values={values} setField={setField}
          err={err} notice={notice} mode={mode} turnstileSitekey={turnstileSitekey} tsRef={tsRef} busy={busy} minPw={minPw}
          setForgotEmail={setForgotEmail} setErr={setErr} setNotice={setNotice} setMode={setMode} />}
      </div>
    </div>
  );
}

export { LoginApp };