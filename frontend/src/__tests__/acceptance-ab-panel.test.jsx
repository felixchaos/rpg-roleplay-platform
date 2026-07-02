/**
 * acceptance-ab-panel.test.jsx — acceptance A/B 改写候选对比面板。
 *
 * 后端 GM 首稿有验收点未覆盖且节流放行时,额外生成一份「改写候选」并 yield `acceptance_alt`。
 * 首稿(玩家流式读到的)永远是权威版、不被静默替换;本面板把两版并排给玩家选择。
 *
 * 覆盖:
 *   · alt=null → 渲染 null(不占位)
 *   · 有 alt → 并排展示 original(当前版本)+ rewrite(改写版本)两栏
 *   · 「换成这一版」→ onChoose('rewrite');「保留这一版」→ onChoose('original')
 *   · 「保留当前」→ onDismiss
 *   · unmet 列表渲染
 *   · busy 时按钮禁用(防重复提交)
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AcceptanceAbPanel } from '../components/AcceptanceAbPanel.jsx';

const ALT = { alt_id: 42, turn: 7, rewrite: '你给自己泡了一杯红茶。', unmet: ['应出现红茶', '应体现啜饮'] };

describe('AcceptanceAbPanel', () => {
  it('renders null when no alt', () => {
    const { container } = render(<AcceptanceAbPanel alt={null} original="x" onChoose={() => {}} onDismiss={() => {}} />);
    expect(container.firstChild).toBeNull();
  });

  it('shows both versions side by side', () => {
    render(<AcceptanceAbPanel alt={ALT} original="你在椅子上坐了下来。" onChoose={() => {}} onDismiss={() => {}} />);
    expect(screen.getByText('你在椅子上坐了下来。')).toBeTruthy();      // 当前版本正文
    expect(screen.getByText('你给自己泡了一杯红茶。')).toBeTruthy();     // 改写版本正文
    expect(screen.getAllByText(/当前版本/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/改写版本/).length).toBeGreaterThan(0);
  });

  it('rewrite button calls onChoose("rewrite")', () => {
    const onChoose = vi.fn();
    render(<AcceptanceAbPanel alt={ALT} original="orig" onChoose={onChoose} onDismiss={() => {}} />);
    fireEvent.click(screen.getByText('换成这一版'));
    expect(onChoose).toHaveBeenCalledWith('rewrite');
  });

  it('keep button calls onChoose("original")', () => {
    const onChoose = vi.fn();
    render(<AcceptanceAbPanel alt={ALT} original="orig" onChoose={onChoose} onDismiss={() => {}} />);
    fireEvent.click(screen.getByText('保留这一版'));
    expect(onChoose).toHaveBeenCalledWith('original');
  });

  it('dismiss button calls onDismiss', () => {
    const onDismiss = vi.fn();
    render(<AcceptanceAbPanel alt={ALT} original="orig" onChoose={() => {}} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByText('保留当前'));
    expect(onDismiss).toHaveBeenCalled();
  });

  it('renders unmet reasons', () => {
    render(<AcceptanceAbPanel alt={ALT} original="orig" onChoose={() => {}} onDismiss={() => {}} />);
    expect(screen.getByText('应出现红茶')).toBeTruthy();
    expect(screen.getByText('应体现啜饮')).toBeTruthy();
  });

  it('disables actions while busy', () => {
    const onChoose = vi.fn();
    render(<AcceptanceAbPanel alt={ALT} original="orig" busy onChoose={onChoose} onDismiss={() => {}} />);
    fireEvent.click(screen.getByText('换成这一版'));
    expect(onChoose).not.toHaveBeenCalled();
  });
});
