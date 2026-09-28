/**
 * saves-list-create.test.jsx — 存档页「新建存档」必须把弹窗结果完整发给 POST /api/saves。
 *
 * 旧实现手抄字段白名单,漏了 story_intent / player_origin / identity_known:玩家在弹窗里
 * 选的剧情走向、穿越方式、开局是否知道身份,从存档页新建时被静默丢掉(平台壳入口不丢)。
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor, act } from '@testing-library/react';

const captured = {};
vi.mock('../components/saves/NewGame.jsx', () => ({
  NewGameModal: (props) => { captured.onConfirm = props.onConfirm; return null; },
}));
vi.mock('../platform-app.jsx', () => ({
  ResizableSplit: ({ top, bottom }) => <div>{top}{bottom}</div>,
}));

import { SavesListView } from '../components/saves/SavesList.jsx';

const MODAL_RESULT = {
  title: '从存档页新建',
  script_id: 12,
  character_id: 3,
  character_kind: 'user_card',
  new_card: null,
  role_mode: 'existing',
  birthpoint: null,
  identity: { name: '林', role: '学徒', background: '', source: 'custom' },
  story_intent: '想走感情线',
  player_origin: 'body',
  identity_known: false,
};

beforeEach(() => {
  captured.onConfirm = null;
  window.api = {
    saves: {
      list: vi.fn().mockResolvedValue([]),
      create: vi.fn().mockResolvedValue({ ok: true, save: { id: 99, title: 'x', script_id: 12 } }),
    },
    scripts: { list: vi.fn().mockResolvedValue([{ id: 12, title: '剧本' }]) },
  };
  window.__openContinue = vi.fn();
});

describe('SavesListView 新建存档', () => {
  it('弹窗选的剧情走向 / 穿越方式 / 是否知道身份原样发给后端', async () => {
    render(<SavesListView />);
    await waitFor(() => expect(typeof captured.onConfirm).toBe('function'));
    await act(async () => { await captured.onConfirm(MODAL_RESULT); });
    expect(window.api.saves.create).toHaveBeenCalledTimes(1);
    const body = window.api.saves.create.mock.calls[0][0];
    expect(body.script_id).toBe(12);
    expect(body.story_intent).toBe('想走感情线');
    expect(body.player_origin).toBe('body');
    expect(body.identity_known).toBe(false);
    expect(body.identity).toEqual(MODAL_RESULT.identity);
  });
});
