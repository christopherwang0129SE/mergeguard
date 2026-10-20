import { useEffect, useState } from 'react';
import type { AnalyzeRequest, DiffHunk, PairDiffFile, PairDiffResponse } from '../types';
import { fetchPairDiff } from '../services/api';
import { pairDiffCache as cache } from '../services/pairDiffCache';
import { alignHunks, lineCounts } from '../utils/diff';

interface PairDiffProps {
  /** The repo fields of the analysis this pair came from. */
  source: Pick<AnalyzeRequest, 'repo_path' | 'repo_url'>;
  baseBranch: string;
  branchA: string;
  branchB: string;
}

type Load =
  | { state: 'loading' }
  | { state: 'error'; message: string }
  | { state: 'ready'; diff: PairDiffResponse };


const STATUS_TEXT: Record<string, string> = {
  modified: 'modified',
  added: 'added',
  deleted: 'deleted',
  unchanged: 'not changed',
};

const Hunk = ({ hunk }: { hunk: DiffHunk }) => (
  <div className="hunk">
    <p className="hunk-head tabular">
      base {hunk.old_start}
      {hunk.old_lines > 1 ? `-${hunk.old_start + hunk.old_lines - 1}` : ''}
    </p>
    <pre className="hunk-lines" tabIndex={0} aria-label={`Lines from base line ${hunk.old_start}`}>
      <code>
        {hunk.lines.map((line, i) => (
          <span key={i} className="dl" data-kind={line.kind}>
            <span className="dl-no" aria-hidden="true">
              {line.kind === 'add' ? line.new_no ?? '' : line.old_no ?? ''}
            </span>
            <span className="dl-sign" aria-hidden="true">
              {line.kind === 'add' ? '+' : line.kind === 'del' ? '-' : ' '}
            </span>
            <span className="sr-only">{line.kind === 'add' ? 'added: ' : line.kind === 'del' ? 'removed: ' : ''}</span>
            <span className="dl-text">{line.text || ' '}</span>
          </span>
        ))}
      </code>
    </pre>
  </div>
);

const SideHead = ({ side, branch, file }: { side: 'A' | 'B'; branch: string; file: PairDiffFile }) => {
  const data = side === 'A' ? file.a : file.b;
  const { added, removed } = lineCounts(data.hunks);
  return (
    <p className="side-head">
      <span className="side-key">{side}</span>
      <span className="side-branch">{branch}</span>
      <span className="side-stat tabular">
        {data.status === 'unchanged' ? STATUS_TEXT.unchanged : `+${added} -${removed}`}
      </span>
    </p>
  );
};

/** A file only one branch touched: one column, folded until opened. */
const OneSidedFile = ({ file, branchA, branchB }: { file: PairDiffFile; branchA: string; branchB: string }) => {
  const side = file.a.status === 'unchanged' ? 'B' : 'A';
  const data = side === 'A' ? file.a : file.b;
  return (
    <details className="file file-single">
      <summary className="file-head">
        <span className="file-path">{file.path}</span>
        <span className="file-note">
          Only {side} changes this file ({STATUS_TEXT[data.status] ?? data.status})
        </span>
      </summary>
      {data.truncated && <p className="file-warn">This diff is large; the backend cut it short.</p>}
      <div className="sbs-head sbs-head-single">
        <SideHead side={side} branch={side === 'A' ? branchA : branchB} file={file} />
      </div>
      {data.hunks.length === 0 && <p className="file-empty">No text changes (binary or mode change).</p>}
      {data.hunks.map((h) => (
        <Hunk key={`${h.old_start}-${h.new_start}`} hunk={h} />
      ))}
    </details>
  );
};

const FileDiff = ({ file, branchA, branchB }: { file: PairDiffFile; branchA: string; branchB: string }) => {
  if (!file.shared && (file.a.status === 'unchanged' || file.b.status === 'unchanged')) {
    return <OneSidedFile file={file} branchA={branchA} branchB={branchB} />;
  }
  const rows = alignHunks(file);
  const overlaps = rows.filter((r) => r.overlapping).length;
  return (
    <section className="file" data-shared={file.shared || undefined} aria-label={file.path}>
      <header className="file-head">
        <h4 className="file-path">{file.path}</h4>
        <p className="file-note">
          {overlaps > 0 || file.overlapping
            ? `Both branches change the same lines${overlaps > 1 ? ` in ${overlaps} places` : ''}`
            : 'Both branches change this file, in different lines. Git merges it cleanly.'}
        </p>
      </header>
      {(file.a.truncated || file.b.truncated) && (
        <p className="file-warn">This diff is large; the backend cut it short.</p>
      )}
      <div className="sbs">
        <div className="sbs-head">
          <SideHead side="A" branch={branchA} file={file} />
          <SideHead side="B" branch={branchB} file={file} />
        </div>
        {rows.length === 0 && <p className="file-empty">No text changes (binary or mode change).</p>}
        {rows.map((row) => (
          <div key={`${row.start}-${row.end}`} className="sbs-row" data-overlap={row.overlapping || undefined}>
            {row.overlapping && (
              <p className="overlap-flag">
                Both branches change base lines {row.start}
                {row.end > row.start ? `-${row.end}` : ''}
              </p>
            )}
            <div className="sbs-cell" data-side="a">
              <span className="cell-key" aria-hidden="true">A</span>
              {row.a.length > 0 ? (
                row.a.map((h) => <Hunk key={`${h.old_start}-${h.new_start}`} hunk={h} />)
              ) : (
                <p className="cell-empty">{file.a.status === 'unchanged' ? '' : 'A leaves these lines alone'}</p>
              )}
            </div>
            <div className="sbs-cell" data-side="b">
              <span className="cell-key" aria-hidden="true">B</span>
              {row.b.length > 0 ? (
                row.b.map((h) => <Hunk key={`${h.old_start}-${h.new_start}`} hunk={h} />)
              ) : (
                <p className="cell-empty">{file.b.status === 'unchanged' ? '' : 'B leaves these lines alone'}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
};

/**
 * Both branches' changes against the base, side by side: shared files
 * first, hunks lined up on the base file, regions both branches rewrote
 * marked. Loaded from POST /api/pair-diff when a pair is opened.
 */
export const PairDiff = ({ source, baseBranch, branchA, branchB }: PairDiffProps) => {
  const key = JSON.stringify([source.repo_path, source.repo_url, baseBranch, branchA, branchB]);
  const [load, setLoad] = useState<(Load & { key: string }) | null>(null);
  const [attempt, setAttempt] = useState(0);
  const hit = cache.get(key);

  useEffect(() => {
    if (cache.has(key)) return;
    const controller = new AbortController();
    fetchPairDiff(
      { ...source, base_branch: baseBranch, branch_a: branchA, branch_b: branchB, context: 3 },
      controller.signal
    )
      .then((diff) => {
        cache.set(key, diff);
        setLoad({ key, state: 'ready', diff });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setLoad({ key, state: 'error', message: err instanceof Error ? err.message : 'Unknown error' });
      });
    return () => controller.abort();
    // `key` covers every input; `attempt` re-runs a failed load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt]);

  const retry = () => {
    setLoad(null);
    setAttempt((n) => n + 1);
  };

  const current: Load = hit ? { state: 'ready', diff: hit } : load && load.key === key ? load : { state: 'loading' };

  if (current.state === 'loading') {
    return (
      <div className="diff-loading" role="status" data-testid="diff-loading">
        <span className="sr-only">Loading both diffs</span>
        <span className="skeleton" aria-hidden="true"></span>
        <span className="skeleton" aria-hidden="true"></span>
      </div>
    );
  }
  if (current.state === 'error') {
    // FastAPI answers an unknown route with exactly "Not Found"; a proxy with "404 ...".
    const missing = /^(not found|404\b.*)$/i.test(current.message.trim()) && !current.message.includes(':');
    return (
      <div className="inline-problem" role="alert" data-testid="diff-error">
        <p>
          <strong>Couldn&apos;t load the diffs.</strong>{' '}
          {missing ? 'This backend has no /api/pair-diff endpoint yet.' : current.message}
        </p>
        <button type="button" className="btn btn-quiet" onClick={retry}>
          Retry
        </button>
      </div>
    );
  }
  const files = [...current.diff.files].sort((x, y) => Number(y.shared) - Number(x.shared));
  if (files.length === 0) return <p className="muted">Neither branch changes any file.</p>;
  return (
    <div className="diff" data-testid="pair-diff">
      {files.map((file) => (
        <FileDiff key={file.path} file={file} branchA={branchA} branchB={branchB} />
      ))}
    </div>
  );
};
