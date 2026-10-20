import { describe, it, expect } from 'vitest';
import { alignHunks, baseRange, hunksOverlap, lineCounts } from './diff';
import type { DiffHunk, FileSide } from '../types';

const hunk = (old_start: number, old_lines: number, kinds: Array<'add' | 'del' | 'context'> = ['add']): DiffHunk => ({
  old_start,
  old_lines,
  new_start: old_start,
  new_lines: old_lines,
  lines: kinds.map((kind, i) => ({ kind, old_no: kind === 'add' ? null : old_start + i, new_no: old_start + i, text: 'x' })),
});

const side = (...hunks: DiffHunk[]): FileSide => ({ status: 'modified', patch: '', hunks, truncated: false });

describe('hunk ranges', () => {
  it('covers the replaced base lines; an insert covers the line it follows', () => {
    expect(baseRange(hunk(10, 4))).toEqual([10, 13]);
    expect(baseRange(hunk(10, 0))).toEqual([10, 10]);
    expect(hunksOverlap(hunk(10, 4), hunk(13, 2))).toBe(true);
    expect(hunksOverlap(hunk(10, 3), hunk(13, 2))).toBe(false);
  });
});

describe('alignHunks', () => {
  it('puts hunks that touch different lines in their own rows, in base order', () => {
    const rows = alignHunks({ a: side(hunk(3, 1)), b: side(hunk(12, 1)) });
    expect(rows.map((r) => [r.start, r.a.length, r.b.length, r.overlapping])).toEqual([
      [3, 1, 0, false],
      [12, 0, 1, false],
    ]);
  });

  it('lines up hunks both branches wrote over the same base lines', () => {
    const rows = alignHunks({ a: side(hunk(10, 5), hunk(40, 1)), b: side(hunk(12, 2)) });
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ start: 10, end: 14, overlapping: true });
    expect(rows[0].a).toHaveLength(1);
    expect(rows[0].b).toHaveLength(1);
    expect(rows[1]).toMatchObject({ start: 40, overlapping: false });
  });

  it('chains overlaps into one row', () => {
    const rows = alignHunks({ a: side(hunk(1, 5), hunk(8, 3)), b: side(hunk(4, 6)) });
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ start: 1, end: 10 });
    expect(rows[0].a).toHaveLength(2);
  });

  it('handles a side with no hunks', () => {
    expect(alignHunks({ a: side(), b: side() })).toEqual([]);
  });
});

describe('lineCounts', () => {
  it('counts added and removed lines', () => {
    expect(lineCounts([hunk(1, 2, ['del', 'add', 'add', 'context'])])).toEqual({ added: 2, removed: 1 });
  });
});
