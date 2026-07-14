// 内联登录 / 注册页 AuthPage。纯机械从 platform-app.jsx 搬出,零行为变化。
import React from 'react';
import { useState as useStatePL } from 'react';
import { Icon } from '../../game-icons.jsx';

/* ---------------------------- AUTH ----------------------------- */
function AuthPage() {
  const [mode, setMode] = useStatePL("login");
  const [username, setUsername] = useStatePL("");
  const [password, setPassword] = useStatePL("");
  const [displayName, setDisplayName] = useStatePL("");
  const [busy, setBusy] = useStatePL(false);
  const [err, setErr] = useStatePL("");
  // Login/AuthPage 上没有 PlatformShell 注入的 window.__apiToast，需要内联反馈
  // 字段来承接「忘记密码」等次要交互的提示，不能依赖可能不存在的全局 toast。
  const [notice, setNotice] = useStatePL("");

  // 登录后跳哪里：优先 ?next=...（来自 Platform/Game Console 的鉴权 gate），
  // 严格只允许同源相对路径，防止开放重定向（?next=https://evil.com）。
  const __nextOrDefault = () => {
    try {
      const raw = new URLSearchParams(location.search).get("next") || "";
      if (!raw) return "Platform.html";
      // 拒绝包含控制字符的输入
      if (/[\r\n\0]/.test(raw)) return "Platform.html";
      // 严格验证：解析后必须同源，才允许跳转
      const u = new URL(raw, location.href);
      if (u.origin !== location.origin) return "Platform.html";
      return u.pathname + u.search + u.hash;
    } catch (_) { return "Platform.html"; }
  };

  // If already logged in → 跳目标页（next 或 Platform）
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await window.api?.auth.me();
        if (!cancelled && me && me.user) {
          location.replace(__nextOrDefault());
        }
      } catch (_) { /* not logged in, stay */ }
    })();
    return () => { cancelled = true; };
  }, []);

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setErr("");
    if (!username.trim() || !password.trim()) {
      setErr("请填写用户名和密码");
      return;
    }
    setBusy(true);
    try {
      if (mode === "register") {
        await window.api.auth.register({
          username: username.trim(),
          password,
          display_name: displayName.trim() || undefined,
        });
      } else {
        await window.api.auth.login({ username: username.trim(), password });
      }
      window.__apiToast?.(mode === "register" ? "注册成功" : "登录成功", { kind: "ok", duration: 1400 });
      location.replace(__nextOrDefault());
    } catch (e) {
      setErr(e?.message || "请求失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pl-auth-wrap">
      <div className="pl-auth">
        <div style={{display: "flex", alignItems: "center", gap: 12}}>
          <div className="pl-auth-mark"><Icon name="logo" size={16} /></div>
          <div>
            <h1>RPG Roleplay</h1>
            <div className="pl-auth-sub">长篇小说拆书 · RPG 续写 · 多用户创作平台</div>
          </div>
        </div>
        <div className="pl-auth-tabs">
          <button className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>登录</button>
          <button className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>注册</button>
        </div>
        <form className="pl-auth-form" onSubmit={submit}>
          <div className="pl-field">
            <label>用户名</label>
            <input autoComplete="username" placeholder="字母 / 数字 / 下划线"
              value={username} onChange={(e) => setUsername(e.target.value)} />
          </div>
          <div className="pl-field">
            <label>密码</label>
            <input type="password" autoComplete={mode === "register" ? "new-password" : "current-password"}
              placeholder="至少 8 位"
              value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {mode === "register" && (
            <div className="pl-field">
              <label>显示名</label>
              <input placeholder="例：用户名"
                value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
            </div>
          )}
          {err && (
            <div className="pl-auth-error" style={{color: "var(--danger,#c0392b)", fontSize: 12.5, padding: "4px 0"}}>
              {err}
            </div>
          )}
          {notice && (
            <div className="pl-auth-notice" role="status" aria-live="polite"
              style={{color: "var(--muted, #6b7280)", fontSize: 12.5, padding: "4px 0",
                      borderLeft: "2px solid var(--accent, #b65b41)", paddingLeft: 8}}>
              {notice}
            </div>
          )}
          <button type="submit" className="btn primary" disabled={busy}
            style={{justifyContent: "center", height: 34, opacity: busy ? 0.7 : 1}}>
            {busy ? "正在提交…" : (mode === "login" ? "登录" : "创建账号")}
          </button>
          <div className="pl-auth-foot">
            <span>首个注册用户会成为管理员。</span>
            <a href="#"
              onClick={(e) => {
                e.preventDefault();
                // 主反馈：内联 notice（Login 页没有 PlatformShell 注入的 toast，
                // 之前直接 optional-chain 全局 toast，匿名用户点了完全没反应）
                setNotice("请联系管理员重置密码（暂未提供自助找回流程）");
                // 次要：如果恰好在 PlatformShell 里也跑了一遍 AuthPage，全局 toast 也带上
                window.__apiToast?.("请联系管理员重置密码", { kind: "info", duration: 2400 });
              }}
              style={{borderBottom: 0}}>忘记密码</a>
          </div>
        </form>
      </div>
    </div>
  );
}

export { AuthPage };
