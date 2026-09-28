/**
 * save-create-payload.test.js — 「新建存档」弹窗填的东西,POST /api/saves 必须原样带上。
 *
 * NewGameModal 收集:剧本 / 角色卡 / 出生点 / 身份卡 / 剧情走向(story_intent)/ 穿越方式
 * (player_origin)/ 开局是否知道身份(identity_known)。平台壳的 __createAndEnterSave 全带,
 * 但存档页(components/saves/SavesList.jsx)的 onCreate 手抄了一份白名单,漏了后三项 ——
 * 从存档页新建的存档,玩家选的「穿越方式」「剧情走向」被静默丢掉,开局一律按默认。
 * 两处改用同一个 buildCreateSavePayload,这里锁它不丢字段。
 */
import { describe, it, expect } from 'vitest';
import { buildCreateSavePayload } from '../lib/save-create-payload.js';

const FULL = {
  title: '我的存档',
  script_id: 12,
  character_id: 3,
  character_kind: 'user_card',
  npc_id: null,
  new_card: null,
  birthpoint: { chapter_min: 5, chapter_max: 5 },
  identity: { name: '林', role: '学徒', background: '', source: 'custom' },
  story_intent: '想走感情线',
  player_origin: 'body',
  identity_known: false,
  role_mode: 'existing',
};

describe('buildCreateSavePayload', () => {
  it('弹窗里的每一项都带给后端', () => {
    const p = buildCreateSavePayload(FULL);
    expect(p.script_id).toBe(12);
    expect(p.story_intent).toBe('想走感情线');
    expect(p.player_origin).toBe('body');
    expect(p.identity_known).toBe(false);          // false 是合法选择,不能被 || null 吞掉
    expect(p.birthpoint).toEqual({ chapter_min: 5, chapter_max: 5 });
    expect(p.identity).toEqual(FULL.identity);
    expect(p.character_id).toBe(3);
    expect(p.character_kind).toBe('user_card');
  });

  it('缺省值:没填的项给 null,标题按调用方给的兜底', () => {
    const p = buildCreateSavePayload({ script_id: 7 }, { defaultTitle: '新存档' });
    expect(p.title).toBe('新存档');
    expect(p.story_intent).toBeNull();
    expect(p.player_origin).toBeNull();
    expect(p.identity_known).toBeNull();
    expect(p.birthpoint).toBeNull();
  });

  it('script_id 缺失时用调用方的兜底剧本', () => {
    expect(buildCreateSavePayload({}, { fallbackScriptId: 9 }).script_id).toBe(9);
  });
});
