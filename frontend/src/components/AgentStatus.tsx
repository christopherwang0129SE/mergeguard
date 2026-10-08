import { sentinelMode, sentinelStatus } from '../utils/agent';
import type { HealthResponse } from '../types';

interface AgentStatusProps {
  health: HealthResponse | null;
}

export const AgentStatus = ({ health }: AgentStatusProps) => {
  const status = sentinelStatus(health);
  const mode = sentinelMode(health);
  const online = status === 'online';

  return (
    <div className="agent-status">
      <h2>Agent Status</h2>
      <div className="status-item">
        <strong>Sentinel:</strong>
        <span
          className="status-dot"
          style={{ backgroundColor: online ? '#10b981' : '#ef4444' }}
          aria-hidden="true"
        ></span>
        <span className="status-text">
          {online ? 'Online' : 'Offline'}
          {online ? ` · ${mode}` : ''}
          {online && health?.nebius_model ? ` · ${health.nebius_model}` : ''}
        </span>
      </div>
      <div className="status-item">
        <strong>Planner:</strong>
        <span className="status-text">Planned</span>
      </div>
      <div className="status-item">
        <strong>Merger:</strong>
        <span className="status-text">Planned</span>
      </div>
    </div>
  );
};
