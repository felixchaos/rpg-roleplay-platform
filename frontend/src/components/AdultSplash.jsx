/**
 * AdultSplash.jsx — AGE-02 首次访问 / 版本升级后强制 18+ 确认弹窗
 *
 * Props:
 *   splashVersion  string   当前 splash 版本常量，需与后端 SPLASH_CURRENT_VERSION 一致
 *   onAcked        () => void  ack 成功后的回调，父组件撤销覆盖层
 */
import React, { useState } from 'react';

const LEGAL_BASE = 'https://play.stellatrix.icu/legal/adult-content-disclaimer';

function getLang() {
  const lang = (navigator.language || 'zh-CN').toLowerCase();
  return lang.startsWith('zh') ? 'zh-CN' : 'en';
}

const I18N = {
  'zh-CN': {
    title: '成人内容声明 / Adult Content Disclaimer',
    body: '本服务面向 18 周岁及以上用户。平台包含成人主题文学创作内容，需确认年龄方可继续使用。',
    legalLink: '查阅完整成人内容免责声明',
    confirm: '我已年满 18 周岁，继续',
    leave: '未满 18 周岁，离开',
    loading: '正在确认…',
  },
  'en': {
    title: 'Adult Content Disclaimer',
    body: 'This service is intended for users aged 18 and above. The platform contains adult-themed literary content. You must confirm your age to continue.',
    legalLink: 'Read the full adult content disclaimer',
    confirm: 'I am 18 or older — Continue',
    leave: 'I am under 18 — Leave',
    loading: 'Confirming…',
  },
};

export default function AdultSplash({ splashVersion, onAcked }) {
  const lang = getLang();
  const t = I18N[lang] || I18N['zh-CN'];
  const legalUrl = `${LEGAL_BASE}.${lang}.html`;

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleConfirm = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch('/api/me/splash/ack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ splash_version: splashVersion }),
        credentials: 'same-origin',
      });
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${resp.status}`);
      }
      onAcked && onAcked();
    } catch (e) {
      setError(e.message || '网络错误，请重试');
      setLoading(false);
    }
  };

  const handleLeave = () => {
    try { window.location.replace('about:blank'); } catch (_) { window.close(); }
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9999,
        background: 'rgba(10, 8, 6, 0.92)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '16px',
      }}
    >
      <div
        style={{
          width: 'min(480px, 94vw)',
          background: 'var(--panel, #211f1d)',
          border: '1px solid var(--line, #3a3330)',
          borderRadius: '10px',
          padding: '32px 28px 28px',
          boxShadow: '0 8px 40px rgba(0,0,0,0.7)',
          color: 'var(--text, #e6ddd5)',
        }}
      >
        {/* Title */}
        <h2
          style={{
            fontFamily: 'var(--font-serif, Georgia, serif)',
            fontSize: 17,
            fontWeight: 600,
            marginBottom: 16,
            lineHeight: 1.4,
            letterSpacing: '0.02em',
          }}
        >
          {t.title}
        </h2>

        {/* Body */}
        <p style={{ fontSize: 14, lineHeight: 1.7, color: 'var(--text-quiet, #b0a89e)', marginBottom: 16 }}>
          {t.body}
        </p>

        {/* Legal link */}
        <p style={{ marginBottom: 24 }}>
          <a
            href={legalUrl}
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontSize: 13, color: 'var(--accent, #d4a45e)', textDecoration: 'underline' }}
          >
            {t.legalLink}
          </a>
        </p>

        {/* Error */}
        {error && (
          <p style={{ fontSize: 12, color: 'var(--danger, #e07070)', marginBottom: 12 }}>
            {error}
          </p>
        )}

        {/* Buttons */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <button
            onClick={handleConfirm}
            disabled={loading}
            style={{
              padding: '11px 20px',
              borderRadius: 6,
              border: '1px solid var(--accent, #d4a45e)',
              background: 'var(--accent, #d4a45e)',
              color: '#1a1510',
              fontSize: 14,
              fontWeight: 600,
              cursor: loading ? 'not-allowed' : 'pointer',
              opacity: loading ? 0.7 : 1,
              transition: 'opacity 0.15s',
            }}
          >
            {loading ? t.loading : t.confirm}
          </button>
          <button
            onClick={handleLeave}
            disabled={loading}
            style={{
              padding: '10px 20px',
              borderRadius: 6,
              border: '1px solid var(--line, #3a3330)',
              background: 'transparent',
              color: 'var(--muted, #888)',
              fontSize: 13,
              cursor: 'pointer',
            }}
          >
            {t.leave}
          </button>
        </div>
      </div>
    </div>
  );
}
