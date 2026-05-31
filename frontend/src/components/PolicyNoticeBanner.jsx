/**
 * PolicyNoticeBanner.jsx — DOC-02 / AUP-03 政策变更提示横幅
 *
 * 自行拉取 /api/policy/notices，每条 pending 通知显示一行横幅：
 *   - 政策名称 / 新版本 / 生效倒计时 / "阅读详情" 链接
 * dismiss 后将 notice id+version 存 localStorage，同版本不再弹。
 *
 * 独立组件，不接入 platform-app.jsx（由后续 T2 sonnet 或人工合并）。
 *
 * 用法（后续接入时）:
 *   import PolicyNoticeBanner from './components/PolicyNoticeBanner';
 *   // 在 App 根或布局层放置即可，无需 props
 *   <PolicyNoticeBanner />
 */
import React, { useEffect, useState } from 'react';

const LANDING_BASE = 'https://play.stellatrix.icu/legal';
const STORAGE_KEY = 'policy_notice_dismissed';

const POLICY_NAMES = {
  'zh-CN': {
    'privacy-policy':          '隐私政策',
    'terms-of-service':        '服务条款',
    'acceptable-use-policy':   '可接受使用政策',
    'cookie-policy':           'Cookie 政策',
    'dmca-policy':             'DMCA 版权政策',
    'adult-content-disclaimer':'成人内容免责声明',
  },
  en: {
    'privacy-policy':          'Privacy Policy',
    'terms-of-service':        'Terms of Service',
    'acceptable-use-policy':   'Acceptable Use Policy',
    'cookie-policy':           'Cookie Policy',
    'dmca-policy':             'DMCA Policy',
    'adult-content-disclaimer':'Adult Content Disclaimer',
  },
};

function getLang() {
  const lang = (navigator.language || 'zh-CN').toLowerCase();
  return lang.startsWith('zh') ? 'zh-CN' : 'en';
}

function policyName(slug, lang) {
  return (POLICY_NAMES[lang] || POLICY_NAMES['zh-CN'])[slug] || slug;
}

function getDismissed() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
  } catch {
    return {};
  }
}

function setDismissed(id, version) {
  const map = getDismissed();
  map[id] = version;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch { /* storage full, ignore */ }
}

function isDismissed(notice) {
  const map = getDismissed();
  return map[notice.id] === notice.new_version;
}

function formatCountdown(effectiveAt, lang) {
  const now = Date.now();
  const target = new Date(effectiveAt).getTime();
  const diffMs = target - now;
  if (diffMs <= 0) {
    return lang === 'zh-CN' ? '即将生效' : 'Effective soon';
  }
  const days = Math.ceil(diffMs / 86400000);
  return lang === 'zh-CN' ? `${days} 天后生效` : `${days} day(s) until effective`;
}

const I18N = {
  'zh-CN': {
    prefix: '政策更新通知：',
    suffix: '将更新至',
    countdown: (at) => formatCountdown(at, 'zh-CN'),
    details: '阅读详情',
    dismiss: '不再提示',
  },
  en: {
    prefix: 'Policy update: ',
    suffix: 'will be updated to',
    countdown: (at) => formatCountdown(at, 'en'),
    details: 'Read details',
    dismiss: 'Dismiss',
  },
};

const styles = {
  container: {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    zIndex: 9999,
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
  },
  banner: {
    background: '#1a6fb5',
    color: '#fff',
    padding: '8px 16px',
    fontSize: '14px',
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    flexWrap: 'wrap',
  },
  link: {
    color: '#cce4ff',
    textDecoration: 'underline',
    cursor: 'pointer',
    background: 'none',
    border: 'none',
    font: 'inherit',
    padding: 0,
  },
  dismiss: {
    marginLeft: 'auto',
    background: 'none',
    border: '1px solid rgba(255,255,255,0.5)',
    color: '#fff',
    borderRadius: '4px',
    padding: '2px 10px',
    cursor: 'pointer',
    fontSize: '12px',
  },
};

export default function PolicyNoticeBanner() {
  const [notices, setNotices] = useState([]);
  const [dismissed, setDismissedState] = useState(getDismissed());
  const lang = getLang();
  const t = I18N[lang] || I18N['zh-CN'];

  useEffect(() => {
    let cancelled = false;
    fetch('/api/policy/notices', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled && data && Array.isArray(data.notices)) {
          setNotices(data.notices);
        }
      })
      .catch(() => { /* not logged in or network error — silent */ });
    return () => { cancelled = true; };
  }, []);

  const visible = notices.filter((n) => !isDismissed(n));

  if (visible.length === 0) return null;

  function handleDismiss(notice) {
    setDismissed(notice.id, notice.new_version);
    setDismissedState(getDismissed());
  }

  return (
    <div style={styles.container} role="region" aria-label="Policy update notices">
      {visible.map((notice) => {
        const name = policyName(notice.slug, lang);
        const url = `${LANDING_BASE}/${notice.slug}.${lang}.html`;
        return (
          <div key={notice.id} style={styles.banner} role="alert">
            <span>
              {t.prefix}
              <strong>{name}</strong>
              {' '}
              {t.suffix}
              {' '}
              <strong>{notice.new_version}</strong>
              {' — '}
              {t.countdown(notice.effective_at)}
            </span>
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              style={styles.link}
            >
              {t.details}
            </a>
            <button
              style={styles.dismiss}
              onClick={() => handleDismiss(notice)}
              aria-label={`Dismiss notice for ${name}`}
            >
              {t.dismiss}
            </button>
          </div>
        );
      })}
    </div>
  );
}
