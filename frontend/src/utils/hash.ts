/**
 * Linkable state in the URL hash: `#pair=feat/a..feat/b`, optionally with
 * `repo=` and `base=` so a shared link can re-run the same analysis. Git ref
 * names cannot contain "..", so the pair separator is unambiguous.
 */
export interface HashState {
  repo?: string;
  base?: string;
  pair?: { a: string; b: string };
}

export const parseHash = (hash: string): HashState => {
  const params = new URLSearchParams(hash.replace(/^#/, ''));
  const state: HashState = {};
  const repo = params.get('repo')?.trim();
  const base = params.get('base')?.trim();
  const pair = params.get('pair') ?? '';
  if (repo) state.repo = repo;
  if (base) state.base = base;
  const split = pair.indexOf('..');
  if (split > 0 && split < pair.length - 2) {
    state.pair = { a: pair.slice(0, split), b: pair.slice(split + 2) };
  }
  return state;
};

/** Keeps "/" and ":" readable in the address bar. */
const enc = (value: string): string => encodeURIComponent(value).replace(/%2F/gi, '/').replace(/%3A/gi, ':');

export const formatHash = (state: HashState): string => {
  const parts: string[] = [];
  if (state.repo) parts.push(`repo=${enc(state.repo)}`);
  if (state.base) parts.push(`base=${enc(state.base)}`);
  if (state.pair) parts.push(`pair=${enc(state.pair.a)}..${enc(state.pair.b)}`);
  return parts.length ? `#${parts.join('&')}` : '';
};
