import { describe, it, expect } from 'vitest';
import { classifyRepoInput } from './repo';

describe('classifyRepoInput', () => {
  it('routes https URLs to repo_url', () => {
    expect(classifyRepoInput('https://github.com/foo/bar.git')).toEqual({
      repo_url: 'https://github.com/foo/bar.git',
    });
  });

  it('routes git@ (scp-style) URLs to repo_url', () => {
    expect(classifyRepoInput('git@github.com:foo/bar.git')).toEqual({
      repo_url: 'git@github.com:foo/bar.git',
    });
  });

  it('routes ssh:// URLs to repo_url', () => {
    expect(classifyRepoInput('ssh://git@github.com/foo/bar.git')).toEqual({
      repo_url: 'ssh://git@github.com/foo/bar.git',
    });
  });

  it('routes local absolute paths to repo_path', () => {
    expect(classifyRepoInput('/Users/dima/mergeguard')).toEqual({
      repo_path: '/Users/dima/mergeguard',
    });
  });

  it('routes relative paths to repo_path', () => {
    expect(classifyRepoInput('./my-repo')).toEqual({ repo_path: './my-repo' });
  });

  it('routes tilde paths to repo_path', () => {
    expect(classifyRepoInput('~/repos/mergeguard')).toEqual({
      repo_path: '~/repos/mergeguard',
    });
  });

  it('trims surrounding whitespace', () => {
    expect(classifyRepoInput('  https://github.com/foo/bar.git  ')).toEqual({
      repo_url: 'https://github.com/foo/bar.git',
    });
  });

  it('returns no fields for empty input', () => {
    expect(classifyRepoInput('')).toEqual({});
    expect(classifyRepoInput('   ')).toEqual({});
  });

  it('never sets both repo_url and repo_path', () => {
    const samples = [
      'https://github.com/foo/bar.git',
      'git@github.com:foo/bar.git',
      'ssh://git@github.com/foo/bar.git',
      '/Users/dima/mergeguard',
      './my-repo',
      '~/repos/mergeguard',
    ];
    for (const sample of samples) {
      const result = classifyRepoInput(sample);
      const hasUrl = result.repo_url !== undefined;
      const hasPath = result.repo_path !== undefined;
      expect(hasUrl && hasPath).toBe(false);
      expect(hasUrl || hasPath).toBe(true);
    }
  });
});
