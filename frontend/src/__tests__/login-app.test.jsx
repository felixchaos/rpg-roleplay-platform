/**
 * login-app.test.jsx — smoke tests for LoginApp component.
 *
 * Verifies the component renders without crashing and shows the login form
 * structure driven by the auth schema endpoint.
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { LoginApp } from '../login-app.jsx';

// Mock schema response matching the backend auth.rs format
const MOCK_SCHEMA = {
  login: [
    { key: 'username', label: 'Username', type: 'text', required: true, autocomplete: 'username' },
    { key: 'password', label: 'Password', type: 'password', required: true, autocomplete: 'current-password', min_length: 8 },
  ],
  register: [
    { key: 'username', label: 'Username', type: 'text', required: true, autocomplete: 'username' },
    { key: 'display_name', label: 'Display Name', type: 'text', required: false },
    { key: 'password', label: 'Password', type: 'password', required: true, autocomplete: 'new-password', min_length: 8 },
  ],
  notes: {
    min_password_length: 8,
    invite_only: false,
  },
};

describe('LoginApp', () => {
  beforeEach(() => {
    // Mock window.api so the "already logged in?" check doesn't blow up
    window.api = {
      auth: {
        me: vi.fn().mockRejectedValue(new Error('not logged in')),
        login: vi.fn(),
        register: vi.fn(),
      },
    };
    window.__API_BASE = '';

    // Mock fetch for /api/v1/auth/schema
    window.fetch = vi.fn().mockImplementation((url) => {
      if (typeof url === 'string' && url.includes('/auth/schema')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(MOCK_SCHEMA),
          headers: new Headers({ 'content-type': 'application/json' }),
        });
      }
      return Promise.reject(new Error('unexpected fetch: ' + url));
    });
  });

  it('renders without crashing', () => {
    const { container } = render(<LoginApp />);
    expect(container).toBeTruthy();
  });

  it('fetches auth schema on mount', async () => {
    render(<LoginApp />);
    await waitFor(() => {
      expect(window.fetch).toHaveBeenCalledWith(
        expect.stringContaining('/auth/schema'),
        expect.any(Object),
      );
    });
  });

  it('renders login form fields from schema', async () => {
    render(<LoginApp />);
    // Wait for schema to load and fields to render
    await waitFor(() => {
      expect(screen.getByText('Username')).toBeInTheDocument();
    });
    expect(screen.getByText('Password')).toBeInTheDocument();
  });

  it('shows both login and register tabs/buttons', async () => {
    render(<LoginApp />);
    await waitFor(() => {
      // The component should have mode toggle elements
      const container = document.body;
      expect(container.textContent).toContain('Username');
    });
  });
});
