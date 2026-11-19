import React from 'react';

export interface PaperTradingFeatureProps {
  data?: any;
  onAction?: (action: string, payload?: any) => void;
}

export const PaperTradingFeature: React.FC<PaperTradingFeatureProps> = ({ data, onAction }) => {
  return (
    <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-2" data-feature="paper">
      <div className="text-xs font-mono uppercase text-emerald-400">paper module</div>
      <div className="text-sm font-semibold text-slate-100">Virtual capital ledger, open positions, virtual orders, simulated fills, realised/unrealised P&L</div>
      {data && (
        <pre className="text-xs font-mono text-slate-300 bg-slate-950 p-3 rounded border border-slate-800 overflow-x-auto">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
};
