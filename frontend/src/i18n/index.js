/* i18n 初始化 — i18next + react-i18next
   语言优先级: localStorage["pref.ui_language"] > 浏览器检测 > zh-CN
   changeLanguage(lng) 供 settings.jsx 的 interfaceLang onChange 调用。 */

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import zhCN from './locales/zh-CN.json';
import en from './locales/en.json';

const STORAGE_KEY = 'pref.ui_language';

// 读 prefs 里存的语言(settings.jsx 的 save("ui_language", v) 写的 key)
function getStoredLang() {
  try {
    return localStorage.getItem(STORAGE_KEY) || undefined;
  } catch (_) {
    return undefined;
  }
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      'zh-CN': { translation: zhCN },
      'zh-TW': { translation: zhCN }, // 暂时复用简体;后续补繁体 locale
      'en':    { translation: en },
    },
    lng: getStoredLang(),           // prefs 优先;未设则走 LanguageDetector
    fallbackLng: 'zh-CN',
    interpolation: { escapeValue: false },
    // 检测顺序: localStorage key → navigator.language
    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: STORAGE_KEY,
      caches: [],  // 不让 detector 自己写 localStorage;由 settings 的 save() 管
    },
  });

/** 切换语言并同步写 localStorage(prefs save 也会写后端,这里只保证本地立即生效) */
export function changeLanguage(lng) {
  try { localStorage.setItem(STORAGE_KEY, lng); } catch (_) {}
  return i18n.changeLanguage(lng);
}

export default i18n;
