/**
 * model-overlay-write.test.js — 设置 → 模型页「单个模型」的启停 / 删除该打哪个端点。
 *
 * 前科:v1.76.0「添加模型」按钮接了管理员专用的全局端点,普通用户撞 403,管理员则把私人
 * 模型写进所有人的目录。当时只修了「添加」和「可见性」,同一页的「启停开关」和「校验后删除」
 * 仍一律打 /api/models/model(/delete)(get_current_admin):
 *   · 普通用户:403 被 catch 吞掉,列表乐观翻转,刷新后又变回来 —— 开关 / 删除都是摆设;
 *   · 管理员:把自己同步来的私人模型写进全局目录。
 * 用户自己的模型(同步来的 / 手填的,synced === true)必须走按用户的端点;平台内置目录里的
 * 模型只有管理员能改,普通用户明确报错,不发注定 403 的请求。
 */
import { describe, it, expect, vi } from 'vitest';
import { setModelEnabled, removeModel, removeTargetsFor, isAdminOnlyModelEdit } from '../lib/model-overlay-write.js';

function fakeApi() {
  return {
    models: {
      meVisibility: vi.fn().mockResolvedValue({ ok: true }),
      meDeleteModel: vi.fn().mockResolvedValue({ ok: true }),
      upsertModel: vi.fn().mockResolvedValue({ ok: true }),
      deleteModel: vi.fn().mockResolvedValue({ ok: true }),
    },
  };
}

const own = { id: 'gpt-x', synced: true };
const builtin = { id: 'gpt-y' };

describe('setModelEnabled', () => {
  it('用户自己的模型 → 按用户的可见性端点(普通用户也能改)', async () => {
    const api = fakeApi();
    await setModelEnabled(api, { apiId: 'openai', model: own, enabled: false, isAdmin: false });
    expect(api.models.meVisibility).toHaveBeenCalledWith({ api_id: 'openai', model: 'gpt-x', visible: false });
    expect(api.models.upsertModel).not.toHaveBeenCalled();
  });

  it('管理员改自己的模型也不写全局目录', async () => {
    const api = fakeApi();
    await setModelEnabled(api, { apiId: 'openai', model: own, enabled: true, isAdmin: true });
    expect(api.models.meVisibility).toHaveBeenCalledTimes(1);
    expect(api.models.upsertModel).not.toHaveBeenCalled();
  });

  it('内置目录模型 + 管理员 → 全局端点', async () => {
    const api = fakeApi();
    await setModelEnabled(api, { apiId: 'openai', model: builtin, enabled: false, isAdmin: true });
    expect(api.models.upsertModel).toHaveBeenCalledWith({ api_id: 'openai', real_name: 'gpt-y', enabled: false });
  });

  it('内置目录模型 + 普通用户 → 不发请求,抛可识别的「仅管理员」错误', async () => {
    const api = fakeApi();
    let err = null;
    try { await setModelEnabled(api, { apiId: 'openai', model: builtin, enabled: false, isAdmin: false }); }
    catch (e) { err = e; }
    expect(isAdminOnlyModelEdit(err)).toBe(true);
    expect(api.models.upsertModel).not.toHaveBeenCalled();
    expect(api.models.meVisibility).not.toHaveBeenCalled();
  });
});

describe('removeModel', () => {
  it('用户自己的模型 → 按用户的删除端点', async () => {
    const api = fakeApi();
    await removeModel(api, { apiId: 'openai', model: own, isAdmin: false });
    expect(api.models.meDeleteModel).toHaveBeenCalledWith({ api_id: 'openai', real_name: 'gpt-x' });
    expect(api.models.deleteModel).not.toHaveBeenCalled();
  });

  it('内置目录模型 + 管理员 → 全局删除', async () => {
    const api = fakeApi();
    await removeModel(api, { apiId: 'openai', model: builtin, isAdmin: true });
    expect(api.models.deleteModel).toHaveBeenCalledWith({ api_id: 'openai', real_name: 'gpt-y' });
  });

  it('内置目录模型 + 普通用户 → 不发请求', async () => {
    const api = fakeApi();
    await expect(removeModel(api, { apiId: 'openai', model: builtin, isAdmin: false })).rejects.toSatisfy(isAdminOnlyModelEdit);
    expect(api.models.deleteModel).not.toHaveBeenCalled();
  });
});

describe('removeTargetsFor', () => {
  it('视图里有的 id 用视图里那条(带 synced 归属)', () => {
    const view = [{ id: 'gpt-x', synced: true }, { id: 'gpt-y' }];
    expect(removeTargetsFor(view, ['gpt-x'])).toEqual([{ id: 'gpt-x', synced: true }]);
  });

  it('视图外的 id 不丢,按内置目录模型处理(校验弹窗的下线模型来自全局目录)', () => {
    const view = [{ id: 'gpt-x', synced: true }];
    expect(removeTargetsFor(view, ['gpt-old', 'gpt-x'])).toEqual([
      { id: 'gpt-old', real_name: 'gpt-old', synced: false },
      { id: 'gpt-x', synced: true },
    ]);
  });

  it('视图为空 / 未加载也照样构造', () => {
    expect(removeTargetsFor(undefined, ['a'])).toEqual([{ id: 'a', real_name: 'a', synced: false }]);
  });
});

// 校验弹窗的删除按 diff 的对比基准路由(弹窗把 { base, localOnly } 传回)。
// 管理员的 diff 以全局目录为基准:local_only 是「目录有、远端没有」的条目。他自己的清单里恰好
// 有同名的手填模型(synced:true,常见于 /models 列不全、只能手填的供应商)时,以前按视图归属
// 删掉的是自己的手填模型,目录条目原样保留,下次校验还在。
describe('removeTargetsFor:按对比基准路由', () => {
  const view = [{ id: 'gpt-old', synced: true }, { id: 'gpt-x', synced: true }];

  it('base=catalog:local_only 里的 id 一律按内置目录条目处理(不看视图归属)', () => {
    expect(removeTargetsFor(view, ['gpt-old'], { base: 'catalog', localOnly: ['gpt-old'] })).toEqual([
      { id: 'gpt-old', real_name: 'gpt-old', synced: false },
    ]);
  });

  it('base=catalog:不在 local_only 里的(不可达的视图模型)仍按视图归属', () => {
    expect(removeTargetsFor(view, ['gpt-x'], { base: 'catalog', localOnly: ['gpt-old'] })).toEqual([
      { id: 'gpt-x', synced: true },
    ]);
  });

  it('base=user:按视图归属(自己的模型走按用户的删除)', () => {
    expect(removeTargetsFor(view, ['gpt-old'], { base: 'user', localOnly: ['gpt-old'] })).toEqual([
      { id: 'gpt-old', synced: true },
    ]);
  });
});
