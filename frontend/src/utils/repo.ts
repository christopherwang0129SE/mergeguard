import type { AnalyzeRequest } from '../types';

/**
 * Classify a single repository input into either `repo_url` or `repo_path`.
 *
 * Rules:
 * - https / git@ (scp-style) / ssh:// values are remote Git URLs -> `repo_url`.
 * - Local paths (absolute, relative, or tilde) -> `repo_path`.
 * - Empty / whitespace-only input -> no field.
 *
 * The two fields are mutually exclusive: this function never sets both.
 */
export function classifyRepoInput(raw: string): Pick<AnalyzeRequest, 'repo_url' | 'repo_path'> {
  const value = raw.trim();
  if (!value) {
    return {};
  }

  const isRemoteUrl =
    value.startsWith('https://') ||
    value.startsWith('http://') ||
    value.startsWith('git@') ||
    value.startsWith('ssh://');

  if (isRemoteUrl) {
    return { repo_url: value };
  }
  return { repo_path: value };
}
