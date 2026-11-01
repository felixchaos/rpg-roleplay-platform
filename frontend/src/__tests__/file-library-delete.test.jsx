import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import FileLibrary from '../components/FileLibrary.jsx';
import i18n from '../i18n/index.js';

const asset = { id: 123, kind: 'script_txt', name: 'synthetic-asset.txt', size: 12 };
const references = [{ kind: 'card_avatar', id: 456 }];
const text = key => i18n.t(key);
const dialog = () => screen.getByRole('dialog');
const confirm = () => within(dialog()).getByRole('button', { name: text('components.file_library.delete_modal.confirm_delete') });
const cancel = () => within(dialog()).getByRole('button', { name: text('common.cancel') });

async function openDelete() {
  render(<FileLibrary />);
  fireEvent.click(await screen.findByRole('button', { name: text('common.delete') }));
  expect(dialog()).toBeVisible();
}

beforeEach(() => {
  window.toast = vi.fn();
  window.api = { library: {
    list: vi.fn().mockResolvedValue({ ok: true, items: [asset] }),
    deleteAsset: vi.fn(),
  } };
});
afterEach(cleanup);

describe('asset library deletion', () => {
  it('opens confirmation and cancels without sending a deletion request', async () => {
    await openDelete();
    expect(window.api.library.deleteAsset).not.toHaveBeenCalled();
    fireEvent.click(cancel());
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByTitle(asset.name)).toBeVisible();
    expect(window.api.library.deleteAsset).not.toHaveBeenCalled();
  });

  it('deletes an unreferenced asset only after initial confirmation', async () => {
    window.api.library.deleteAsset.mockResolvedValue({ ok: true, deleted: true });
    await openDelete();
    fireEvent.click(confirm());
    await waitFor(() => expect(screen.queryByTitle(asset.name)).toBeNull());
    expect(window.api.library.deleteAsset).toHaveBeenCalledExactlyOnceWith(asset.id, false);
    expect(window.toast).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ kind: 'ok' }));
  });

  it('shows references and allows cancellation without forcing deletion', async () => {
    window.api.library.deleteAsset.mockResolvedValue({ ok: false, needs_confirm: true, references });
    await openDelete();
    fireEvent.click(confirm());
    await screen.findByText(text('components.file_library.delete_modal.refs_warning_header'));
    expect(within(dialog()).getByText(/#456/)).toBeVisible();
    fireEvent.click(cancel());
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(window.api.library.deleteAsset).toHaveBeenCalledExactlyOnceWith(asset.id, false);
    expect(screen.getByTitle(asset.name)).toBeVisible();
  });

  it('forces a referenced deletion only after the reference warning is confirmed', async () => {
    window.api.library.deleteAsset
      .mockResolvedValueOnce({ ok: false, needs_confirm: true, references })
      .mockResolvedValueOnce({ ok: true, deleted: true });
    await openDelete();
    fireEvent.click(confirm());
    await screen.findByText(text('components.file_library.delete_modal.refs_warning_header'));
    fireEvent.click(confirm());
    await waitFor(() => expect(screen.queryByTitle(asset.name)).toBeNull());
    expect(window.api.library.deleteAsset.mock.calls).toEqual([[asset.id, false], [asset.id, true]]);
  });

  it.each([
    { ok: false, error: 'not_found' },
    { ok: true, deleted: false },
    undefined,
  ])('keeps the asset and reports an unsuccessful first response: %j', async response => {
    window.api.library.deleteAsset.mockResolvedValue(response);
    await openDelete();
    fireEvent.click(confirm());
    await waitFor(() => expect(window.toast).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ kind: 'danger' })));
    expect(screen.getByTitle(asset.name)).toBeVisible();
    expect(window.api.library.deleteAsset).toHaveBeenCalledTimes(1);
  });

  it('keeps referenced assets when the confirmed request fails, then permits retry', async () => {
    window.api.library.deleteAsset
      .mockResolvedValueOnce({ ok: false, needs_confirm: true, references })
      .mockResolvedValueOnce({ ok: false, error: 'not_found' })
      .mockResolvedValueOnce({ ok: true, deleted: true });
    await openDelete();
    fireEvent.click(confirm());
    await screen.findByText(text('components.file_library.delete_modal.refs_warning_header'));
    fireEvent.click(confirm());
    await waitFor(() => expect(window.toast).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ kind: 'danger' })));
    expect(screen.getByTitle(asset.name)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: text('common.delete') }));
    fireEvent.click(confirm());
    await waitFor(() => expect(screen.queryByTitle(asset.name)).toBeNull());
  });

  it('keeps the asset after a network error', async () => {
    window.api.library.deleteAsset.mockRejectedValue(new Error('synthetic network failure'));
    await openDelete();
    fireEvent.click(confirm());
    await waitFor(() => expect(window.toast).toHaveBeenCalledWith('synthetic network failure', expect.objectContaining({ kind: 'danger' })));
    expect(screen.getByTitle(asset.name)).toBeVisible();
  });

  it('disables confirmation and cancellation while deletion is pending', async () => {
    let resolve;
    window.api.library.deleteAsset.mockImplementation(() => new Promise(done => { resolve = done; }));
    await openDelete();
    fireEvent.click(confirm());
    expect(within(dialog()).getByRole('button', { name: text('components.file_library.delete_modal.deleting') })).toBeDisabled();
    expect(cancel()).toBeDisabled();
    await act(async () => resolve({ ok: true, deleted: true }));
    expect(window.api.library.deleteAsset).toHaveBeenCalledTimes(1);
  });
});
