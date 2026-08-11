/**
 * player-message-edit.test.jsx — 玩家发言可内联编辑(群反馈:白玖)。
 *
 * 反馈原文:「这个对话框能不能加个修改编辑功能,有时候只是想要加一句话,
 * 要复制删除粘贴太麻烦了」。
 *
 * 修复前:MsgActions 的 canEdit 写死 role === "assistant",玩家气泡上根本没有编辑按钮;
 * PlayerBlock 也没有内联编辑态。后端 /api/message/edit 本来就不限角色(源码注释明写
 * 「可编辑任意角色(玩家也能改自己输入)」),纯粹是前端单方面收窄。
 *
 * 覆盖:
 *   · PlayerBlock 渲染出编辑按钮(以前没有)
 *   · 点编辑 → 进入 textarea,初值 = 原文
 *   · 改完确认 → 调 window.api.game.editMessage,带对的 save_id/message_index/content
 *   · Escape / 取消 → 退出编辑态且不发请求
 *   · NarrativeBlock(GM)编辑能力零回归
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PlayerBlock, NarrativeBlock } from '../components/game/GameChatMessages.jsx';

function mockApi() {
  const editMessage = vi.fn().mockResolvedValue({ ok: true });
  window.api = { game: { editMessage } };
  window.toast = vi.fn();
  return editMessage;
}

// 编辑按钮没有可见文字(只有 Icon),用 aria/data-tip 之外的稳妥方式:取动作区里的按钮集合。
function editButton(container) {
  const btns = Array.from(container.querySelectorAll('.gc-msg-actions .iconbtn'));
  return btns.find((b) => b.getAttribute('data-tip') && /编辑|edit/i.test(b.getAttribute('data-tip')));
}

describe('PlayerBlock — 玩家发言内联编辑', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('玩家气泡上出现编辑按钮(修复前被 role==="assistant" 挡掉)', () => {
    mockApi();
    const { container } = render(
      <PlayerBlock text="我走向门口" ts="12:00" saveId={7} msgIndex={3} commitId={1} />,
    );
    expect(editButton(container)).toBeTruthy();
  });

  it('点编辑 → 进入 textarea,初值等于原文', () => {
    mockApi();
    const { container } = render(
      <PlayerBlock text="我走向门口" ts="12:00" saveId={7} msgIndex={3} commitId={1} />,
    );
    fireEvent.click(editButton(container));
    const ta = container.querySelector('textarea');
    expect(ta).toBeTruthy();
    expect(ta.value).toBe('我走向门口');
  });

  it('改完确认 → 用对的 save_id/message_index/content 调 editMessage', async () => {
    const editMessage = mockApi();
    const { container } = render(
      <PlayerBlock text="我走向门口" ts="12:00" saveId={7} msgIndex={3} commitId={1} />,
    );
    fireEvent.click(editButton(container));
    const ta = container.querySelector('textarea');
    fireEvent.change(ta, { target: { value: '我走向门口,并回头看了一眼。' } });
    fireEvent.click(screen.getByText('保存修改'));
    await waitFor(() => expect(editMessage).toHaveBeenCalledTimes(1));
    expect(editMessage).toHaveBeenCalledWith({
      save_id: 7, message_index: 3, content: '我走向门口,并回头看了一眼。',
    });
  });

  it('Escape 退出编辑态且不发请求', () => {
    const editMessage = mockApi();
    const { container } = render(
      <PlayerBlock text="我走向门口" ts="12:00" saveId={7} msgIndex={3} commitId={1} />,
    );
    fireEvent.click(editButton(container));
    fireEvent.keyDown(container.querySelector('textarea'), { key: 'Escape' });
    expect(container.querySelector('textarea')).toBeNull();
    expect(editMessage).not.toHaveBeenCalled();
  });

  it('编辑态下原文段落让位给编辑框(不重复显示)', () => {
    mockApi();
    const { container } = render(
      <PlayerBlock text="我走向门口" ts="12:00" saveId={7} msgIndex={3} commitId={1} />,
    );
    fireEvent.click(editButton(container));
    expect(container.querySelector('.gc-msg-body p')).toBeNull();
  });
});

describe('NarrativeBlock — GM 正文编辑零回归', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('GM 气泡仍有编辑按钮,且仍能保存', async () => {
    const editMessage = mockApi();
    const { container } = render(
      <NarrativeBlock text="夜色压下来。" ts="12:01" saveId={7} msgIndex={4} commitId={1} />,
    );
    const btn = editButton(container);
    expect(btn).toBeTruthy();
    fireEvent.click(btn);
    const ta = container.querySelector('textarea');
    expect(ta.value).toBe('夜色压下来。');
    fireEvent.change(ta, { target: { value: '夜色沉沉压下来。' } });
    fireEvent.click(screen.getByText('保存修改'));
    await waitFor(() => expect(editMessage).toHaveBeenCalledTimes(1));
    expect(editMessage).toHaveBeenCalledWith({
      save_id: 7, message_index: 4, content: '夜色沉沉压下来。',
    });
  });
});
