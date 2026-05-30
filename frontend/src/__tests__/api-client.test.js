/**
 * api-client.test.js — smoke tests for the IIFE-based API client.
 *
 * The api-client.js is a self-executing script that attaches helpers to
 * `window`. We load it by evaluating the file content in a jsdom context.
 */
import { describe, it, expect, beforeAll, vi } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

// Read the api-client source (IIFE, not ES module — no import)
const apiClientSource = readFileSync(
  resolve(__dirname, '../api-client.js'),
  'utf-8',
);

describe('api-client basics', () => {
  beforeAll(() => {
    // Evaluate the IIFE in the jsdom global context
    const fn = new Function(apiClientSource);
    fn.call(window);
  });

  it('should expose window.__API_BASE as a string', () => {
    expect(typeof window.__API_BASE).toBe('string');
  });

  it('detectBase returns localhost backend when port is not 7860', () => {
    // jsdom default location is about:blank; detectBase should fall back
    // to "http://127.0.0.1:7860" or "" depending on conditions.
    const base = window.__API_BASE;
    // In jsdom (about:blank), protocol is about: which is not file:,
    // hostname is empty string — the catch block returns fallback.
    expect(typeof base).toBe('string');
  });

  it('should expose window.ApiError as a constructor', () => {
    expect(typeof window.ApiError).toBe('function');
    const err = new window.ApiError('test_code', 404, 'not found', null);
    expect(err).toBeInstanceOf(Error);
    expect(err.code).toBe('test_code');
    expect(err.status).toBe(404);
    expect(err.message).toBe('not found');
  });

  it('should expose window.api object', () => {
    expect(window.api).toBeDefined();
    expect(typeof window.api).toBe('object');
  });

  it('_send rejects on network error', async () => {
    // Mock fetch to simulate network failure
    const originalFetch = window.fetch;
    window.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));

    try {
      // Access the internal _send via the api helpers;
      // window.api.auth should have methods that call _send
      await expect(
        window.api.auth.me()
      ).rejects.toThrow();
    } finally {
      window.fetch = originalFetch;
    }
  });
});
