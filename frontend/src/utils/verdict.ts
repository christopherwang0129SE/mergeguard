import type { AnalyzeResponse, PairRisk, RiskLevel } from '../types';
import { usableDocs } from './docs';
import {
  effectivePairLevel,
  isConfirmedByBranchTest,
  isConfirmedByIntentTest,
  isConfirmedByTest,
  levelRank,
} from './risk';

export type VerdictTone = 'proven' | 'risky' | 'clear' | 'empty';

export interface Verdict {
  tone: VerdictTone;
  /** Effective worst level across pairs, for the colour of the verdict. */
  level: RiskLevel;
  headline: string;
  detail: string;
}

const plural = (n: number, word: string, many = `${word}s`): string =>
  `${n} ${n === 1 ? word : many}`;

/** Tests that pass on their own branch and fail on the A+B merge. */
export const failingTestCount = (pair: PairRisk): number =>
  (isConfirmedByBranchTest(pair) ? pair.branch_tests?.confirming.length ?? 0 : 0) +
  (isConfirmedByIntentTest(pair) ? pair.verification?.confirming_tests?.length ?? 0 : 0);

/**
 * A pair git merges cleanly: no overlapping hunks and no textual merge
 * conflict when the tests merged it. A proven conflict here is invisible to
 * git, which is the case MergeGuard exists for.
 */
export const mergesCleanly = (pair: PairRisk): boolean =>
  pair.overlapping_files === 0 &&
  !pair.branch_tests?.merge_conflict &&
  !pair.verification?.merge_conflict;

/** Pairs that need a human look: proven by a test, or effective level medium+. */
export const needsAttention = (pair: PairRisk): boolean =>
  isConfirmedByTest(pair) || levelRank(effectivePairLevel(pair)) >= levelRank('medium');

const worst = (pairs: PairRisk[]): RiskLevel =>
  pairs.reduce<RiskLevel>((acc, pair) => {
    const level = effectivePairLevel(pair);
    return levelRank(level) > levelRank(acc) ? level : acc;
  }, 'none');

/** One-glance verdict for the results: what was found and how sure we are. */
export const verdictFor = (pairs: PairRisk[]): Verdict => {
  const total = pairs.length;
  if (total === 0) {
    return {
      tone: 'empty',
      level: 'none',
      headline: 'No branch pairs to compare',
      detail: 'MergeGuard needs at least two branches besides the base branch.',
    };
  }

  const proven = pairs.filter((pair) => isConfirmedByTest(pair));
  if (proven.length > 0) {
    const allHidden = proven.every(mergesCleanly);
    const headline = `${plural(proven.length, allHidden ? 'hidden conflict' : 'conflict')} proven by tests`;
    let detail: string;
    if (proven.length === 1) {
      const [pair] = proven;
      const failing = failingTestCount(pair);
      const tests = failing > 0 ? plural(failing, 'test') : 'Tests';
      const fail = failing === 1 ? 'fails' : 'fail';
      detail = mergesCleanly(pair)
        ? `${pair.branch_a} and ${pair.branch_b} merge without a git conflict, but ${tests} ${fail} on the merged code.`
        : `${tests} ${fail} when ${pair.branch_a} and ${pair.branch_b} are merged.`;
    } else {
      detail = `Each of these pairs has tests that pass on their own branch and fail after the merge. Checked ${plural(total, 'branch pair')}.`;
    }
    return { tone: 'proven', level: 'high', headline, detail };
  }

  const risky = pairs.filter(needsAttention);
  if (risky.length > 0) {
    return {
      tone: 'risky',
      level: worst(pairs),
      headline: `${plural(risky.length, 'risky pair')} to review before merging`,
      detail: `${
        pairs.some(testsRan)
          ? 'Flagged by the diff or by Nemotron, but no test failed on the merged code.'
          : 'Flagged by the diff or by Nemotron. No tests ran on the merged code.'
      } Checked ${plural(total, 'branch pair')}.`,
    };
  }

  return {
    tone: 'clear',
    level: worst(pairs),
    headline: 'No conflicts found',
    detail: `All ${plural(total, 'branch pair')} look safe to merge in any order.`,
  };
};

/** Short facts for the finished pipeline, one per step. */
export const pipelineFacts = (response: AnalyzeResponse): [string, string, string] => {
  const { pairs, usage } = response;
  const branches = response.branches.length;
  const diffed = `Diffed ${plural(branches, 'branch', 'branches')} against ${response.base_branch} and scored ${plural(pairs.length, 'pair')}.`;

  const sent = usage?.pairs_sent_to_model ?? pairs.filter((p) => p.score_source === 'model').length;
  const skipped = usage?.pairs_prefiltered ?? pairs.filter((p) => p.fallback_reason === 'prefiltered').length;
  const docs = usableDocs(pairs.flatMap((p) => p.docs_sources ?? [])).length;
  let explained =
    sent > 0
      ? `Reviewed ${plural(sent, 'pair')} that share code`
      : 'No pair shared enough code to need Nemotron';
  if (sent > 0 && docs > 0) explained += `, grounded with ${plural(docs, 'library doc')} via Tavily`;
  if (sent > 0 && skipped > 0) explained += `; skipped ${skipped} with nothing in common`;
  if (usage && usage.cache_hits > 0 && usage.llm_calls === 0) explained += ' (cached answers)';
  explained += '.';

  return [diffed, explained, testFact(pairs)];
};

/** True when pairs were picked for testing but no test ran on any of them. */
export const noTestsRan = (pairs: PairRisk[]): boolean => {
  const considered = pairs.filter((p) => p.branch_tests || p.verification);
  return considered.length > 0 && !considered.some(testsRan);
};

/** Did any test actually run for this pair, branch tests or intent tests? */
const testsRan = (pair: PairRisk): boolean =>
  isConfirmedByTest(pair) ||
  (pair.branch_tests?.runs.length ?? 0) > 0 ||
  (pair.verification?.runs.length ?? 0) > 0;

const sentence = (text: string): string => text.trim().replace(/\.$/, '');

/**
 * Step 3 as it happened: a pair the backend considered for testing may have
 * been skipped (repo outside the allowed roots, no tests) without running
 * anything, which must not read as "none failed".
 */
const testFact = (pairs: PairRisk[]): string => {
  const considered = pairs.filter((p) => p.branch_tests || p.verification);
  if (considered.length === 0) return 'No pair was risky enough to test.';

  const ran = considered.filter(testsRan).length;
  const proven = considered.filter((p) => isConfirmedByTest(p)).length;
  if (ran === 0) {
    const reasons = [
      ...new Set(
        considered
          .flatMap((p) => [p.verification?.reason, p.branch_tests?.reason])
          .filter((r): r is string => Boolean(r && r.trim()))
          .map(sentence)
      ),
    ];
    const skippedOnly = considered.every(
      (p) =>
        (!p.verification || p.verification.status === 'skipped') &&
        (!p.branch_tests || p.branch_tests.status === 'skipped' || p.branch_tests.status === 'unsupported')
    );
    if (reasons.length === 1) return skippedOnly ? `No tests ran. Skipped: ${reasons[0]}.` : `No tests ran: ${reasons[0]}.`;
    return skippedOnly ? 'No tests ran: testing was skipped.' : 'No tests ran: the test setup failed.';
  }

  let fact = `Tested ${plural(ran, 'pair')} on the merged code: ${proven > 0 ? `${proven} proven` : 'none failed'}`;
  if (considered.length > ran) fact += `; ${considered.length - ran} skipped`;
  return `${fact}.`;
};

/** What MergeGuard concluded about one pair, in one word. */
export type PairVerdict = 'confirmed' | 'suspected' | 'clean' | 'skipped';

/**
 * confirmed: a test passes on its own branch and fails on the merge.
 * suspected: the diff or Nemotron flags medium+ risk, no test proved it.
 * skipped: the pair shares code but Nemotron never reviewed it (pair cap,
 * model error) and the git score alone is low.
 * clean: nothing to act on.
 */
export const pairVerdict = (pair: PairRisk): PairVerdict => {
  if (isConfirmedByTest(pair)) return 'confirmed';
  if (needsAttention(pair)) return 'suspected';
  const unreviewed =
    pair.fallback_reason === 'pair_cap' ||
    pair.fallback_reason === 'model_error' ||
    pair.fallback_reason === 'unparsable';
  if (unreviewed && pair.shared_files > 0) return 'skipped';
  return 'clean';
};

export const VERDICT_TEXT: Record<PairVerdict, string> = {
  confirmed: 'Confirmed',
  suspected: 'Suspected',
  clean: 'Clean',
  skipped: 'Not reviewed',
};

/** Pairs worst first: confirmed, then by score. */
export const rankPairs = (pairs: PairRisk[]): PairRisk[] => {
  const order: Record<PairVerdict, number> = { confirmed: 0, suspected: 1, skipped: 2, clean: 3 };
  return [...pairs].sort(
    (a, b) => order[pairVerdict(a)] - order[pairVerdict(b)] || b.score - a.score || a.branch_a.localeCompare(b.branch_a)
  );
};

/**
 * The one sentence a judge reads first, e.g. "1 of 3 branch pairs breaks
 * when merged, confirmed by test."
 */
export const summaryLine = (pairs: PairRisk[]): string => {
  const total = pairs.length;
  if (total === 0) return 'No branch pairs to compare.';
  const confirmed = pairs.filter((p) => pairVerdict(p) === 'confirmed').length;
  const suspected = pairs.filter((p) => pairVerdict(p) === 'suspected').length;
  const of = `of ${plural(total, 'branch pair')}`;
  if (confirmed > 0) {
    const verb = confirmed === 1 ? 'breaks' : 'break';
    const more = suspected > 0 ? ` ${suspected} more ${suspected === 1 ? 'looks' : 'look'} risky.` : '';
    return `${confirmed} ${of} ${verb} when merged, confirmed by test.${more}`;
  }
  if (suspected > 0) {
    return `${suspected} ${of} may break when merged. No test has confirmed it.`;
  }
  return total === 1 ? 'The branch pair merges safely.' : `All ${total} branch pairs merge safely.`;
};
