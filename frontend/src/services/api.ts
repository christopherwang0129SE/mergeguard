import type { AnalyzeRequest, AnalyzeResponse, HealthResponse } from '../types';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

/**
 * Parse an error response body. FastAPI returns `{ detail: "..." }` for 4xx,
 * but a proxy or gateway may return a non-JSON body (e.g. plain text "502 Bad
 * Gateway"). We surface the status and the raw text in that case.
 */
async function readError(response: Response): Promise<string> {
  let detail: string | null = null;
  let raw = '';
  try {
    raw = await response.text();
  } catch {
    raw = '';
  }
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as { detail?: unknown };
      if (typeof parsed.detail === 'string') {
        detail = parsed.detail;
      }
    } catch {
      // Not JSON — fall through to the raw text.
    }
  }
  if (detail) {
    return detail;
  }
  const text = raw.trim();
  return text ? `${response.status} ${response.statusText || ''} ${text}`.trim() : `Request failed with status ${response.status}`;
}

export async function analyzeRepo(request: AnalyzeRequest): Promise<AnalyzeResponse> {
  const response = await fetch(`${API_BASE_URL}/api/analyze`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(await readError(response));
  }

  return response.json();
}

export async function checkHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/api/health`);
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json();
}
