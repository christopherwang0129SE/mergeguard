import { describe, it, expect } from 'vitest';
import { sentinelStatus, sentinelMode } from './agent';
import type { HealthResponse } from '../types';

const baseHealth: HealthResponse = {
  status: 'ok',
  nebius_ready: false,
  nebius_enabled: false,
  nebius_model: null,
  tavily_ready: false,
};

describe('sentinelStatus', () => {
  it('is online when health responds', () => {
    expect(sentinelStatus(baseHealth)).toBe('online');
  });

  it('is online even when Nebius is not ready (git scoring still works)', () => {
    expect(sentinelStatus({ ...baseHealth, nebius_ready: false, nebius_enabled: false })).toBe(
      'online'
    );
  });

  it('is offline when health is unavailable', () => {
    expect(sentinelStatus(null)).toBe('offline');
  });
});

describe('sentinelMode', () => {
  it('is "git only" when Nebius is neither ready nor enabled', () => {
    expect(sentinelMode({ ...baseHealth, nebius_ready: false, nebius_enabled: false })).toBe(
      'git only'
    );
  });

  it('is "git+Nemotron" when nebius_ready is true', () => {
    expect(sentinelMode({ ...baseHealth, nebius_ready: true, nebius_enabled: true })).toBe(
      'git+Nemotron'
    );
  });

  it('is "git+Nemotron" when nebius_enabled is true', () => {
    expect(sentinelMode({ ...baseHealth, nebius_ready: false, nebius_enabled: true })).toBe(
      'git+Nemotron'
    );
  });

  it('falls back to "git only" when health is unavailable', () => {
    expect(sentinelMode(null)).toBe('git only');
  });
});
