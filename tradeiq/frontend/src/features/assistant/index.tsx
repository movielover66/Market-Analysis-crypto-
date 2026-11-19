import React from 'react';

export interface AssistantFeatureProps {
  data?: any;
  onAction?: (action: string, payload?: any) => void;
}

export const AssistantFeature: React.FC<AssistantFeatureProps> = ({ data, onAction }) => {
  return (
    <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-2" data-feature="assistant">
      <div className="text-xs font-mono uppercase text-emerald-400">assistant module</div>
      <div className="text-sm font-semibold text-slate-100">Grounded explanations & fixed read-only tools: get_analysis, get_plan, calc_quantity, get_journal_stats</div>
      {data && (
        <pre className="text-xs font-mono text-slate-300 bg-slate-950 p-3 rounded border border-slate-800 overflow-x-auto">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
};
