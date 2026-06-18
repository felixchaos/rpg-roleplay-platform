'use strict';
// i18n.js —— 控制台轻量国际化(与网页端 i18next 同思路:flat key + zh-CN 兜底)。
// window.I18N: { t, applyI18n, setLang, getLang, LANGS }。语言存 desktop config.uiLanguage。
// 整体包进 IIFE:内部 STR/LANG/t 等不污染全局(避免与 panel.js 的 const t 重名冲突)。
(function () {

const STR = {
  'zh-CN': {
    'common.save': '保存', 'common.cancel': '取消', 'common.refresh': '刷新',
    'common.close': '关闭', 'common.confirm': '确认', 'common.copied': '已复制', 'common.saved': '已保存',
    // 导航
    'nav.overview': '概览', 'nav.logs': '日志', 'nav.backup': '备份 / 恢复', 'nav.lan': '局域网访问',
    'nav.config': '配置', 'nav.update': '更新', 'nav.feedback': '反馈', 'nav.about': '关于',
    'side.open_browser': '在浏览器打开 ↗',
    // 运行模式 / 概览
    'overview.run_mode': '运行模式', 'mode.online': '免部署', 'mode.local': '本地部署', 'mode.remember': '记住我的选择',
    'mode.local_hint': '本机离线 · NSFW 自主', 'mode.online_hint': '连云端账号',
    'overview.open_browser': '在浏览器中打开', 'overview.magic_link': '免登录魔法链接',
    'overview.open_hint': '用系统默认浏览器', 'overview.copy_lan': '复制局域网访问地址',
    'overview.qr_cap': '手机扫码访问', 'overview.lan_hint': '同网手机/平板扫码或访问',
    'overview.local_service': '本地服务', 'overview.start': '启动服务', 'overview.stop': '停止', 'overview.restart': '重启',
    'overview.status': '状态', 'overview.backend_port': '后端端口', 'overview.db_port': '数据库端口',
    'overview.start_failed': '启动失败', 'overview.retry': '重试', 'overview.open_logs': '打开日志目录', 'overview.copy_diag': '复制诊断',
    'overview.local_guide': '首次使用:点「启动服务」完成本地初始化(首次需建库,约 10-30 秒,请稍候)。',
    // 账户
    'account.cloud': '云端账户', 'account.local': '本地账户', 'account.login': '登录', 'account.logout': '登出',
    'account.edit': '编辑', 'account.reset_pw': '重置密码', 'account.not_logged_in': '未登录',
    'account.username_ph': '用户名或邮箱', 'account.password_ph': '密码', 'account.nickname_ph': '昵称',
    'account.new_pw_ph': '新密码(留空=清除,回环免登录)',
    'account.pw_note': '设密码后,局域网设备访问需登录;留空清除则本机回环免登录。',
    'account.pw_set': '已设密码', 'account.pw_none': '未设密码(回环免登录)',
    'account.start_first': '(启动服务后可用)', 'account.current': '当前账户',
    'account.login_failed': '登录失败', 'account.logged_in': '已登录',
    // 重启确认
    'restart.confirm': '检测到正在运行的任务({tasks})。重启会中断它们,确定强制重启?',
    'restart.settings_changed': '部分设置需重启服务才生效,现在重启?',
    // 配置
    'config.title': '配置', 'config.online_url': '云端地址', 'config.backend_port': '本地后端端口(0=自动)',
    'config.channel': '更新渠道', 'config.autostart': '本地模式下「打开」时自动启动服务',
    'config.advanced': '高级', 'config.params': '可配置参数', 'config.params_hint': '本地部署可调参数,改后保存;标「需重启」的会询问是否立即重启。',
    'config.extra_env': '额外环境变量 (每行 KEY=VALUE)', 'config.save': '保存配置', 'config.open_data': '打开数据目录',
    'config.language': '界面语言', 'config.lang_follow': '跟随系统',
    'config.wipe': '清除本地数据', 'config.needs_restart': '需重启',
    // 备份
    'backup.intro': '剧本 / 存档 / 角色卡 / 偏好,打包成单个 .zip。', 'backup.export': '备份(导出)',
    'backup.import': '恢复(导入)', 'backup.est_size': '预计大小', 'backup.with_chunks': '含对话切片(更大,更完整)',
    'backup.export_btn': '导出为 .zip', 'backup.pick_zip': '选择 .zip 文件…', 'backup.import_btn': '开始导入',
    'backup.auto': '自动备份', 'backup.enable': '启用', 'backup.every': '每', 'backup.hours': '小时',
    'backup.keep': '保留', 'backup.copies': '份', 'backup.dir': '备份目录', 'backup.choose': '选择…',
    'backup.now': '立即备份', 'backup.cloud_sync': '云端账户数据迁移',
    'backup.to_cloud': '把本地数据同步到云端账户', 'backup.from_cloud': '从云端账户导入到本地',
    'backup.cloud_need_login': '需先在「概览」登录云端账户。',
    // 局域网
    'lan.enable': '开启局域网访问', 'lan.url': '访问地址', 'lan.fw': '放行防火墙端口',
    // 更新
    'update.title': '应用更新', 'update.check': '检查更新', 'update.download': '下载更新', 'update.install': '重启并安装',
    'update.notes': '更新日志', 'update.checking': '检查中…', 'update.none': '已是最新版本',
    // 反馈
    'feedback.replies': '我的反馈 / 回执', 'feedback.submit': '提交反馈', 'feedback.again': '再提一条',
  },
  'en': {
    'common.save': 'Save', 'common.cancel': 'Cancel', 'common.refresh': 'Refresh',
    'common.close': 'Close', 'common.confirm': 'Confirm', 'common.copied': 'Copied', 'common.saved': 'Saved',
    'nav.overview': 'Overview', 'nav.logs': 'Logs', 'nav.backup': 'Backup', 'nav.lan': 'LAN access',
    'nav.config': 'Settings', 'nav.update': 'Update', 'nav.feedback': 'Feedback', 'nav.about': 'About',
    'side.open_browser': 'Open in browser ↗',
    'overview.run_mode': 'Run mode', 'mode.online': 'No setup', 'mode.local': 'Local', 'mode.remember': 'Remember my choice',
    'mode.local_hint': 'Offline on this machine · NSFW on you', 'mode.online_hint': 'Connect to cloud account',
    'overview.open_browser': 'Open in browser', 'overview.magic_link': 'Passwordless magic link',
    'overview.open_hint': 'Uses your default browser', 'overview.copy_lan': 'Copy LAN address',
    'overview.qr_cap': 'Scan to open on phone', 'overview.lan_hint': 'Scan / open from a device on the same network',
    'overview.local_service': 'Local service', 'overview.start': 'Start', 'overview.stop': 'Stop', 'overview.restart': 'Restart',
    'overview.status': 'Status', 'overview.backend_port': 'Backend port', 'overview.db_port': 'Database port',
    'overview.start_failed': 'Failed to start', 'overview.retry': 'Retry', 'overview.open_logs': 'Open logs folder', 'overview.copy_diag': 'Copy diagnostics',
    'overview.local_guide': 'First run: click "Start" to initialize locally (first time builds the DB, ~10-30s, please wait).',
    'account.cloud': 'Cloud account', 'account.local': 'Local account', 'account.login': 'Sign in', 'account.logout': 'Sign out',
    'account.edit': 'Edit', 'account.reset_pw': 'Reset password', 'account.not_logged_in': 'Not signed in',
    'account.username_ph': 'Username or email', 'account.password_ph': 'Password', 'account.nickname_ph': 'Display name',
    'account.new_pw_ph': 'New password (empty = clear, loopback no-login)',
    'account.pw_note': 'With a password set, LAN devices must sign in; clear it for loopback no-login.',
    'account.pw_set': 'password set', 'account.pw_none': 'no password (loopback no-login)',
    'account.start_first': '(available after the service starts)', 'account.current': 'Current account',
    'account.login_failed': 'Sign-in failed', 'account.logged_in': 'Signed in',
    'restart.confirm': 'A task is running ({tasks}). Restart will interrupt it. Force restart?',
    'restart.settings_changed': 'Some settings need a restart to take effect. Restart now?',
    'config.title': 'Settings', 'config.online_url': 'Cloud URL', 'config.backend_port': 'Local backend port (0=auto)',
    'config.channel': 'Update channel', 'config.autostart': 'Auto-start the service when "Open" in local mode',
    'config.advanced': 'Advanced', 'config.params': 'Configurable parameters', 'config.params_hint': 'Local-deploy tunables. Save after editing; ones marked "needs restart" will ask to restart.',
    'config.extra_env': 'Extra environment variables (one KEY=VALUE per line)', 'config.save': 'Save settings', 'config.open_data': 'Open data folder',
    'config.language': 'Language', 'config.lang_follow': 'Follow system',
    'config.wipe': 'Wipe local data', 'config.needs_restart': 'needs restart',
    'backup.intro': 'Scripts / saves / character cards / prefs into a single .zip.', 'backup.export': 'Backup (export)',
    'backup.import': 'Restore (import)', 'backup.est_size': 'Estimated size', 'backup.with_chunks': 'Include dialogue slices (bigger, complete)',
    'backup.export_btn': 'Export .zip', 'backup.pick_zip': 'Choose .zip file…', 'backup.import_btn': 'Start import',
    'backup.auto': 'Auto backup', 'backup.enable': 'Enable', 'backup.every': 'Every', 'backup.hours': 'hours',
    'backup.keep': 'Keep', 'backup.copies': 'copies', 'backup.dir': 'Backup folder', 'backup.choose': 'Choose…',
    'backup.now': 'Back up now', 'backup.cloud_sync': 'Cloud account data migration',
    'backup.to_cloud': 'Sync local data to cloud account', 'backup.from_cloud': 'Import from cloud account to local',
    'backup.cloud_need_login': 'Sign in to your cloud account on the Overview tab first.',
    'lan.enable': 'Enable LAN access', 'lan.url': 'Address', 'lan.fw': 'Open firewall port',
    'update.title': 'App update', 'update.check': 'Check for updates', 'update.download': 'Download', 'update.install': 'Restart & install',
    'update.notes': 'Release notes', 'update.checking': 'Checking…', 'update.none': 'You are up to date',
    'feedback.replies': 'My feedback / replies', 'feedback.submit': 'Submit feedback', 'feedback.again': 'Submit another',
  },
};

let LANG = 'zh-CN';

function _normLang(l) {
  l = (l || '').trim();
  if (!l) return '';
  if (l.toLowerCase().startsWith('en')) return 'en';
  return 'zh-CN';
}

function t(key) {
  return (STR[LANG] && STR[LANG][key]) || STR['zh-CN'][key] || key;
}

function setLang(l) {
  LANG = _normLang(l) || (_normLang(navigator.language) || 'zh-CN');
  applyI18n();
  return LANG;
}

function getLang() { return LANG; }

function applyI18n() {
  document.documentElement.lang = LANG;
  document.querySelectorAll('[data-i18n]').forEach((el) => { const v = t(el.dataset.i18n); if (v) el.textContent = v; });
  document.querySelectorAll('[data-i18n-ph]').forEach((el) => { const v = t(el.dataset.i18nPh); if (v) el.setAttribute('placeholder', v); });
  document.querySelectorAll('[data-i18n-title]').forEach((el) => { const v = t(el.dataset.i18nTitle); if (v) el.setAttribute('title', v); });
}

window.I18N = { t, applyI18n, setLang, getLang, LANGS: ['zh-CN', 'en'] };

})();
