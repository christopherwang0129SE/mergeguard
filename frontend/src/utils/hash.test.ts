import { describe, it, expect } from 'vitest';
import { formatHash, parseHash } from './hash';

describe('URL hash', () => {
  it('round-trips repo, base and pair', () => {
    const hash = formatHash({ repo: '/srv/shop', base: 'main', pair: { a: 'feat/a', b: 'feat/b' } });
    expect(hash).toBe('#repo=/srv/shop&base=main&pair=feat/a..feat/b');
    expect(parseHash(hash)).toEqual({ repo: '/srv/shop', base: 'main', pair: { a: 'feat/a', b: 'feat/b' } });
  });

  it('keeps URLs with their own query intact', () => {
    const repo = 'https://github.com/o/r/pull/12?x=1&y=2';
    expect(parseHash(formatHash({ repo })).repo).toBe(repo);
  });

  it('reads a bare pair and ignores a malformed one', () => {
    expect(parseHash('#pair=feat/a..feat/b')).toEqual({ pair: { a: 'feat/a', b: 'feat/b' } });
    expect(parseHash('#pair=feat/a')).toEqual({});
    expect(parseHash('#pair=..b')).toEqual({});
    expect(parseHash('')).toEqual({});
    expect(formatHash({})).toBe('');
  });
});
