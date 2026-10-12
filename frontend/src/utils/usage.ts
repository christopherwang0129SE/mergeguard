import type { UsageSummary } from '../types';

/** "$0.0007" style; costs here are fractions of a cent. */
export const formatCost = (usd: number): string => `$${usd.toFixed(4)}`;

/** One-line LLM cost summary: calls, tokens, estimated $, cache, prefilter. */
export const usageLine = (usage: UsageSummary): string => {
  const parts = [
    `${usage.llm_calls} LLM call${usage.llm_calls === 1 ? '' : 's'}`,
    `${usage.total_tokens.toLocaleString('en-US')} tokens`,
    `${formatCost(usage.estimated_cost_usd)} est.`,
  ];
  if (usage.cache_hits > 0) {
    parts.push(`${usage.cache_hits} cached`);
  }
  if (usage.pairs_prefiltered > 0) {
    parts.push(`${usage.pairs_prefiltered} filtered before LLM`);
  }
  if (usage.verify_pairs && usage.verify_pairs > 0) {
    parts.push(`${usage.verify_confirmed ?? 0}/${usage.verify_pairs} confirmed by intent test`);
  }
  if (usage.branch_test_pairs && usage.branch_test_pairs > 0) {
    parts.push(
      `${usage.branch_test_confirmed ?? 0}/${usage.branch_test_pairs} confirmed by branch test`
    );
  }
  return parts.join(' · ');
};
