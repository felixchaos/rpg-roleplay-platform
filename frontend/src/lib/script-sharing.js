// script-sharing.js — 剧本「引用模式」的前端单一真相源(web 剧本详情 / 剧本列表徽标 / 手机分享页共用)。
//
// 后端 scripts.sharing_mode 只有三种值(见 rpg/platform_app/api/script_edit/sharing.py):
//   private          —— 普通剧本,读自己的设定;
//   pinned-snapshot  —— 引用另一个剧本,记录了一个版本号(GM 检索目前仍按目标剧本的最新内容读);
//   floating-latest  —— 引用另一个剧本,跟随它的最新内容。
// 「公开」不是 sharing_mode,是 is_public(发布到剧本库,走 /visibility)。以前的选择器把「公开」
// 当成第四种模式发给 /pin,后端必回 400;引用模式又把 target_script_id 填成剧本自己,保存成功
// 但什么都没变。commit id 是整数,旧代码对它调 .slice 会直接抛错。

export const REFERENCE_MODES = ['pinned-snapshot', 'floating-latest'];

export function isReferenceMode(mode) {
  return REFERENCE_MODES.includes(String(mode || ''));
}

// 引用状态(只在真的引用了另一个剧本时返回对象,否则 null)。
// 旧选择器曾把 target_script_id 写成剧本自己,线上可能留着「引用自己」的行:KB 读取重定向指回自己,
// 等于没引用 —— 不当引用展示,免得出现「本剧本读取剧本 #自己」这种误导文案。
export function referenceInfo(script) {
  if (!script || !isReferenceMode(script.sharing_mode)) return null;
  const target = script.current_pin_script_id;
  if (target == null || target === '') return null;
  if (script.id != null && String(target) === String(script.id)) return null;
  const commit = script.current_pin_commit_id;
  return {
    mode: script.sharing_mode,
    targetId: String(target),
    commitId: commit == null || commit === '' ? '' : String(commit),
  };
}
