// EditorPlaytest.jsx — 「写完即试玩」自包含组件。
//
// 剧本编辑器(pages/md-editor.jsx)内一键从指定章节开一个测试存档并跳转游戏台。
// 铁律:不修改 md-editor.jsx 本体(主循环统一接线),本文件只导出一个 hook,
// md-editor.jsx 在章节标签激活时的顶栏/状态栏里调用它即可。
//
// 流程:
//   1. window.__confirm 二次确认(说明会创建一个新的测试存档「试玩·第N章」)。
//   2. 建档 POST /api/saves(script_id + birthpoint={chapter_min,chapter_max,phase_label,
//      story_time_label}),复用全局 window.api.saves.create —— 与「新建游戏」向导
//      (pages/saves.jsx)完全同一后端契约(见 rpg/platform_app/workspace.py:160-162
//      birthpoint 形态 + workspace.py:181-182 复核闸)。
//   3. 若后端因剧本未过 KB 复核拒绝(workspace.py:181 抛 ValueError,saves.py:244-251
//      包成 {ok:false, error, needs_review:true, script_id}，HTTP 400)→ 二次确认后
//      调用 POST /api/scripts/{id}/mark-reviewed(scripts.py:1700-1714，返回
//      {ok:true, review_status:'reviewed'})→ 标记成功后自动重试一次建档。
//      该端点没有 window.api 封装(仓库里 pages/saves.jsx:2176 / pages/script-review.jsx:24
//      也是走裸 fetch，此处保持同一现有模式，不新增 api-client.js 改动)。
//   4. 建档成功后 activate(window.api.saves.activate)+ window.open 新标签跳游戏台
//      入口静态文件 Game Console.html（与 platform-app.jsx:4370-4430 的
//      window.__openContinue 同一形态：无 ?save=ID 查询参数，游戏台靠服务端已激活的
//      runtime 反查当前存档）。
//   5. 全程 window.__apiToast 反馈成功/失败，busy 状态防止重入连点。
//
// 有意不复用 window.__createAndEnterSave（platform-app.jsx:4437）整体，因为那条全局流程
// 不区分「未复核」错误、也不做二次确认+自动重试；这里需要插入 mark-reviewed 分支，
// 所以手写等价的 create → (mark-reviewed retry) → activate → open 三段，两处保持同一
// 请求/响应契约，任何一处后端契约变化都需要同步检查。

import React from 'react';
import { useTranslation } from 'react-i18next';

const { useState, useCallback, useRef } = React;

function apiBase() {
  return (typeof window !== 'undefined' && window.__API_BASE) || '';
}

// mark-reviewed 没有 window.api 封装(仓库既有模式,见 pages/saves.jsx / script-review.jsx),
// 这里保持同一裸 fetch 写法,不新增 api-client.js 改动。
async function markScriptReviewed(scriptId) {
  const res = await fetch(`${apiBase()}/api/scripts/${scriptId}/mark-reviewed`, {
    method: 'POST',
    credentials: 'include',
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    throw new Error((data && (data.error || data.detail)) || `mark-reviewed 失败 (HTTP ${res.status})`);
  }
  return data;
}

// 建档请求是否是「剧本未过 KB 复核」这一特定门禁(workspace.py:181-182 抛出、
// saves.py:244-251 包装)。优先信structured 的 needs_review 字段(saves.py:249 才会附加),
// 缺省时兜底用文案子串匹配(与 pages/saves.jsx:2164 同一正则,防止后端偶发遗漏该字段)。
function isReviewGateError(err) {
  if (!err) return false;
  if (err.payload && err.payload.needs_review) return true;
  const msg = String((err && err.message) || '');
  return /KB 复核|review_status|尚未复核|尚未通过/.test(msg);
}

/**
 * usePlaytest(scriptId) → { playtest(chapterIndex, chapterTitle), busy }
 *
 * chapterIndex: number  — 章节序号(md-editor.jsx 里 tab.id,对应 chapter_min/chapter_max)
 * chapterTitle: string  — 章节展示标题(如 tab.label,已含「第N章」前缀),仅用于存档命名 + 提示文案
 */
export function usePlaytest(scriptId) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  // 防止 confirm 弹窗等待期间的重复点击(busy 已覆盖大部分场景,inFlightRef 兜底并发调用)
  const inFlightRef = useRef(false);

  const playtest = useCallback(async (chapterIndex, chapterTitle) => {
    if (inFlightRef.current) return;
    if (!scriptId) {
      window.__apiToast?.(t('md_editor.playtest.no_script', { defaultValue: '请先选择剧本' }), { kind: 'warning' });
      return;
    }
    if (chapterIndex == null) {
      window.__apiToast?.(t('md_editor.playtest.no_chapter', { defaultValue: '请先打开一个章节' }), { kind: 'warning' });
      return;
    }

    const saveTitle = t('md_editor.playtest.save_title', { n: chapterIndex, defaultValue: '试玩·第{{n}}章' });
    const confirmMsg = t('md_editor.playtest.confirm_msg', {
      n: chapterIndex,
      title: chapterTitle || '',
      defaultValue: '将从「{{title}}」创建一个新的测试存档「{{n}}」并在新标签页打开游戏台，用于快速验收本章效果。是否继续？',
    });
    const ok = await window.__confirm?.({
      title: t('md_editor.playtest.confirm_title', { defaultValue: '试玩本章？' }),
      message: confirmMsg,
    });
    if (!ok) return;

    inFlightRef.current = true;
    setBusy(true);
    try {
      await runPlaytest({ scriptId, chapterIndex, saveTitle, t, allowMarkReviewedRetry: true });
    } finally {
      inFlightRef.current = false;
      setBusy(false);
    }
  }, [scriptId, t]);

  return { playtest, busy };
}

async function runPlaytest({ scriptId, chapterIndex, saveTitle, t, allowMarkReviewedRetry }) {
  let created;
  try {
    created = await window.api.saves.create({
      title: saveTitle,
      script_id: parseInt(scriptId, 10),
      birthpoint: {
        chapter_min: chapterIndex,
        chapter_max: chapterIndex,
      },
    });
    if (created && created.ok === false) {
      throw new Error(created.error || created.detail || t('md_editor.playtest.create_fail', { defaultValue: '建档失败' }));
    }
  } catch (e) {
    if (allowMarkReviewedRetry && isReviewGateError(e)) {
      const wantsMark = await window.__confirm?.({
        title: t('md_editor.playtest.review_gate_title', { defaultValue: '剧本尚未通过 KB 复核' }),
        message: t('md_editor.playtest.review_gate_msg', {
          defaultValue: '该剧本还没有标记为「已复核」，无法开局。是否现在标记为已复核并重试试玩？(请确认已在「KB 核查」里检查过实体/时间线/世界观)',
        }),
        danger: false,
      });
      if (!wantsMark) {
        window.__apiToast?.(t('md_editor.playtest.review_gate_declined', { defaultValue: '已取消:剧本未复核' }), { kind: 'warning' });
        return;
      }
      try {
        await markScriptReviewed(scriptId);
      } catch (markErr) {
        window.__apiToast?.(t('md_editor.playtest.mark_reviewed_fail', { defaultValue: '标记已复核失败' }), {
          kind: 'danger', detail: markErr?.message,
        });
        return;
      }
      window.__apiToast?.(t('md_editor.playtest.mark_reviewed_ok', { defaultValue: '已标记为已复核，正在重试建档…' }), { kind: 'ok' });
      // 只重试一次,避免 mark-reviewed 后仍失败(例如另一个理由)时无限递归。
      await runPlaytest({ scriptId, chapterIndex, saveTitle, t, allowMarkReviewedRetry: false });
      return;
    }
    window.__apiToast?.(t('md_editor.playtest.create_fail', { defaultValue: '建档失败' }), { kind: 'danger', detail: e?.message });
    return;
  }

  const save = created && (created.save || created);
  if (!save || !save.id) {
    window.__apiToast?.(t('md_editor.playtest.create_fail', { defaultValue: '建档失败' }), {
      kind: 'danger', detail: t('md_editor.playtest.no_save_id', { defaultValue: '响应缺少存档 id' }),
    });
    return;
  }

  try { window.dispatchEvent(new CustomEvent('rpg-saves-updated')); } catch (_) {}

  // 用户手势链路已经在 __confirm 之后中断过一次(await),这里再次 window.open 仍处在
  // 同一次点击触发的 async 调用栈延续内,现代浏览器(Chrome/Safari/Firefox)对弹窗拦截的判定
  // 是「是否存在用户激活(user activation)标志」而非严格同步调用,该标志在最近一次点击后
  // 有若干秒的有效期,足够覆盖 confirm + create 两次 await,与 platform-app.jsx:4388 同一假设。
  const gameWin = window.open('about:blank', '_blank');

  try {
    await window.api.saves.activate(save.id);
  } catch (e) {
    window.__apiToast?.(t('md_editor.playtest.activate_fail', { defaultValue: '激活试玩存档失败' }), { kind: 'danger', detail: e?.message });
    try { gameWin && gameWin.close && gameWin.close(); } catch (_) {}
    return;
  }

  const gameUrl = new URL('Game Console.html', window.location.href).href;
  if (gameWin) gameWin.location.href = gameUrl;
  else window.open(gameUrl, '_blank');

  window.__apiToast?.(t('md_editor.playtest.started', { n: chapterIndex, defaultValue: '试玩存档已创建，正在新标签页打开游戏台' }), { kind: 'ok' });
}

export default usePlaytest;
