/**
 * script-play-gate.test.jsx — 开局闸只拦「导入/重建还在跑」和「一章都没有」,不按 readiness.missing 拦。
 *
 * normalizeScript 改成整行透传后,readiness 进到了 web 剧本列表 / 详情和手机剧本页。
 * 四处开局闸(web scriptPlayBlockReason、web 新游戏弹窗、手机 isPlayBlocked、手机新游戏向导)
 * 原先会在 readiness.missing 含 chunks / anchors 时禁用开始 —— 但后端开局并不需要这两样:
 *   - fork 出来的剧本不复制 document_chunks(script_edit/fork.py 第 3-7 步只抄章节/世界书/设定/锚点/卡);
 *   - 空白剧本(create_blank_script)有 1 章,但没有切片也没有锚点;
 * 这两类都能正常开局,md-editor 的「从本章开始」也一直绕过这道闸。按 missing 拦就会把它们挡在门外,
 * 手机详情页连「继续游戏」都点不了。
 * 另外:开局闸只管「开新档」,已有存档的「继续」任何情况下都不拦。
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import i18n from '../i18n/index.js';

import { normalizeScript } from '../data-loader.js';
import { scriptPlayBlockKind } from '../lib/script-play-gate.js';
import { scriptPlayBlockReason, playDropdownState } from '../components/scripts/shared.js';
import { newGameScriptBlockReason } from '../components/saves/NewGameModal.jsx';
import { isPlayBlocked } from '../mobile/scripts/helpers.js';
import { scriptBlockReason as mobileNewGameBlockReason } from '../mobile/new-game/helpers.js';
import { ScriptDetailView } from '../mobile/scripts/ScriptDetailView.jsx';

vi.mock('../mobile/icons.jsx', () => ({ Icon: () => null }));

const t = (k, o) => i18n.t(k, o);

// fork 出来的剧本:章节在,但 document_chunks 没复制,锚点也可能为空。
const FORK_ROW = {
  id: 21,
  owner_id: 1,
  title: '斗破(fork)',
  chapter_count: 40,
  word_count: 120000,
  forked_from_script_id: 7,
  readiness: {
    ok: false,
    missing: ['chunks', 'embeddings', 'anchors'],
    items: [],
  },
};
// 空白剧本:1 章,没切片没锚点没设定。
const BLANK_ROW = {
  id: 22,
  owner_id: 1,
  title: '新剧本',
  chapter_count: 1,
  readiness: { ok: false, missing: ['chunks', 'embeddings', 'canon', 'worldbook', 'anchors'], items: [] },
};
const IMPORTING_ROW = { id: 23, owner_id: 1, title: '导入中', chapter_count: 3, import_status: 'running' };
const EMPTY_ROW = { id: 24, owner_id: 1, title: '空', chapter_count: 0, readiness: { ok: false, missing: ['chunks'] } };

const surfaces = [
  ['web 剧本列表/详情', (s) => scriptPlayBlockReason(s, t)],
  ['web 新游戏弹窗', (s) => newGameScriptBlockReason(s, t)],
  ['手机剧本页', (s) => isPlayBlocked(s)],
  ['手机新游戏向导', (s) => mobileNewGameBlockReason(s)],
];

describe('开局闸不按 readiness.missing 拦', () => {
  for (const [name, reason] of surfaces) {
    it(`${name}:fork 剧本过 normalizeScript 后可以开局`, () => {
      expect(reason(normalizeScript(FORK_ROW))).toBe('');
      expect(reason(FORK_ROW)).toBe('');
    });
    it(`${name}:空白剧本可以开局`, () => {
      expect(reason(normalizeScript(BLANK_ROW))).toBe('');
    });
    it(`${name}:导入还在跑、或一章都没有,仍然拦`, () => {
      expect(reason(normalizeScript(IMPORTING_ROW))).not.toBe('');
      expect(reason(normalizeScript(EMPTY_ROW))).not.toBe('');
    });
  }

  it('单一判定:只返回 importing / no_chapters / 空', () => {
    expect(scriptPlayBlockKind(FORK_ROW)).toBe('');
    expect(scriptPlayBlockKind(BLANK_ROW)).toBe('');
    expect(scriptPlayBlockKind(IMPORTING_ROW)).toBe('importing');
    expect(scriptPlayBlockKind({ ...IMPORTING_ROW, import_status: 'done' })).toBe('');
    expect(scriptPlayBlockKind(EMPTY_ROW)).toBe('no_chapters');
    expect(scriptPlayBlockKind(null)).toBe('');
  });
});

describe('开局闸只管开新档,不挡继续已有存档', () => {
  it('web 开始下拉:有存档时下拉可用,只禁用「开新游戏」', () => {
    const block = scriptPlayBlockReason(IMPORTING_ROW, t);
    const st = playDropdownState({ saves: [{ id: 5, title: '存档一' }], block, t });
    expect(st.disabled).toBe(false);
    const cont = st.items.find((x) => Array.isArray(x.items));
    expect(cont.items.map((x) => x.id)).toEqual(['continue:5']);
    const neu = st.items.find((x) => x.id === 'new');
    expect(neu.disabled).toBe(true);
    expect(neu.disabledReason).toBe(block);
  });

  it('web 开始下拉:没存档又被拦时整个下拉禁用', () => {
    const block = scriptPlayBlockReason(IMPORTING_ROW, t);
    const st = playDropdownState({ saves: [], block, t });
    expect(st.disabled).toBe(true);
  });

  it('web 开始下拉:没被拦时「开新游戏」可用', () => {
    const st = playDropdownState({ saves: [], block: '', t });
    expect(st.disabled).toBe(false);
    expect(st.items.find((x) => x.id === 'new').disabled).toBeFalsy();
  });

  it('手机详情:导入中但已有存档,「继续游戏」按钮可点并打开存档', () => {
    const openGame = vi.fn();
    const toast = vi.fn();
    const script = normalizeScript(IMPORTING_ROW);
    render(
      <ScriptDetailView
        script={script}
        saves={[{ id: 5, script_id: 23, title: '存档一' }]}
        embedStatus={{}}
        currentUserId={1}
        onBack={() => {}}
        onRefresh={() => {}}
        nav={{ openGame, toast, push: vi.fn() }}
      />,
    );
    const btn = screen.getByText(i18n.t('mobile.scripts.detail.continue_game', { count: 1 })).closest('button');
    expect(btn.disabled).toBe(false);
    btn.click();
    expect(openGame).toHaveBeenCalledWith(expect.objectContaining({ id: 5 }));
    expect(toast).not.toHaveBeenCalled();
    // 「新建存档」仍然被拦
    screen.getByText(i18n.t('mobile.scripts.detail.new_save')).closest('button').click();
    expect(toast).toHaveBeenCalled();
  });

  it('手机详情:导入中且没有存档,开始按钮禁用', () => {
    const script = normalizeScript(IMPORTING_ROW);
    render(
      <ScriptDetailView
        script={script}
        saves={[]}
        embedStatus={{}}
        currentUserId={1}
        onBack={() => {}}
        onRefresh={() => {}}
        nav={{ openGame: vi.fn(), toast: vi.fn(), push: vi.fn() }}
      />,
    );
    const btn = screen.getByText(i18n.t('mobile.scripts.detail.play_blocked')).closest('button');
    expect(btn.disabled).toBe(true);
  });
});
