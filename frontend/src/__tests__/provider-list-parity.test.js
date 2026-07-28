/**
 * provider-list-parity.test.js — 供应商列表有两份,必须对齐。
 *
 * 前科(2026-07-28,群反馈「火山的能用吗」→ v1.74.0 修完他说「刷新过了但没看到」):
 * 供应商其实有**两份互不知情的清单** ——
 *   · 后端 `rpg/config/model_catalog.json`(+ model_registry 的 DEFAULT/策展白名单):
 *     决定 provider 是否存在、base_url、能不能被 GM 解析到;
 *   · 前端 `components/settings/models-catalog.js` 的 `PROVIDERS`:**写死的数组**,
 *     决定「添加供应商」下拉里能不能选到。
 * v1.74.0 只在后端把 doubao 启用了,前端这份没加 → 用户刷新后依旧看不到,白改一轮。
 *
 * 本文件锁双向一致:后端启用的 provider 前端要能选到,前端给的 base_url 要和后端一致。
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { PROVIDERS_CONFIG as PROVIDERS } from '../components/settings/models-catalog.js';

const backend = JSON.parse(
  readFileSync(resolve(__dirname, '../../../rpg/config/model_catalog.json'), 'utf-8'),
);
const byId = Object.fromEntries((backend.apis || []).map((a) => [a.id, a]));

/** 前端用别的 id 表示、或刻意不进下拉的条目,逐个写明理由。
 *  注意:本文件比对的是 **JSON 种子** rpg/config/model_catalog.json —— 它与
 *  model_registry.DEFAULT_MODEL_CATALOG(代码内)不是同一份,后者还多几条(如
 *  google_ai_studio)。所以这里只豁免 JSON 种子里真实存在的 id。 */
const EXEMPT = {
  vertex_ai: '前端叫 AgentPlatform(SA 凭据),走 special: agent_platform',
};

const feById = Object.fromEntries(PROVIDERS.map((p) => [p.id, p]));

describe('供应商清单双向对齐', () => {
  it('后端 enabled 的 provider,前端下拉里必须能选到', () => {
    const missing = (backend.apis || [])
      .filter((a) => a.enabled && !EXEMPT[a.id] && !feById[a.id])
      .map((a) => a.id);
    expect(missing, `后端启用了但前端下拉没有(用户刷新也看不到): ${missing}`).toEqual([]);
  });

  it('前端给的 defaultBase 必须与后端 catalog 的 base_url 一致', () => {
    const mismatched = PROVIDERS
      .filter((p) => p.defaultBase && byId[p.id]?.base_url)
      .filter((p) => p.defaultBase !== byId[p.id].base_url)
      .map((p) => `${p.id}: 前端 ${p.defaultBase} ≠ 后端 ${byId[p.id].base_url}`);
    expect(mismatched).toEqual([]);
  });

  it('火山方舟在前端下拉里,且名字能按「火山」搜到', () => {
    const ark = feById.doubao;
    expect(ark, '前端 PROVIDERS 里没有 doubao —— 正是 v1.74.0 漏掉的那半边').toBeTruthy();
    expect(ark.name).toContain('火山');
    expect(ark.defaultBase).toBe('https://ark.cn-beijing.volces.com/api/v3');
  });

  it('豁免名单里的 id 必须真的存在于后端,别留过期豁免', () => {
    const stale = Object.keys(EXEMPT).filter((id) => !byId[id]);
    expect(stale, `豁免了不存在的 provider: ${stale}`).toEqual([]);
  });
});
