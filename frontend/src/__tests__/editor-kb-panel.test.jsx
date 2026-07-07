/**
 * editor-kb-panel.test.jsx — useKbHealthBadge 回归测试。
 *
 * 剧本编辑器(/md-editor)顶栏徽标用的轻量 hook:拉一次 /modules-status,
 * 数出非 ready 模块个数。铁律:失败静默返回 null,不抛、不打扰主流程。
 */
import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useKbHealthBadge } from '../components/EditorKbPanel.jsx';

afterEach(() => { delete window.api; });

describe('useKbHealthBadge', () => {
  it('全部 ready → stale_count=0, ready=true', async () => {
    window.api = {
      scripts: {
        getModulesStatus: vi.fn(async () => ({
          ok: true,
          modules: [
            { module: 'chunks', status: 'ready' },
            { module: 'canon', status: 'ready' },
          ],
        })),
      },
    };
    const { result } = renderHook(() => useKbHealthBadge(42));
    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current).toEqual({ stale_count: 0, ready: true });
  });

  it('部分 stale/missing → stale_count 累计, ready=false', async () => {
    window.api = {
      scripts: {
        getModulesStatus: vi.fn(async () => ({
          ok: true,
          modules: [
            { module: 'chunks', status: 'ready' },
            { module: 'cards', status: 'stale' },
            { module: 'worldbook', status: 'missing' },
          ],
        })),
      },
    };
    const { result } = renderHook(() => useKbHealthBadge(42));
    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current).toEqual({ stale_count: 2, ready: false });
  });

  it('modules 为 dict 形态(非数组)也能正确统计', async () => {
    window.api = {
      scripts: {
        getModulesStatus: vi.fn(async () => ({
          ok: true,
          modules: {
            chunks: { status: 'ready' },
            cards: { status: 'running' },
          },
        })),
      },
    };
    const { result } = renderHook(() => useKbHealthBadge(42));
    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current).toEqual({ stale_count: 1, ready: false });
  });

  it('scriptId 为空 → 不请求,badge 为 null', async () => {
    const getModulesStatus = vi.fn();
    window.api = { scripts: { getModulesStatus } };
    const { result } = renderHook(() => useKbHealthBadge(null));
    expect(result.current).toBeNull();
    expect(getModulesStatus).not.toHaveBeenCalled();
  });

  it('请求失败(网络/权限)→ 静默返回 null,不抛异常', async () => {
    window.api = {
      scripts: {
        getModulesStatus: vi.fn(async () => { throw new Error('network down'); }),
      },
    };
    const { result } = renderHook(() => useKbHealthBadge(42));
    await waitFor(() => expect(window.api.scripts.getModulesStatus).toHaveBeenCalled());
    expect(result.current).toBeNull();
  });

  it('r.ok === false → 静默返回 null', async () => {
    window.api = {
      scripts: {
        getModulesStatus: vi.fn(async () => ({ ok: false, error: 'forbidden' })),
      },
    };
    const { result } = renderHook(() => useKbHealthBadge(42));
    await waitFor(() => expect(window.api.scripts.getModulesStatus).toHaveBeenCalled());
    expect(result.current).toBeNull();
  });
});
