import { describe, it, expect, vi, afterEach } from 'vitest';
import { analyzeStream, readNdjson, StreamUnavailableError } from './stream';
import type { StreamEvent } from '../types';

/** A body that delivers the given string chunks, split wherever the test says. */
const bodyOf = (...chunks: string[]) => {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
};

const collect = async (stream: ReadableStream<Uint8Array>) => {
  const out: unknown[] = [];
  for await (const value of readNdjson(stream)) out.push(value);
  return out;
};

describe('readNdjson', () => {
  it('yields one object per line', async () => {
    expect(await collect(bodyOf('{"a":1}\n{"b":2}\n'))).toEqual([{ a: 1 }, { b: 2 }]);
  });

  it('joins a line split across chunks', async () => {
    expect(await collect(bodyOf('{"type":"st', 'age","t":1}\n{"x"', ':2}\n'))).toEqual([
      { type: 'stage', t: 1 },
      { x: 2 },
    ]);
  });

  it('keeps multi-byte characters split across chunks intact', async () => {
    const bytes = new TextEncoder().encode('{"label":"Nemotron’s"}\n');
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 20));
        controller.enqueue(bytes.slice(20));
        controller.close();
      },
    });
    expect(await collect(stream)).toEqual([{ label: 'Nemotron’s' }]);
  });

  it('reads a last line without a newline, skips blank and broken lines', async () => {
    expect(await collect(bodyOf('\n{"a":1}\r\nnot json\n\n{"b":2}'))).toEqual([{ a: 1 }, { b: 2 }]);
  });
});

describe('analyzeStream', () => {
  afterEach(() => vi.unstubAllGlobals());

  const stub = (response: Response) => {
    const fetchMock = vi.fn(() => Promise.resolve(response));
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
  };

  it('passes every event on and resolves with the result', async () => {
    const result = { base_branch: 'main', branches: [], diffs: [], pairs: [] };
    const fetchMock = stub(
      new Response(
        bodyOf(
          '{"type":"stage","stage":"diff","status":"start","label":"Diff","t":0}\n',
          '{"type":"ping","t":5}\n',
          `{"type":"result","result":${JSON.stringify(result)},"t":6}\n`
        )
      )
    );
    const events: StreamEvent[] = [];
    await expect(analyzeStream({ repo_path: '/r' }, (e) => events.push(e))).resolves.toEqual(result);
    expect(events.map((e) => e.type)).toEqual(['stage', 'ping', 'result']);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/analyze\/stream$/);
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ repo_path: '/r' });
  });

  it('signals a missing endpoint so the caller can fall back', async () => {
    stub(new Response('{"detail":"Not Found"}', { status: 404 }));
    await expect(analyzeStream({}, () => {})).rejects.toBeInstanceOf(StreamUnavailableError);
  });

  it('surfaces the detail of a pre-flight HTTP error', async () => {
    stub(new Response('{"detail":"/x is not a git repository"}', { status: 400 }));
    await expect(analyzeStream({}, () => {})).rejects.toThrow('/x is not a git repository');
  });

  it('throws the detail of an error event', async () => {
    stub(new Response(bodyOf('{"type":"error","detail":"clone failed: timeout","t":3}\n')));
    await expect(analyzeStream({}, () => {})).rejects.toThrow('clone failed: timeout');
  });

  it('fails when the stream ends without a result', async () => {
    stub(new Response(bodyOf('{"type":"ping","t":1}\n')));
    await expect(analyzeStream({}, () => {})).rejects.toThrow(/ended before a result/);
  });
});
