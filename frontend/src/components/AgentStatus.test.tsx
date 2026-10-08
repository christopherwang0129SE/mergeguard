import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AgentStatus } from './AgentStatus';
import type { HealthResponse } from '../types';

const health = (over: Partial<HealthResponse> = {}): HealthResponse => ({
  status: 'ok',
  nebius_ready: false,
  nebius_enabled: false,
  nebius_model: null,
  tavily_ready: false,
  ...over,
});

describe('AgentStatus', () => {
  it('shows Sentinel online with git only mode when Nebius is off', () => {
    render(<AgentStatus health={health({ nebius_ready: false, nebius_enabled: false })} />);
    expect(screen.getByText(/online/i)).toBeInTheDocument();
    expect(screen.getByText(/git only/i)).toBeInTheDocument();
  });

  it('shows Sentinel online with git+Nemotron mode and the model id when Nebius is ready', () => {
    render(
      <AgentStatus
        health={health({ nebius_ready: true, nebius_enabled: true, nebius_model: 'nemotron' })}
      />
    );
    expect(screen.getByText(/online/i)).toBeInTheDocument();
    expect(screen.getByText(/git\+Nemotron/i)).toBeInTheDocument();
    expect(screen.getByText(/nemotron/i)).toBeInTheDocument();
  });

  it('shows Sentinel offline when health is unavailable', () => {
    render(<AgentStatus health={null} />);
    expect(screen.getByText(/offline/i)).toBeInTheDocument();
  });

  it('shows Planner and Merger as soon chips', () => {
    render(<AgentStatus health={health()} />);
    expect(screen.getByText(/planner/i)).toBeInTheDocument();
    expect(screen.getByText(/merger/i)).toBeInTheDocument();
    expect(screen.getAllByText(/soon/i)).toHaveLength(2);
  });
});
