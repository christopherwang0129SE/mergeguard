import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { analyzeRepo, checkHealth } from './api';

const jsonResponse = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

describe('api', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('POSTs to /api/analyze with the request body', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        base_branch: 'main',
        branches: [],
        diffs: [],
        pairs: [],
        risk_model: null,
      })
    );

    await analyzeRepo({ repo_url: 'https://github.com/foo/bar.git', base_branch: 'main' });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://localhost:8000/api/analyze');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({
      repo_url: 'https://github.com/foo/bar.git',
      base_branch: 'main',
    });
  });

  it('GETs /api/health', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        status: 'ok',
        nebius_ready: true,
        nebius_enabled: true,
        nebius_model: 'nvidia/nemotron',
        tavily_ready: false,
      })
    );

    await checkHealth();

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe('http://localhost:8000/api/health');
  });

  it('throws the detail message on a 400 JSON error', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'No branches to analyze' }, 400));

    await expect(analyzeRepo({ repo_path: '/x' })).rejects.toThrow('No branches to analyze');
  });

  it('throws status and text when the error body is not JSON', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response('Bad Gateway', { status: 502, statusText: 'Bad Gateway' })
    );

    await expect(checkHealth()).rejects.toThrow(/502/);
  });
});
