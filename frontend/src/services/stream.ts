import type { AnalyzeRequest, AnalyzeResponse, StreamEvent } from '../types';
import { API_BASE_URL, readError } from './api';

/** The backend has no /api/analyze/stream (older deploy): use POST /api/analyze. */
export class StreamUnavailableError extends Error {
  constructor() {
    super('Streaming endpoint not available');
    this.name = 'StreamUnavailableError';
  }
}

/**
 * Split a byte stream into JSON objects, one per line. A line may arrive in
 * pieces across chunks, so the tail without a newline waits for the next
 * chunk. Blank lines are skipped; a line that is not JSON is skipped too, so
 * one bad line does not end the run.
 */
export async function* readNdjson(stream: ReadableStream<Uint8Array>): AsyncGenerator<unknown> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const parse = (line: string): unknown => {
    try {
      return JSON.parse(line);
    } catch {
      return undefined;
    }
  };
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let newline = buffer.indexOf('\n');
      while (newline >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (line) {
          const parsed = parse(line);
          if (parsed !== undefined) yield parsed;
        }
        newline = buffer.indexOf('\n');
      }
      if (done) break;
    }
    const tail = buffer.trim();
    if (tail) {
      const parsed = parse(tail);
      if (parsed !== undefined) yield parsed;
    }
  } finally {
    reader.releaseLock();
  }
}

const isEvent = (value: unknown): value is StreamEvent =>
  typeof value === 'object' && value !== null && typeof (value as { type?: unknown }).type === 'string';

/**
 * Run an analysis over POST /api/analyze/stream. Every event goes to
 * `onEvent` as it arrives; the promise resolves with the final `result`.
 * Throws StreamUnavailableError on 404 so the caller can fall back, the
 * server's message for an HTTP error or a mid-stream `error` event, and an
 * AbortError when `signal` is aborted.
 */
export async function analyzeStream(
  request: AnalyzeRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal
): Promise<AnalyzeResponse> {
  const response = await fetch(`${API_BASE_URL}/api/analyze/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/x-ndjson' },
    body: JSON.stringify(request),
    signal,
  });
  if (response.status === 404 || response.status === 405) {
    throw new StreamUnavailableError();
  }
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  if (!response.body) {
    throw new StreamUnavailableError();
  }

  let result: AnalyzeResponse | null = null;
  for await (const value of readNdjson(response.body)) {
    if (!isEvent(value)) continue;
    onEvent(value);
    if (value.type === 'error') throw new Error(value.detail || 'Analysis failed');
    if (value.type === 'result') result = value.result;
  }
  if (!result) {
    throw new Error('The analysis stream ended before a result arrived. Check the backend log.');
  }
  return result;
}
