/**
 * mobile-admin-csam-decision.test.jsx — 手机管理后台 CSAM 举报「决定」只能选后端认的三个值。
 *
 * POST /api/admin/csam/reports/{id}/decision 只收 founded | unfounded | escalate(桌面管理页是下拉)。
 * 手机端以前是自由文本框,占位符写着 founded:管理员打个中文「成立」或拼错,后端恒 400;
 * 列表里的决定也直接显示英文原值。现在改成下拉,选项与后端一致,显示中文。
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import '../i18n/index.js';
import { SectionCsamReports } from '../mobile/admin/csam.jsx';

vi.mock('../mobile/icons.jsx', () => ({ Icon: () => null }));

const PENDING = { id: 7, status: 'pending', reported_user_id: 3, reported_username: 'u3' };
const DECIDED = { id: 8, status: 'decided', decision: 'unfounded', reported_user_id: 4, reported_username: 'u4' };

beforeEach(() => {
  window.api = { admin: { csamReports: {
    list: vi.fn().mockResolvedValue({ reports: [PENDING, DECIDED] }),
    decision: vi.fn().mockResolvedValue({ ok: true }),
  } } };
});

describe('SectionCsamReports', () => {
  it('决定是下拉,选项正好是后端认的三个值', async () => {
    const nav = { go: vi.fn(), toast: vi.fn() };
    render(<SectionCsamReports nav={nav} />);
    fireEvent.click(await screen.findByText('作出决定'));
    const select = await screen.findByRole('combobox');
    const values = [...select.querySelectorAll('option')].map((o) => o.value).filter(Boolean);
    expect(values.sort()).toEqual(['escalate', 'founded', 'unfounded']);
    fireEvent.change(select, { target: { value: 'escalate' } });
    await act(async () => { fireEvent.click(screen.getByText('确认')); });
    await waitFor(() => expect(window.api.admin.csamReports.decision).toHaveBeenCalledTimes(1));
    expect(window.api.admin.csamReports.decision).toHaveBeenCalledWith(7, { decision: 'escalate', notes: '' });
  });

  it('没选决定就确认 → 不发请求', async () => {
    const nav = { go: vi.fn(), toast: vi.fn() };
    render(<SectionCsamReports nav={nav} />);
    fireEvent.click(await screen.findByText('作出决定'));
    await act(async () => { fireEvent.click(screen.getByText('确认')); });
    expect(window.api.admin.csamReports.decision).not.toHaveBeenCalled();
  });

  it('已决定的举报显示中文决定,不显示英文原值', async () => {
    const nav = { go: vi.fn(), toast: vi.fn() };
    render(<SectionCsamReports nav={nav} />);
    await screen.findByText('不成立');
    expect(screen.queryByText('unfounded')).toBe(null);
  });
});
