import React from 'react';

export interface DashboardFeatureProps {
  data?: any;
  onAction?: (action: string, payload?: any) => void;
}

export const DashboardFeature: React.FC<DashboardFeatureProps> = ({ data, onAction }) => {
  return (
    <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-2" data-feature="dashboard">
      <div className="text-xs font-mono uppercase text-emerald-400">dashboard module</div>
      <div className="text-sm font-semibold text-slate-100">Market state in IST, index snapshots, breadth, sector overview, open plans, risk-lock status</div>
      {data && (
        <pre className="text-xs font-mono text-slate-300 bg-slate-950 p-3 rounded border border-slate-800 overflow-x-auto">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
};
