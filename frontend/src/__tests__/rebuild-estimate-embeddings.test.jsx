/**
 * rebuild-estimate-embeddings.test.jsx — 「按类型重嵌」回归测试。
 *
 * 背景:后端从一开始就支持 POST /rebuild/embeddings body.include=[...](估算路径
 * rebuild_scheduler 与执行路径 rebuild_worker._rebuild_embeddings 都读它),但前端
 * 一直没给口子——剧本详情页的 embed 4 子卡是只读进度、文案却写着「可选择性重嵌」,
 * 用户满页找不到按钮(群反馈)。现在勾选口子落在 RebuildEstimateModal。
 *
 * 另含跨语言奇偶守卫:前端 EMBED_KINDS 必须与后端两处默认 include 字面量一致,
 * 否则勾选项与后端实际重嵌的表会对不上(且不会有任何报错)。
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { RebuildEstimateModal } from '../components/RebuildEstimateModal.jsx';

const OK_ESTIMATE = { ok: true, tokens_est: 0, cost_est: 0, affects: [], prereqs: [] };

function renderModal(props) {
  return render(
    <RebuildEstimateModal
      open
      module="embeddings"
      scriptId={7}
      estimate={OK_ESTIMATE}
      loading={false}
      options={null}
      onOptionsChange={() => {}}
      onClose={() => {}}
      onConfirm={() => {}}
      {...props}
    />,
  );
}

/* 4 个类型勾选框 = 文档顺序上的前 4 个 checkbox(embeddings 分支只有这一组)。 */
const kindBoxes = () => screen.getAllByRole('checkbox');

describe('RebuildEstimateModal — embeddings 按类型重嵌', () => {
  it('embeddings 弹窗渲染 4 个类型勾选框,默认全选', () => {
    renderModal();
    const boxes = kindBoxes();
    expect(boxes).toHaveLength(4);
    expect(boxes.every((b) => b.checked)).toBe(true);
  });

  it('取消一个类型 → onOptionsChange 收到剩余 3 个的 include(保持固定顺序)', () => {
    const onOptionsChange = vi.fn();
    renderModal({ onOptionsChange });
    fireEvent.click(kindBoxes()[1]);  // cards
    expect(onOptionsChange).toHaveBeenCalledWith({ include: ['chunks', 'worldbook', 'canon'] });
  });

  it('重新勾回 → include 回到 EMBED_KINDS 原顺序,不是点击顺序', () => {
    const onOptionsChange = vi.fn();
    renderModal({ onOptionsChange, options: { include: ['worldbook'] } });
    fireEvent.click(kindBoxes()[0]);  // chunks 勾回
    expect(onOptionsChange).toHaveBeenLastCalledWith({ include: ['chunks', 'worldbook'] });
  });

  it('全不选 → 确认按钮禁用(否则后端会把空 include 当全选,与所见相反)', () => {
    renderModal();
    kindBoxes().forEach((b) => fireEvent.click(b));
    const confirm = screen.getByRole('button', { name: /确认重做|Confirm/ });
    expect(confirm).toBeDisabled();
  });

  it('非 embeddings 模块不渲染类型勾选框', () => {
    renderModal({ module: 'anchors' });
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
  });
});

describe('EMBED_KINDS 跨语言奇偶守卫', () => {
  const modalSrc = readFileSync(resolve(__dirname, '../components/RebuildEstimateModal.jsx'), 'utf-8');
  const feKinds = JSON.parse(
    (modalSrc.match(/const EMBED_KINDS = (\[[^\]]*\]);/) || [])[1].replace(/'/g, '"'),
  );

  it.each([
    ['rebuild_worker.py', '../../../rpg/platform_app/import_pipeline/rebuild_worker.py'],
    ['rebuild_scheduler.py', '../../../rpg/platform_app/import_pipeline/rebuild_scheduler.py'],
  ])('前端 EMBED_KINDS == 后端 %s 的默认 include', (_name, rel) => {
    const py = readFileSync(resolve(__dirname, rel), 'utf-8');
    const m = py.match(/body\.get\("include"\)\s*or\s*(\[[^\]]*\])/);
    expect(m).not.toBeNull();
    expect(JSON.parse(m[1].replace(/'/g, '"'))).toEqual(feKinds);
  });
});
