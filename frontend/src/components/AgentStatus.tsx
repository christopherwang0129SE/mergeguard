interface AgentStatusProps {
  health: any; // from /api/health
}

export const AgentStatus = ({ health }: AgentStatusProps) => {
  if (!health) {
    return (
      <div className="agent-status">
        <h2>Agent Status</h2>
        <p>Loading health...</p>
      </div>
    );
  }

  const sentinelStatus = health.nebius_ready && health.nebius_enabled ? 'Live' : 'Offline';
  const sentinelColor = health.nebius_ready && health.nebius_enabled ? '#10b981' : '#ef4444';

  return (
    <div className="agent-status">
      <h2>Agent Status</h2>
      <div className="status-item">
        <strong>Sentinel:</strong>
        <span
          style={{
            display: 'inline-block',
            width: '12px',
            height: '12px',
            backgroundColor: sentinelColor,
            marginLeft: '8px',
          }}
        ></span>
        {sentinelStatus} (Nebius: {health.nebius_ready ? 'ready' : 'not ready'}, Model: {health.nebius_model ?? 'none'})
      </div>
      <div className="status-item">
        <strong>Planner:</strong> Planned
      </div>
      <div className="status-item">
        <strong>Merger:</strong> Planned
      </div>
    </div>
  );
};