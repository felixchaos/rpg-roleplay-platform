/**
 * tavern-card-import.test.js — 酒馆卡导入共享执行段(lib/tavern-card-import.js)。
 *
 * 背景:群反馈(白玖)要求剧本「NPC 角色卡」也能导入酒馆卡 / 粘贴 JSON。新入口与用户
 * 卡库共用同一个弹窗,所以「多选的文件怎么循环、ok:false 怎么算失败、同名覆盖怎么
 * 统计」必须只有一份实现 —— 否则同一个弹窗在两个页面表现不同。
 *
 * 同时锁住一个老 bug:弹窗允许一次选 8 个文件,但 payload 只带预览的第一张,其余被
 * 静默丢弃(用户选 5 张只进 1 张,没有任何提示)。
 */
import { describe, it, expect, vi } from 'vitest';
import { cardImportFiles, runCardImport } from '../lib/tavern-card-import.js';

const f = (name) => ({ name });

describe('cardImportFiles — payload 归一', () => {
  it('优先用 files[](弹窗多选)', () => {
    expect(cardImportFiles({ files: [f('a.png'), f('b.json')], file: f('a.png') }))
      .toHaveLength(2);
  });
  it('兼容只带单个 file 的旧形态', () => {
    expect(cardImportFiles({ file: f('a.png') })).toEqual([{ name: 'a.png' }]);
  });
  it('粘贴 JSON 形态没有文件', () => {
    expect(cardImportFiles({ json_string: '{}' })).toEqual([]);
  });
});

describe('runCardImport — 多文件逐张导入', () => {
  it('选几张就导几张(不再静默丢掉除第一张外的全部)', async () => {
    const importFile = vi.fn().mockResolvedValue({ ok: true });
    const r = await runCardImport(
      { type: 'card', file: f('a.png'), files: [f('a.png'), f('b.png'), f('c.json')] },
      { importFile, importJson: vi.fn() },
    );
    expect(importFile).toHaveBeenCalledTimes(3);
    expect(r.imported).toBe(3);
    expect(r.failures).toEqual([]);
  });

  it('单张失败不打断其余,失败带文件名', async () => {
    const importFile = vi.fn()
      .mockResolvedValueOnce({ ok: true })
      .mockRejectedValueOnce(new Error('解析失败'))
      .mockResolvedValueOnce({ ok: true });
    const r = await runCardImport(
      { files: [f('a.png'), f('bad.png'), f('c.png')] },
      { importFile, importJson: vi.fn() },
    );
    expect(r.imported).toBe(2);
    expect(r.failures).toEqual([{ name: 'bad.png', message: '解析失败' }]);
  });

  it('HTTP 200 但 {ok:false} 也算失败(后端统一信封)', async () => {
    const importFile = vi.fn().mockResolvedValue({ ok: false, error: '仅原作者可编辑该剧本' });
    const r = await runCardImport({ files: [f('a.png')] }, { importFile, importJson: vi.fn() });
    expect(r.imported).toBe(0);
    expect(r.failures[0].message).toBe('仅原作者可编辑该剧本');
  });

  it('统计同名覆盖张数(replaced)', async () => {
    const importFile = vi.fn()
      .mockResolvedValueOnce({ ok: true, replaced: true })
      .mockResolvedValueOnce({ ok: true, replaced: false });
    const r = await runCardImport({ files: [f('a.png'), f('b.png')] }, { importFile, importJson: vi.fn() });
    expect(r.imported).toBe(2);
    expect(r.replaced).toBe(1);
  });
});

describe('runCardImport — 粘贴 JSON', () => {
  it('走 importJson,并透传 ai_split', async () => {
    const importJson = vi.fn().mockResolvedValue({ ok: true });
    const r = await runCardImport({ json_string: '{"name":"夜莺"}', aiSplit: true },
      { importFile: vi.fn(), importJson });
    expect(importJson).toHaveBeenCalledWith({ json_string: '{"name":"夜莺"}', ai_split: true });
    expect(r.imported).toBe(1);
  });

  it('没文件也没 JSON → 什么都不调', async () => {
    const importFile = vi.fn(); const importJson = vi.fn();
    const r = await runCardImport({ type: 'card' }, { importFile, importJson });
    expect(importFile).not.toHaveBeenCalled();
    expect(importJson).not.toHaveBeenCalled();
    expect(r).toEqual({ imported: 0, replaced: 0, failures: [] });
  });
});
