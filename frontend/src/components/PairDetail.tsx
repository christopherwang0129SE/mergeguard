import type { Ref } from 'react';
import type { AnalyzeRequest, PairRisk, UsageSummary } from '../types';
import { isFloorRule, isHiddenConflict, levelToText } from '../utils/risk';
import { VERDICT_TEXT, pairVerdict } from '../utils/verdict';
import { usableDocs } from '../utils/docs';
import { formatCost } from '../utils/usage';
import { splitSymbolEvidence } from '../utils/evidence';
import { RiskGauge } from './RiskGauge';
import { PairDiff } from './PairDiff';
import { VerificationDetails } from './VerificationDetails';
import { BranchTestDetails } from './BranchTestDetails';
import { DocsSources } from './DocsSources';

interface PairDetailProps {
  pair: PairRisk;
  source: Pick<AnalyzeRequest, 'repo_path' | 'repo_url'>;
  baseBranch: string;
  usage?: UsageSummary | null;
  /** True while the stream may still update this pair. */
  provisional?: boolean;
  headingRef?: Ref<HTMLHeadingElement>;
}

const VERDICT_LINE: Record<ReturnType<typeof pairVerdict>, string> = {
  confirmed: 'A test passes on each branch and fails once they are merged.',
  suspected: 'Flagged by the diff or by Nemotron. No test has failed on the merge.',
  clean: 'Nothing here needs attention.',
  skipped: 'These branches share code, but Nemotron did not review the pair in this run.',
};

const FALLBACK_TEXT: Record<string, string> = {
  no_api_key: 'no Nebius API key on the backend',
  disabled: 'Nemotron is turned off',
  prefiltered: 'the branches share no files or functions',
  pair_cap: 'more pairs than the per-run model limit',
  model_error: 'the model call failed',
  unparsable: 'the model answer was unreadable',
};

/** "raised: confirmed by test" + blended 44 -> "Raised from 44: confirmed by test". */
const scoreNote = (pair: PairRisk): string => {
  const note = pair.score_note ?? '';
  const match = /^(raised|lowered):\s*(.*)$/i.exec(note);
  if (match && pair.blended_score !== null && pair.blended_score !== undefined) {
    const verb = match[1].charAt(0).toUpperCase() + match[1].slice(1).toLowerCase();
    return `${verb} from ${Math.round(pair.blended_score)}: ${match[2]}`;
  }
  return note;
};

/** Why the pair scored as it did, from the source that set the score. */
const Explanation = ({ pair }: { pair: PairRisk }) => {
  const fromModel = pair.score_source === 'model';
  const text = fromModel ? pair.model_rationale ?? pair.rationale : pair.rationale;
  return (
    <section className="explain" aria-labelledby="explain-heading">
      <h3 id="explain-heading" className="explain-title">
        {fromModel ? 'Nemotron’s reading' : 'Git heuristic'}
      </h3>
      <p className="explain-text">{text}</p>
      {isHiddenConflict(pair) && pairVerdict(pair) !== 'confirmed' && (
        <p className="explain-note">
          Nemotron rates this {pair.model_level} risk while the diff alone looks{' '}
          {levelToText(pair.level).toLowerCase()}: a conflict git cannot see.
        </p>
      )}
      {isFloorRule(pair) && (
        <p className="explain-note">
          Both branches edit the same lines, so the score stays at the git level ({Math.round(pair.git_score ?? 0)})
          even though Nemotron scored it {Math.round(pair.model_score ?? 0)}.
        </p>
      )}
      {!fromModel && pair.fallback_reason && FALLBACK_TEXT[pair.fallback_reason] && (
        <p className="explain-note">Nemotron skipped this pair: {FALLBACK_TEXT[pair.fallback_reason]}.</p>
      )}
      {fromModel && pair.git_rationale && <p className="explain-aside">Git heuristic: {pair.git_rationale}</p>}
      {(pair.static_breaks?.length ?? 0) > 0 && (
        <ul className="explain-list" aria-label="Static signature check">
          {pair.static_breaks?.map((line) => (
            <li key={line}>Static check: {line}</li>
          ))}
        </ul>
      )}
    </section>
  );
};

/** Functions one branch changes and the other relies on. */
const Symbols = ({ pair }: { pair: PairRisk }) => {
  const lines = pair.symbol_evidence ?? [];
  if (lines.length === 0) return null;
  return (
    <ul className="symbols" aria-label="Functions both branches touch">
      {lines.map((line) => {
        const { symbol, detail } = splitSymbolEvidence(line);
        return (
          <li key={line}>
            {symbol && <code className="symbol">{symbol}</code>} <span>{detail}</span>
          </li>
        );
      })}
    </ul>
  );
};

/** Tokens this pair used (risk call plus test writing) and their estimated price. */
const Cost = ({ pair, usage }: { pair: PairRisk; usage?: UsageSummary | null }) => {
  const tokensIn = (pair.prompt_tokens ?? 0) + (pair.verification?.prompt_tokens ?? 0);
  const tokensOut = (pair.completion_tokens ?? 0) + (pair.verification?.completion_tokens ?? 0);
  if (tokensIn + tokensOut === 0) {
    return pair.model_cached ? (
      <section className="block block-cost" aria-labelledby="cost-heading">
        <h3 id="cost-heading" className="block-title">
          Model usage for this pair
        </h3>
        <p className="muted">Answered from the cache: no tokens spent in this run.</p>
      </section>
    ) : null;
  }
  const cost = usage
    ? (tokensIn * usage.input_usd_per_1m + tokensOut * usage.output_usd_per_1m) / 1_000_000
    : null;
  return (
    <section className="block block-cost" aria-labelledby="cost-heading">
      <h3 id="cost-heading" className="block-title">
        Model usage for this pair
      </h3>
      <dl className="figures">
        <div>
          <dt>Tokens in</dt>
          <dd className="tabular">{tokensIn.toLocaleString('en-US')}</dd>
        </div>
        <div>
          <dt>Tokens out</dt>
          <dd className="tabular">{tokensOut.toLocaleString('en-US')}</dd>
        </div>
        {cost !== null && (
          <div>
            <dt>Estimated cost</dt>
            <dd className="tabular">{formatCost(cost)}</dd>
          </div>
        )}
        {pair.model_cached && (
          <div>
            <dt>Risk call</dt>
            <dd>from cache</dd>
          </div>
        )}
      </dl>
    </section>
  );
};

/**
 * One branch pair in full: verdict and score, the explanation, both diffs
 * side by side, the test evidence, the docs Nemotron read and what it cost.
 */
export const PairDetail = ({ pair, source, baseBranch, usage, provisional = false, headingRef }: PairDetailProps) => {
  const verdict = pairVerdict(pair);
  const awaitingVerdict = provisional && verdict === 'clean';
  const docs = usableDocs([...(pair.docs_sources ?? []), ...(pair.verification?.docs_sources ?? [])]);
  return (
    <article className="detail" data-verdict={awaitingVerdict ? 'pending' : verdict} aria-labelledby="detail-heading" data-testid="pair-detail">
      <header className="detail-head">
        <h2 id="detail-heading" className="detail-pair" ref={headingRef} tabIndex={-1}>
          <span className="pair-side">
            <span className="side-key" aria-hidden="true">A</span>
            <span className="pair-branch">{pair.branch_a}</span>
          </span>
          <span className="sr-only"> and </span>
          <span className="pair-side">
            <span className="side-key" aria-hidden="true">B</span>
            <span className="pair-branch">{pair.branch_b}</span>
          </span>
        </h2>
        <p className="detail-verdict">
          <span className="verdict-word">{awaitingVerdict ? 'Awaiting verdict' : VERDICT_TEXT[verdict]}</span>
          <span className="verdict-why">{awaitingVerdict ? 'Risk scoring and test checks are still running.' : VERDICT_LINE[verdict]}</span>
        </p>
        {provisional && <p className="detail-provisional">Still running: this pair may change when the tests finish.</p>}
      </header>

      <div className="detail-top">
        <div className="detail-gauge">
          <RiskGauge pair={pair} />
          {pair.score_note && (
            <p className="gauge-note" data-testid="score-note">
              {scoreNote(pair)}
            </p>
          )}
        </div>
        <Explanation pair={pair} />
      </div>

      <section className="block" aria-labelledby="diff-heading">
        <h3 id="diff-heading" className="block-title">
          Both diffs <span className="block-sub">against {baseBranch}, lined up on the base file</span>
        </h3>
        <div className="block-body">
          <Symbols pair={pair} />
          <PairDiff source={source} baseBranch={baseBranch} branchA={pair.branch_a} branchB={pair.branch_b} />
        </div>
      </section>

      {pair.verification && (
        <VerificationDetails verification={pair.verification} branchA={pair.branch_a} branchB={pair.branch_b} />
      )}
      {pair.branch_tests && (
        <BranchTestDetails branchTests={pair.branch_tests} branchA={pair.branch_a} branchB={pair.branch_b} />
      )}
      <DocsSources sources={docs} />
      <Cost pair={pair} usage={usage} />
    </article>
  );
};
