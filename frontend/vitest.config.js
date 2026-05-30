import { defineConfig } from 'vitest/config';

export default defineConfig({
  oxc: {
    jsx: 'automatic',
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/__tests__/**/*.{test,spec}.{js,jsx,ts,tsx}'],
    setupFiles: ['src/__tests__/setup.js'],
  },
});
