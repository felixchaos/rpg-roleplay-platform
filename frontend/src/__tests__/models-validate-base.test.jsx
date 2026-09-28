/**
 * models-validate-base.test.jsx — 校验弹窗按 diff 的对比基准(diff.base)决定按钮语义与删除去向。
 *
 * GET /api/models/diff 的 base 说明这次比的是谁:'catalog' = 平台内置目录(所有人共用),
 * 'user' = 用户自己的清单。以前弹窗只看「是不是管理员」:
 *   · 管理员对自建中转站点「校验」,后端实际拿他自己的清单比(全局目录里没有这个供应商),
 *     弹窗却照旧显示「对比对象:平台内置目录」,「全部添加」逐个往全局目录里写;
 *   · 删除只传 id,调用方拿不到基准,管理员勾掉目录里的下线条目时,若自己手填过同名模型,
 *     删掉的是自己的手填模型,目录条目原样保留。
 * 现在:基准以 diff.base 为准,删除把基准和待删 id 的来源(local_only)一起传回。
 * 演示数据(demo)不打后端:直接按本地清单显示「一致」。
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import i18n from '../i18n/index.js';
import { ValidateModal } from '../components/settings/model-modals.jsx';

// 文案里有括号等正则元字符,按字面匹配
const lit = (key, opts) => new RegExp(i18n.t(key, opts).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));

const RELAY = { id: 'my-relay', name: 'my-relay', models: [{ id: 'relay-a', real_name: 'relay-a', health: 'ok' }] };
const DEEPSEEK = { id: 'deepseek', name: 'DeepSeek', models: [{ id: 'deepseek-chat', real_name: 'deepseek-chat', health: 'ok' }] };

function installDiff(diff) {
  window.__apiToast = vi.fn();
  window.__refreshPlatform = vi.fn().mockResolvedValue({});
  window.api = { models: {
    diff: vi.fn().mockResolvedValue(diff),
    upsertModel: vi.fn().mockResolvedValue({ ok: true }),
  } };
}

describe('校验弹窗:对比基准以 diff.base 为准', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('管理员 + base=user(中转站不在全局目录)→「全部添加」= 重新同步自己的清单,不写全局目录', async () => {
    installDiff({ ok: true, base: 'user', remote_only: ['relay-new'], local_only: [], matching: ['relay-a'] });
    const onSyncRemote = vi.fn().mockResolvedValue({});
    render(<ValidateModal open api={RELAY} isAdminUser onSyncRemote={onSyncRemote} onClose={() => {}} onConfirm={() => {}} />);
    const btn = await screen.findByRole('button', { name: lit('settings.validate.resync') });
    expect(screen.getByText(lit('settings.validate.base_user'))).toBeTruthy();
    await act(async () => { fireEvent.click(btn); });
    expect(onSyncRemote).toHaveBeenCalledTimes(1);
    expect(window.api.models.upsertModel).not.toHaveBeenCalled();
  });

  it('管理员 + base=catalog → 「全部添加」写全局目录(维护内置目录的本职)', async () => {
    installDiff({ ok: true, base: 'catalog', remote_only: ['deepseek-new'], local_only: [], matching: ['deepseek-chat'] });
    const onSyncRemote = vi.fn();
    render(<ValidateModal open api={DEEPSEEK} isAdminUser onSyncRemote={onSyncRemote} onClose={() => {}} onConfirm={() => {}} />);
    const btn = await screen.findByRole('button', { name: lit('settings.validate.add_all') });
    expect(screen.getByText(lit('settings.validate.base_catalog'))).toBeTruthy();
    await act(async () => { fireEvent.click(btn); });
    await waitFor(() => expect(window.api.models.upsertModel).toHaveBeenCalledTimes(1));
    expect(onSyncRemote).not.toHaveBeenCalled();
  });

  it('删除:把 diff.base 与 local_only 一起传回,调用方据此决定删目录条目还是自己的模型', async () => {
    installDiff({ ok: true, base: 'catalog', remote_only: [], local_only: ['deepseek-old'], matching: ['deepseek-chat'] });
    const onConfirm = vi.fn();
    render(<ValidateModal open api={DEEPSEEK} isAdminUser onSyncRemote={() => {}} onClose={() => {}} onConfirm={onConfirm} />);
    const box = await waitFor(() => {
      const el = document.querySelector('input[type="checkbox"]');
      expect(el).toBeTruthy();
      return el;
    });
    fireEvent.click(box);
    fireEvent.click(screen.getByRole('button', { name: lit('settings.validate.delete_btn', { count: 1 }) }));
    expect(onConfirm).toHaveBeenCalledWith(['deepseek-old'], { base: 'catalog', localOnly: ['deepseek-old'] });
  });

  it('演示数据 → 不打后端,按本地清单显示一致', async () => {
    installDiff({ ok: true });
    render(<ValidateModal open demo api={DEEPSEEK} isAdminUser onSyncRemote={() => {}} onClose={() => {}} onConfirm={() => {}} />);
    await screen.findByText(i18n.t('settings.validate.in_sync'));
    expect(window.api.models.diff).not.toHaveBeenCalled();
  });
});
