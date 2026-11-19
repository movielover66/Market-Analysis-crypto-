import React from 'react';

export interface RiskCalculatorFeatureProps {
  data?: any;
  onAction?: (action: string, payload?: any) => void;
}

export const RiskCalculatorFeature: React.FC<RiskCalculatorFeatureProps> = ({ data, onAction }) => {
  return (
    <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-2" data-feature="risk">
      <div className="text-xs font-mono uppercase text-emerald-400">risk module</div>
      <div className="text-sm font-semibold text-slate-100">Decimal capital, stop, quantity, itemized NSE charges, exposure cap, daily/weekly risk lock</div>
      {data && (
        <pre className="text-xs font-mono text-slate-300 bg-slate-950 p-3 rounded border border-slate-800 overflow-x-auto">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
};
