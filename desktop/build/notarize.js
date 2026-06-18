'use strict';
// notarize.js —— electron-builder afterSign hook:对已签名的 macOS .app 做公证。
// 用 App Store Connect API Key(.p8)法,避开 CI 里 Apple ID 2FA 弹窗(研究强调)。
// 非 macOS、或缺少凭据时跳过(本地 dist:dir 调试不阻塞)。
//
// 所需环境变量(CI Secret):
//   APPLE_API_KEY     指向 .p8 文件路径(AuthKey_XXXX.p8)
//   APPLE_API_KEY_ID  Key ID
//   APPLE_API_ISSUER  Issuer ID
// 备选(本机已 store-credentials 时):APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID

exports.default = async function notarizing(context) {
  const { electronPlatformName, appOutDir } = context;
  if (electronPlatformName !== 'darwin') return;

  const hasApiKey = process.env.APPLE_API_KEY && process.env.APPLE_API_KEY_ID && process.env.APPLE_API_ISSUER;
  const hasAppleId = process.env.APPLE_ID && process.env.APPLE_APP_SPECIFIC_PASSWORD && process.env.APPLE_TEAM_ID;
  if (!hasApiKey && !hasAppleId) {
    console.log('[notarize] 跳过:未提供公证凭据(APPLE_API_KEY… 或 APPLE_ID…)');
    return;
  }

  let notarize;
  try { ({ notarize } = require('@electron/notarize')); }
  catch (_) { console.log('[notarize] 跳过:未安装 @electron/notarize'); return; }

  const appName = context.packager.appInfo.productFilename;
  const appPath = `${appOutDir}/${appName}.app`;

  console.log(`[notarize] 提交公证(notarytool): ${appPath}`);
  const opts = { appPath, tool: 'notarytool' };
  if (hasApiKey) {
    opts.appleApiKey = process.env.APPLE_API_KEY;
    opts.appleApiKeyId = process.env.APPLE_API_KEY_ID;
    opts.appleApiIssuer = process.env.APPLE_API_ISSUER;
  } else {
    opts.appleId = process.env.APPLE_ID;
    opts.appleIdPassword = process.env.APPLE_APP_SPECIFIC_PASSWORD;
    opts.teamId = process.env.APPLE_TEAM_ID;
  }
  await notarize(opts);
  console.log('[notarize] 完成(electron-builder 随后对 .app 与 DMG 执行 staple)');
};
