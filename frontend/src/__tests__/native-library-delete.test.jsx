import React from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import ts from 'typescript';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

// Run the actual native screen with host widgets stubbed: Expo/RN packages are not
// required to exercise its alert callbacks, request flags, and success handling.
function loadNative(path, modules) {
  const source = readFileSync(resolve(__dirname, '../../../mobile', path), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true },
    fileName: path,
  });
  const exports = {};
  new Function('require', 'exports', outputText)(name => {
    if (!(name in modules)) throw new Error(`Unstubbed native import: ${name}`);
    return modules[name];
  }, exports);
  return exports;
}

const asset = { id: 123, url: '/synthetic.png' };
const referenceResponse = { ok: false, needs_confirm: true, references: [{ kind: 'card_avatar', id: 456 }] };
const alerts = { alert: vi.fn() };
const library = { list: vi.fn(), remove: vi.fn() };
const box = ({ children }) => <div>{children}</div>;
const AnimatedView = box;
const Screen = loadNative('app/(app)/reliquary.tsx', {
  react: React,
  'react-native': {
    ActivityIndicator: () => null,
    Alert: alerts,
    FlatList: ({ data, renderItem }) => <div>{data.map((item, index) => <React.Fragment key={item.id}>{renderItem({ item, index })}</React.Fragment>)}</div>,
    Modal: () => null,
    Pressable: ({ children, onPress, onLongPress }) => <button onClick={onPress} onContextMenu={onLongPress}>{children}</button>,
    StyleSheet: { create: value => value }, Text: box, View: box,
    useWindowDimensions: () => ({ width: 400 }),
  },
  'expo-image': { Image: () => <span>synthetic relic</span> },
  'expo-router': { useRouter: () => ({ back: vi.fn() }) },
  'react-native-safe-area-context': { useSafeAreaInsets: () => ({ top: 0, bottom: 0 }) },
  'expo-haptics': { impactAsync: vi.fn(), ImpactFeedbackStyle: { Medium: 'medium' } },
  'react-native-reanimated': { __esModule: true, default: { View: AnimatedView }, FadeIn: { delay: () => ({ duration: () => null }) } },
  '@/components/GrimoireBackdrop': { GrimoireBackdrop: box },
  '@/api': { library },
  '@/api/http': { baseUrl: async () => '', ApiError: Error },
  '@/theme/theme': { theme: { space: n => n * 4, color: {}, font: {}, size: {}, radius: {} } },
}).default;

async function openDelete() {
  render(<Screen />);
  fireEvent.contextMenu(await screen.findByText('synthetic relic'));
}
const lastAlert = () => alerts.alert.mock.calls.at(-1);
const chooseDelete = async () => {
  const callback = lastAlert()[2].find(button => button.style === 'destructive').onPress;
  await act(async () => callback());
};

beforeEach(() => {
  alerts.alert.mockReset();
  library.list.mockReset().mockResolvedValue({ ok: true, items: [asset] });
  library.remove.mockReset();
});
afterEach(cleanup);

describe('native library deletion confirmation', () => {
  it('cancels the initial alert without a deletion request', async () => {
    await openDelete();
    expect(lastAlert()[2].some(button => button.style === 'cancel')).toBe(true);
    expect(library.remove).not.toHaveBeenCalled();
  });

  it('deletes unreferenced assets without forcing, then reloads', async () => {
    library.remove.mockResolvedValue({ ok: true, deleted: true });
    await openDelete();
    await chooseDelete();
    expect(library.remove).toHaveBeenCalledExactlyOnceWith(asset.id, false);
    await waitFor(() => expect(library.list).toHaveBeenCalledTimes(2));
  });

  it('shows references before force deletion and allows cancellation', async () => {
    library.remove.mockResolvedValue(referenceResponse);
    await openDelete();
    await chooseDelete();
    expect(alerts.alert).toHaveBeenCalledTimes(2);
    expect(lastAlert()[1]).toContain('#456');
    expect(lastAlert()[2].some(button => button.style === 'cancel')).toBe(true);
    expect(library.remove).toHaveBeenCalledExactlyOnceWith(asset.id, false);
    expect(library.list).toHaveBeenCalledTimes(1);
  });

  it('sends force only after confirming the reference warning', async () => {
    library.remove.mockResolvedValueOnce(referenceResponse).mockResolvedValueOnce({ ok: true, deleted: true });
    await openDelete();
    await chooseDelete();
    await chooseDelete();
    expect(library.remove.mock.calls).toEqual([[asset.id, false], [asset.id, true]]);
    expect(library.list).toHaveBeenCalledTimes(2);
  });

  it.each([{ ok: false, error: 'not_found' }, { ok: true, deleted: false }, undefined])('reports unsuccessful response without reloading: %j', async response => {
    library.remove.mockResolvedValue(response);
    await openDelete();
    await chooseDelete();
    expect(lastAlert()[0]).toBe('无法删除');
    expect(library.list).toHaveBeenCalledTimes(1);
  });

  it('reports a failed confirmed deletion without reloading', async () => {
    library.remove.mockResolvedValueOnce(referenceResponse).mockRejectedValueOnce(new Error('synthetic failure'));
    await openDelete();
    await chooseDelete();
    await chooseDelete();
    expect(lastAlert()[0]).toBe('无法删除');
    expect(library.list).toHaveBeenCalledTimes(1);
  });
});

describe('native library API confirmation flag', () => {
  it.each([[undefined, false], [false, false], [true, true], ['false', false], [1, false]])('requires explicit true (%j)', async (confirmation, expected) => {
    const post = vi.fn().mockResolvedValue({ ok: true, deleted: true });
    const api = loadNative('src/api/index.ts', { './http': { http: { post }, baseUrl: vi.fn() }, './sse': {} });
    await api.library.remove(123, confirmation);
    expect(post).toHaveBeenCalledExactlyOnceWith('/api/library/asset/123/delete', { confirm: expected });
  });
});
