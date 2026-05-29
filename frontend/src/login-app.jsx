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
function SchemaField({ field, value, onChange }) {
  return (
    <div className="pl-field">
      <label>
        {field.label}
        {field.required && <span className="pl-field-req">*</span>}
      </label>
      <input
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

function LoginApp() {
  const [mode, setMode] = useState('login');     // 'login' | 'register'
  const [schema, setSchema] = useState(null);    // { login: [...], register: [...], notes: {...} }
  const [schemaErr, setSchemaErr] = useState('');
  const [values, setValues] = useState({});      // {[fieldKey]: string}
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [notice, setNotice] = useState('');

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
        if (!cancelled) setSchemaErr(e?.message || '无法加载表单定义');
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const fields = schema?.[mode] || [];
  const minPw = schema?.notes?.min_password_length || 8;
  const inviteOnly = !!schema?.notes?.invite_only;

  const setField = (k, v) => setValues((prev) => ({ ...prev, [k]: v }));

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setErr(''); setNotice('');

    // 必填校验(前端 + 后端会再校验一次)
    for (const f of fields) {
      const v = (values[f.key] || '').trim();
      if (f.required && !v) {
        setErr(`请填写${f.label}`);
        return;
      }
      if (f.min_length && v.length > 0 && v.length < f.min_length) {
        setErr(`${f.label}至少 ${f.min_length} 位`);
        return;
      }
    }

    setBusy(true);
    try {
      const body = {};
      for (const f of fields) {
        const v = (values[f.key] || '').trim();
        // 可选字段空值不发,让后端兜底
        if (!f.required && !v) continue;
        // password 不 trim 末尾的空白(用户允许密码带空格),用 raw
        body[f.key] = f.type === 'password' ? (values[f.key] || '') : v;
      }

      if (mode === 'register') {
        await window.api.auth.register(body);
      } else {
        await window.api.auth.login(body);
      }
      setNotice(mode === 'register' ? '注册成功,正在进入…' : '登录成功');
      setTimeout(() => location.replace(__resolveNextOrDefault()), 200);
    } catch (e) {
      setErr(e?.message || '请求失败');
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
            <div className="pl-auth-sub">长篇小说拆书 · RPG 续写 · 多用户创作平台</div>
          </div>
        </div>

        <div className="pl-auth-tabs" role="tablist">
          <button type="button" role="tab"
                  className={mode === 'login' ? 'active' : ''}
                  aria-selected={mode === 'login'}
                  onClick={() => { setMode('login'); setErr(''); setNotice(''); }}>登录</button>
          <button type="button" role="tab"
                  className={mode === 'register' ? 'active' : ''}
                  aria-selected={mode === 'register'}
                  onClick={() => { setMode('register'); setErr(''); setNotice(''); }}
                  disabled={inviteOnly}
                  data-tip={inviteOnly ? '当前部署为邀请制,注册关闭' : undefined}>注册</button>
        </div>

        <form className="pl-auth-form" onSubmit={submit}>
          {schemaErr && (
            <div className="pl-auth-error"
                 style={{color: 'var(--danger)', fontSize: 12.5, padding: '4px 0'}}>
              表单加载失败:{schemaErr}
            </div>
          )}

          {!schema && !schemaErr && (
            <div style={{color: 'var(--muted)', fontSize: 12.5, padding: '4px 0'}}>
              正在加载…
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
            {busy ? '正在提交…' : (mode === 'login' ? '登录' : '创建账号')}
          </button>

          <div className="pl-auth-foot">
            <span>
              {schema?.notes?.first_user_is_admin
                ? '首个注册用户会成为管理员。'
                : ''}
              {schema?.notes?.invite_only
                ? '当前部署为邀请制,请联系管理员获取账号。'
                : ''}
              {!schema?.notes?.invite_only && !schema?.notes?.first_user_is_admin
                ? `密码至少 ${minPw} 位。`
                : ''}
            </span>
            <a href="#"
               onClick={(e) => {
                 e.preventDefault();
                 setNotice('请联系管理员重置密码(暂未提供自助找回流程)');
               }}
               style={{borderBottom: 0, color: 'var(--muted)', cursor: 'pointer'}}>忘记密码</a>
          </div>
        </form>
      </div>
    </div>
  );
}

window.LoginApp = LoginApp;
export { LoginApp };
