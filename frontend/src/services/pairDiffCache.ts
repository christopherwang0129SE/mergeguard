import type { PairDiffResponse } from '../types';

// Diffs only change when the branches do; keep them for the session.
export const pairDiffCache = new Map<string, PairDiffResponse>();

/** Test hook: forget cached diffs between tests. */
export const clearPairDiffCache = () => pairDiffCache.clear();
