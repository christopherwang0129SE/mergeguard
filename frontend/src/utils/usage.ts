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
  return parts.join(' · ');
};
