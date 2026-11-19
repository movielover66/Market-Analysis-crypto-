import React from 'react';

export interface BacktestFeatureProps {
  data?: any;
  onAction?: (action: string, payload?: any) => void;
}

export const BacktestFeature: React.FC<BacktestFeatureProps> = ({ data, onAction }) => {
  return (
    <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-2" data-feature="backtest">
      <div className="text-xs font-mono uppercase text-emerald-400">backtest module</div>
      <div className="text-sm font-semibold text-slate-100">Bar-by-bar execution, next-bar-open fill, stop-first conflict, gap-through-stop, IS/OOS split</div>
      {data && (
        <pre className="text-xs font-mono text-slate-300 bg-slate-950 p-3 rounded border border-slate-800 overflow-x-auto">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
};
