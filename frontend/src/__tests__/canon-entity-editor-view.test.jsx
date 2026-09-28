/**
 * canon-entity-editor-view.test.jsx — 剧本详情「知识库人物」表格(CanonEntityEditorView)。
 *
 * 与 md-editor 资源管理器同一组端点(/canon-entities),同一个「新建必失败」根因的孪生入口:
 *   · 新建不再强制填 logical_key(留空由后端生成);只有名称必填
 *   · 走 window.api(统一错误归一),失败时 toast detail 是后端 error 原文
 *     (之前裸 fetch + r.json(),后端 500 纯文本时 detail 是 JSON 解析异常)
 *   · 类型切换把 type 交给后端过滤,并全量拉取(之前 limit=500 被夹到 200,type 被忽略)
 *   · 类型选项包含提取链路会产出的 organization(组织)
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { CanonEntityEditorView } from '../components/script-edit/CanonEntityEditorView.jsx';

const ROWS = [
  { logical_key: 'bg_location', name: '北港', type: 'location', importance: 3, summary: '' },
  { logical_key: 'yz_organization', name: '夜枭乐团', type: 'organization', importance: 2, summary: '' },
];

function installApi(overrides = {}) {
  const scripts = {
    canonList: vi.fn().mockResolvedValue({ ok: true, items: ROWS }),
    canonCreate: vi.fn().mockResolvedValue({ ok: true, entity: { logical_key: '灵脉_concept' } }),
    canonUpdate: vi.fn().mockResolvedValue({ ok: true }),
    canonDelete: vi.fn().mockResolvedValue({ ok: true, deleted: true }),
    ...overrides,
  };
  window.api = { scripts };
  window.__apiToast = vi.fn();
  return scripts;
}

describe('CanonEntityEditorView', () => {
  beforeEach(() => { delete window.api; });

  it('列表走 api.scripts.canonList;组织类型有中文标签', async () => {
    const scripts = installApi();
    render(<CanonEntityEditorView scriptId={9} ownerId={1} currentUserId={1} />);
    await screen.findByText('夜枭乐团');
    expect(scripts.canonList).toHaveBeenCalledWith(9, {});
    expect(screen.getAllByText('组织').length).toBeGreaterThan(0);
  });

  it('新建:logical_key 留空也能提交,不带空 key', async () => {
    const scripts = installApi();
    render(<CanonEntityEditorView scriptId={9} ownerId={1} currentUserId={1} />);
    await screen.findByText('北港');
    fireEvent.click(screen.getByText('新建知识库条目'));
    fireEvent.change(await screen.findByPlaceholderText('人物/势力名'), { target: { value: '灵脉' } });
    await act(async () => { fireEvent.click(screen.getByText('创建')); });
    await waitFor(() => expect(scripts.canonCreate).toHaveBeenCalled());
    const [sid, body] = scripts.canonCreate.mock.calls[0];
    expect(sid).toBe(9);
    expect(body.name).toBe('灵脉');
    expect(body.logical_key).toBeUndefined();
    expect(window.__apiToast).toHaveBeenCalledWith('知识库条目已创建', expect.objectContaining({ kind: 'ok' }));
  });

  it('新建失败:toast 显示后端给的原因', async () => {
    installApi({ canonCreate: vi.fn().mockRejectedValue(new Error('名称不能为空')) });
    render(<CanonEntityEditorView scriptId={9} ownerId={1} currentUserId={1} />);
    await screen.findByText('北港');
    fireEvent.click(screen.getByText('新建知识库条目'));
    fireEvent.change(await screen.findByPlaceholderText('人物/势力名'), { target: { value: '灵脉' } });
    await act(async () => { fireEvent.click(screen.getByText('创建')); });
    await waitFor(() => expect(window.__apiToast).toHaveBeenCalledWith(
      '保存失败', expect.objectContaining({ kind: 'danger', detail: '名称不能为空' }),
    ));
  });

  it('名称没填:提示只要求名称', async () => {
    const scripts = installApi();
    render(<CanonEntityEditorView scriptId={9} ownerId={1} currentUserId={1} />);
    await screen.findByText('北港');
    fireEvent.click(screen.getByText('新建知识库条目'));
    await act(async () => { fireEvent.click(await screen.findByText('创建')); });
    expect(scripts.canonCreate).not.toHaveBeenCalled();
    expect(window.__apiToast).toHaveBeenCalledWith('请填写名称', expect.objectContaining({ kind: 'warn' }));
  });
});
