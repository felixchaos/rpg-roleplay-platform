/**
 * mobile-admin-dmca-action.test.jsx — 手机管理后台「批准下架」发的 action 必须是后端认的。
 *
 * POST /api/admin/dmca/takedowns/{id}/action 只收 takedown | restore | reject(桌面管理页就发
 * 'takedown');手机端的「批准下架」按钮发的是 'grant' → 后端恒 400
 * 「action 须为 takedown|restore|reject」,手机上根本批不了下架。
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { SectionDmcaTakedowns } from '../mobile/admin/dmca.jsx';

const ITEM = { id: 31, status: 'open', complainant_name: '权利人', infringing_url: 'https://example.com/x', created_at: null };

beforeEach(() => {
  window.api = {
    admin: {
      dmcaTakedowns: {
        list: vi.fn().mockResolvedValue({ takedowns: [ITEM] }),
        action: vi.fn().mockResolvedValue({ ok: true }),
      },
    },
  };
});

describe('SectionDmcaTakedowns', () => {
  it('批准下架 → action=takedown', async () => {
    const nav = { go: vi.fn(), toast: vi.fn() };
    render(<SectionDmcaTakedowns nav={nav} />);
    fireEvent.click(await screen.findByText('批准下架'));
    await act(async () => { fireEvent.click(screen.getByText('确认')); });
    await waitFor(() => expect(window.api.admin.dmcaTakedowns.action).toHaveBeenCalledTimes(1));
    const [id, body] = window.api.admin.dmcaTakedowns.action.mock.calls[0];
    expect(id).toBe(31);
    expect(['takedown', 'restore', 'reject']).toContain(body.action);
    expect(body.action).toBe('takedown');
  });

  it('拒绝 → action=reject', async () => {
    const nav = { go: vi.fn(), toast: vi.fn() };
    render(<SectionDmcaTakedowns nav={nav} />);
    fireEvent.click(await screen.findByText('拒绝'));
    await act(async () => { fireEvent.click(screen.getByText('确认')); });
    await waitFor(() => expect(window.api.admin.dmcaTakedowns.action).toHaveBeenCalledTimes(1));
    expect(window.api.admin.dmcaTakedowns.action.mock.calls[0][1].action).toBe('reject');
  });
});
