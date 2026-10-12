import { sentinelMode, sentinelStatus } from '../utils/agent';
import type { HealthResponse } from '../types';

interface AgentStatusProps {
  health: HealthResponse | null;
}

/**
 * Compact agent strip for the header: Sentinel (live, from /api/health) plus
 * Planner and Merger as "soon" chips. The model id is shown once, in mono.
 */
export const AgentStatus = ({ health }: AgentStatusProps) => {
  const status = sentinelStatus(health);
  const mode = sentinelMode(health);
  const online = status === 'online';

  return (
    <div className="agent-strip" aria-label="Agent status">
      <span className="agent-sentinel">
        <span
          className="status-dot"
          style={{ backgroundColor: online ? '#10b981' : '#ef4444' }}
          aria-hidden="true"
        ></span>
        <span className="agent-name">Sentinel {online ? 'online' : 'offline'}</span>
        {online && (
          <span className="agent-mode">
            {mode}
            {health?.nebius_model ? ` · ${health.nebius_model}` : ''}
          </span>
        )}
      </span>
      <span className="agent-soon">Planner · soon</span>
      <span className="agent-soon">Merger · soon</span>
    </div>
  );
};
