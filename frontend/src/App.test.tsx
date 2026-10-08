import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import path from 'node:path';
import { render, screen, fireEvent } from '@testing-library/react';
import App from './App';

const jsonResponse = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

// The demo path is injected via `define` in vite.config.ts: the absolute
// path to demo/checkout-demo, resolved from the config location.
const expectedDemoPath = path.resolve(process.cwd(), '../demo/checkout-demo');

describe('App', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fills the repo field with the demo path when "Try demo repo" is clicked', () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        status: 'ok',
        nebius_ready: true,
        nebius_enabled: true,
        nebius_model: 'nvidia/nemotron',
        tavily_ready: false,
      })
    );

    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'Try demo repo' }));

    const input = screen.getByLabelText(/repository/i) as HTMLInputElement;
    expect(input.value).toBe(expectedDemoPath);
  });

  it('shows the tagline under the form', () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        status: 'ok',
        nebius_ready: true,
        nebius_enabled: true,
        nebius_model: 'nvidia/nemotron',
        tavily_ready: false,
      })
    );

    render(<App />);

    expect(
      screen.getByText(
        'Point at a repo. Sentinel diffs every branch against main. Nemotron explains each risky pair.'
      )
    ).toBeInTheDocument();
  });
});
