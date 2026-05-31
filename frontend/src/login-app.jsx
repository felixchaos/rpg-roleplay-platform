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
import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';

const __DEFAULT_NEXT = 'Platform.html';

function __resolveNextOrDefault() {
  try {
    const raw = new URLSearchParams(location.search).get('next') || '';
    if (!raw) return __DEFAULT_NEXT;
    // 拒绝绝对 URL / 协议相对 URL / 包含换行的输入(开放重定向防御)
    if (/^[a-z][a-z0-9+.\-]*:|^\/\//i.test(raw) || /[\r\n]/.test(raw)) return __DEFAULT_NEXT;
    return raw;
  } catch (_) { return __DEFAULT_NEXT; }
}

/// 渲染单个表单字段。`field` 形如:
///   { key, label, type, required, autocomplete, placeholder, min_length, max_length }
/// 当 type === 'boolean' 时渲染为 checkbox。
function SchemaField({ field, value, onChange }) {
  const { t } = useTranslation();
  if (field.type === 'boolean') {
    // 为 terms_accepted 字段注入带链接的 label;其余 boolean 字段用纯文本
    // 法律文档正本托管在 landing 站(play.stellatrix.icu/legal/),软件内不复制以避免双权威。
    // landing 的 legal/ 已发布 v1.2 双语 6 篇:privacy/terms/acceptable-use/cookie/dmca/adult-content-disclaimer
    const _legalBase = 'https://play.stellatrix.icu/legal';
    const _legalLang = (typeof navigator !== 'undefined' && /^en/i.test(navigator.language || '')) ? 'en' : 'zh-CN';
    const labelNode = field.key === 'terms_accepted' ? (
      <span>
        {t('auth.terms_agree')}{' '}
        <a href={`${_legalBase}/terms-of-service.${_legalLang}.html`} target="_blank" rel="noopener noreferrer"
           style={{color: 'var(--accent)'}}>{t('auth.terms_of_service')}</a>
        {'、'}
        <a href={`${_legalBase}/privacy-policy.${_legalLang}.html`} target="_blank" rel="noopener noreferrer"
           style={{color: 'var(--accent)'}}>{t('auth.privacy_policy')}</a>
        {'、'}
        <a href={`${_legalBase}/acceptable-use-policy.${_legalLang}.html`} target="_blank" rel="noopener noreferrer"
           style={{color: 'var(--accent)'}}>{t('auth.acceptable_use')}</a>
        {' '}{t('auth.terms_and')}{' '}
        <a href={`${_legalBase}/adult-content-disclaimer.${_legalLang}.html`} target="_blank" rel="noopener noreferrer"
           style={{color: 'var(--accent)'}}>{t('auth.adult_disclaimer')}</a>
        {field.required && <span className="pl-field-req">*</span>}
      </span>
    ) : (
      <span>{field.label}{field.required && <span className="pl-field-req">*</span>}</span>
    );
    return (
      <div className="pl-field" style={{flexDirection: 'row', alignItems: 'flex-start', gap: 8, marginTop: 6}}>
        <input
          id={field.key}
          type="checkbox"
          checked={!!value}
          onChange={(e) => onChange(e.target.checked)}
          style={{marginTop: 3, flexShrink: 0, accentColor: 'var(--accent)'}}
        />
        <label htmlFor={field.key} style={{fontWeight: 'normal', cursor: 'pointer', fontSize: 13}}>
          {labelNode}
        </label>
      </div>
    );
  }
  return (
    <div className="pl-field">
      <label htmlFor={field.key}>
        {field.label}
        {field.required && <span className="pl-field-req">*</span>}
      </label>
      <input
        id={field.key}
        type={field.type || 'text'}
        autoComplete={field.autocomplete || undefined}
        placeholder={field.placeholder || undefined}
        minLength={field.min_length || undefined}
        maxLength={field.max_length || undefined}
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function OtpInput({ value, onChange, onComplete, length = 6, disabled = false, autoFocus = false, label }) {
  const inputRef = React.useRef(null);
  const completeRef = React.useRef('');
  const clean = String(value || '').replace(/\D/g, '').slice(0, length);

  React.useEffect(() => {
    if (!autoFocus) return;
    const timer = setTimeout(() => inputRef.current?.focus(), 0);
    return () => clearTimeout(timer);
  }, [autoFocus]);

  React.useEffect(() => {
    if (clean.length === length && clean !== completeRef.current) {
      completeRef.current = clean;
      onComplete?.(clean);
    }
    if (clean.length < length) completeRef.current = '';
  }, [clean, length, onComplete]);

  return (
    <div className="pl-otp" onClick={() => inputRef.current?.focus()}>
      <input
        ref={inputRef}
        className="pl-otp-input"
        aria-label={label}
        type="text"
        inputMode="numeric"
        pattern={`\\d{${length}}`}
        maxLength={length}
        autoComplete="one-time-code"
        value={clean}
        disabled={disabled}
        onChange={(e) => onChange(String(e.target.value || '').replace(/\D/g, '').slice(0, length))}
        onPaste={(e) => {
          const pasted = e.clipboardData?.getData('text') || '';
          const next = pasted.replace(/\D/g, '').slice(0, length);
          if (next) {
            e.preventDefault();
            onChange(next);
          }
        }}
      />
      {Array.from({ length }).map((_, i) => (
        <div key={i} className={`pl-otp-box ${clean[i] ? 'filled' : ''}`}>
          {clean[i] || ''}
        </div>
      ))}
    </div>
  );
}

function LoginApp() {
  const { t } = useTranslation();
  const [mode, setMode] = useState('login');     // 'login' | 'code-login' | 'register' | 'verify' | 'forgot' | 'reset'
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
  // forgot/reset state
  const [forgotEmail, setForgotEmail] = useState('');
  const [resetToken, setResetToken] = useState('');
  const [resetPw, setResetPw] = useState('');
  const [resetPwConfirm, setResetPwConfirm] = useState('');

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
        const base = window.__API_BASE || '';
        const r = await fetch(`${base}/api/v1/auth/register`, {
          method: 'POST',
          credentials: 'include',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body),
        });
        const j = await r.json();
        if (!j.ok) throw new Error(j.error || t('auth.register_fail'));
        // 两步流程：进入验证码步骤
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
    }
  };

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

        {mode !== 'verify' && mode !== 'forgot' && mode !== 'reset' && (
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
          <form className="pl-auth-form" onSubmit={handleVerify}>
            <div style={{fontSize: 13, color: 'var(--muted)', marginBottom: 8}}>
              {t('auth.verify.sent_to')} <strong>{pendingEmail}</strong>{t('auth.verify.expires')}
            </div>
            <div className="pl-field">
              <label htmlFor="verify_code">{t('auth.verify.code_label')}</label>
              <OtpInput
                value={verifyCode}
                onChange={setVerifyCode}
                onComplete={(code) => handleVerify(null, code)}
                disabled={busy}
                autoFocus
                label={t('auth.verify.code_label')}
              />
            </div>
            {err && (
              <div className="pl-auth-error" role="alert"
                   style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>{err}</div>
            )}
            {notice && (
              <div className="pl-auth-notice" role="status" aria-live="polite"
                   style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0',
                           borderLeft: '2px solid var(--accent)', paddingLeft: 8}}>{notice}</div>
            )}
            <button type="submit" className="btn primary" disabled={busy || verifyCode.length !== 6}
                    style={{justifyContent: 'center', height: 34, opacity: busy ? 0.7 : 1}}>
              {busy ? t('auth.verify.verifying') : t('auth.verify.verify_btn')}
            </button>
            <div className="pl-auth-foot" style={{justifyContent: 'space-between'}}>
              <button type="button" style={{background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 13, padding: 0}}
                      onClick={() => { setMode('register'); setErr(''); setNotice(''); }}>
                {t('auth.verify.back')}
              </button>
              <button type="button"
                      disabled={resendCooldown > 0 || busy}
                      style={{background: 'none', border: 'none', color: resendCooldown > 0 ? 'var(--muted)' : 'var(--accent)', cursor: resendCooldown > 0 ? 'default' : 'pointer', fontSize: 13, padding: 0}}
                      onClick={handleResend}>
                {resendCooldown > 0 ? t('auth.verify.resend_cooldown', { s: resendCooldown }) : t('auth.verify.resend')}
              </button>
            </div>
          </form>
        )}

        {/* ── 邮箱验证码登录 ─────────────────────────────────────────── */}
        {mode === 'code-login' && (
          <form className="pl-auth-form" onSubmit={(e) => loginCodeSent ? handleLoginCodeVerify(e) : (e.preventDefault(), requestLoginCode(loginCodeEmail))}>
            {!loginCodeSent ? (
              <>
                <div style={{fontSize: 13, color: 'var(--muted)', marginBottom: 8}}>
                  {t('auth.login_code.desc')}
                </div>
                <div className="pl-field">
                  <label htmlFor="login_code_email">{t('auth.login_code.email_label')}</label>
                  <input
                    id="login_code_email"
                    type="email"
                    autoComplete="email"
                    value={loginCodeEmail}
                    onChange={(e) => setLoginCodeEmail(e.target.value)}
                    autoFocus
                  />
                </div>
              </>
            ) : (
              <>
                <div style={{fontSize: 13, color: 'var(--muted)', marginBottom: 8}}>
                  {t('auth.verify.sent_to')} <strong>{loginCodeEmailMask}</strong>{t('auth.verify.expires')}
                </div>
                <div className="pl-field">
                  <label htmlFor="login_code">{t('auth.login_code.code_label')}</label>
                  <OtpInput
                    value={loginCode}
                    onChange={setLoginCode}
                    onComplete={(code) => handleLoginCodeVerify(null, code)}
                    disabled={busy}
                    autoFocus
                    label={t('auth.login_code.code_label')}
                  />
                </div>
              </>
            )}
            {err && (
              <div className="pl-auth-error" role="alert"
                   style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>{err}</div>
            )}
            {notice && (
              <div className="pl-auth-notice" role="status" aria-live="polite"
                   style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0',
                           borderLeft: '2px solid var(--accent)', paddingLeft: 8}}>{notice}</div>
            )}
            <button type="submit" className="btn primary"
                    disabled={busy || (loginCodeSent ? loginCode.length !== 6 : !loginCodeEmail.trim())}
                    style={{justifyContent: 'center', height: 34, opacity: busy ? 0.7 : 1}}>
              {busy
                ? (loginCodeSent ? t('auth.login_code.verifying') : t('auth.login_code.sending'))
                : (loginCodeSent ? t('auth.login_code.verify_btn') : t('auth.login_code.send_btn'))}
            </button>
            {loginCodeSent && (
              <div className="pl-auth-foot" style={{justifyContent: 'space-between'}}>
                <button type="button" style={{background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 13, padding: 0}}
                        onClick={() => { setLoginCodeSent(false); setLoginCode(''); setErr(''); setNotice(''); }}>
                  {t('auth.login_code.back')}
                </button>
                <button type="button"
                        disabled={resendCooldown > 0 || busy}
                        style={{background: 'none', border: 'none', color: resendCooldown > 0 ? 'var(--muted)' : 'var(--accent)', cursor: resendCooldown > 0 ? 'default' : 'pointer', fontSize: 13, padding: 0}}
                        onClick={handleResend}>
                  {resendCooldown > 0 ? t('auth.verify.resend_cooldown', { s: resendCooldown }) : t('auth.verify.resend')}
                </button>
              </div>
            )}
          </form>
        )}

        {/* ── 忘记密码表单 ─────────────────────────────────────────── */}
        {mode === 'forgot' && (
          <form className="pl-auth-form" onSubmit={handleForgot}>
            <div style={{fontSize: 13, color: 'var(--muted)', marginBottom: 8}}>
              {t('auth.forgot_desc')}
            </div>
            <div className="pl-field">
              <label htmlFor="forgot_email">{t('auth.forgot_email')}</label>
              <input
                id="forgot_email"
                type="email"
                autoComplete="email"
                value={forgotEmail}
                onChange={(e) => setForgotEmail(e.target.value)}
                autoFocus
              />
            </div>
            {err && (
              <div className="pl-auth-error" role="alert"
                   style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>{err}</div>
            )}
            {notice && (
              <div className="pl-auth-notice" role="status" aria-live="polite"
                   style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0',
                           borderLeft: '2px solid var(--accent)', paddingLeft: 8}}>{notice}</div>
            )}
            <button type="submit" className="btn primary" disabled={busy}
                    style={{justifyContent: 'center', height: 34, opacity: busy ? 0.7 : 1}}>
              {busy ? t('auth.submitting') : t('auth.forgot_send')}
            </button>
            <div className="pl-auth-foot">
              <button type="button" style={{background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer', fontSize: 13, padding: 0}}
                      onClick={() => { setMode('login'); setErr(''); setNotice(''); }}>
                {t('auth.forgot_back_to_login')}
              </button>
            </div>
          </form>
        )}

        {/* ── 重置密码表单 ─────────────────────────────────────────── */}
        {mode === 'reset' && (
          <form className="pl-auth-form" onSubmit={handleReset}>
            <div style={{fontSize: 13, color: 'var(--muted)', marginBottom: 8}}>
              {t('auth.reset_desc')}
            </div>
            <div className="pl-field">
              <label htmlFor="reset_pw">{t('auth.reset_new_pw')}</label>
              <input
                id="reset_pw"
                type="password"
                autoComplete="new-password"
                value={resetPw}
                onChange={(e) => setResetPw(e.target.value)}
                autoFocus
              />
            </div>
            <div className="pl-field">
              <label htmlFor="reset_pw_confirm">{t('auth.reset_confirm')}</label>
              <input
                id="reset_pw_confirm"
                type="password"
                autoComplete="new-password"
                value={resetPwConfirm}
                onChange={(e) => setResetPwConfirm(e.target.value)}
              />
            </div>
            {err && (
              <div className="pl-auth-error" role="alert"
                   style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>{err}</div>
            )}
            {notice && (
              <div className="pl-auth-notice" role="status" aria-live="polite"
                   style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0',
                           borderLeft: '2px solid var(--accent)', paddingLeft: 8}}>{notice}</div>
            )}
            <button type="submit" className="btn primary" disabled={busy}
                    style={{justifyContent: 'center', height: 34, opacity: busy ? 0.7 : 1}}>
              {busy ? t('auth.submitting') : t('auth.reset_submit')}
            </button>
          </form>
        )}

        {/* ── 登录 / 注册表单 ────────────────────────────────────────── */}
        {mode !== 'verify' && mode !== 'code-login' && mode !== 'forgot' && mode !== 'reset' && <form className="pl-auth-form" onSubmit={submit}>
          {schemaErr && (
            <div className="pl-auth-error"
                 style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>
              {t('auth.schema_err')}{schemaErr}
            </div>
          )}

          {!schema && !schemaErr && (
            <div style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0'}}>
              {t('auth.schema_loading')}
            </div>
          )}

          {fields.map((f) => (
            <SchemaField key={f.key} field={f}
                         value={values[f.key]}
                         onChange={(v) => setField(f.key, v)} />
          ))}

          {err && (
            <div className="pl-auth-error" role="alert"
                 style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>
              {err}
            </div>
          )}

          {notice && (
            <div className="pl-auth-notice" role="status" aria-live="polite"
                 style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0',
                         borderLeft: '2px solid var(--accent)', paddingLeft: 8}}>
              {notice}
            </div>
          )}

          <button type="submit" className="btn primary" disabled={busy || !schema}
                  style={{justifyContent: 'center', height: 34, opacity: busy ? 0.7 : 1}}>
            {busy ? t('auth.submitting') : (mode === 'login' ? t('auth.login_btn') : t('auth.register_btn'))}
          </button>

          <div className="pl-auth-foot">
            <span>
              {schema?.notes?.first_user_is_admin
                ? t('auth.first_admin')
                : ''}
              {schema?.notes?.invite_only
                ? t('auth.invite_only_note')
                : ''}
              {!schema?.notes?.invite_only && !schema?.notes?.first_user_is_admin
                ? t('auth.min_password', { min: minPw })
                : ''}
            </span>
            <a href="#"
               onClick={(e) => {
                 e.preventDefault();
                 setForgotEmail('');
                 setErr(''); setNotice('');
                 setMode('forgot');
               }}
               style={{borderBottom: 0, color: 'var(--muted)', cursor: 'pointer'}}>{t('auth.forget_password')}</a>
          </div>
        </form>}
      </div>
    </div>
  );
}

export { LoginApp };
