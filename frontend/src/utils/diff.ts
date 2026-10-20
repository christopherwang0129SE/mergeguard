import type { DiffHunk, PairDiffFile } from '../types';

/** Base-file lines a hunk replaces, as an inclusive range. A pure insert covers the line it follows. */
export const baseRange = (hunk: DiffHunk): [number, number] => {
  const start = hunk.old_start;
  return hunk.old_lines > 0 ? [start, start + hunk.old_lines - 1] : [start, start];
};

const intersects = (a: [number, number], b: [number, number]): boolean => a[0] <= b[1] && b[0] <= a[1];

/** Do the two branches change any of the same base lines? */
export const hunksOverlap = (a: DiffHunk, b: DiffHunk): boolean => intersects(baseRange(a), baseRange(b));

export interface DiffRow {
  /** First base line the row covers, for the row label. */
  start: number;
  end: number;
  a: DiffHunk[];
  b: DiffHunk[];
  /** Both branches change lines in this region of the base file. */
  overlapping: boolean;
}

/**
 * Line both branches' hunks up against the base file. Hunks whose base
 * ranges intersect (directly or through a chain) share a row, so a region
 * both branches rewrote sits side by side; everything else gets its own
 * row with one side empty. Rows run in base-file order.
 */
export const alignHunks = (file: Pick<PairDiffFile, 'a' | 'b'>): DiffRow[] => {
  const items = [
    ...file.a.hunks.map((hunk) => ({ side: 'a' as const, hunk, range: baseRange(hunk) })),
    ...file.b.hunks.map((hunk) => ({ side: 'b' as const, hunk, range: baseRange(hunk) })),
  ].sort((x, y) => x.range[0] - y.range[0] || (x.side === 'a' ? -1 : 1));

  const rows: DiffRow[] = [];
  for (const item of items) {
    const last = rows[rows.length - 1];
    if (last && intersects([last.start, last.end], item.range)) {
      last.end = Math.max(last.end, item.range[1]);
      last[item.side].push(item.hunk);
    } else {
      rows.push({ start: item.range[0], end: item.range[1], a: [], b: [], overlapping: false, [item.side]: [item.hunk] });
    }
  }
  for (const row of rows) row.overlapping = row.a.length > 0 && row.b.length > 0;
  return rows;
};

/** "+12 -3" counts for one side of a file. */
export const lineCounts = (hunks: DiffHunk[]): { added: number; removed: number } => {
  let added = 0;
  let removed = 0;
  for (const hunk of hunks) {
    for (const line of hunk.lines) {
      if (line.kind === 'add') added += 1;
      else if (line.kind === 'del') removed += 1;
    }
  }
  return { added, removed };
};
