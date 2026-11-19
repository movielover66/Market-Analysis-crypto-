import React from 'react';

export interface SettingsFeatureProps {
  data?: any;
  onAction?: (action: string, payload?: any) => void;
}

export const SettingsFeature: React.FC<SettingsFeatureProps> = ({ data, onAction }) => {
  return (
    <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-2" data-feature="settings">
      <div className="text-xs font-mono uppercase text-emerald-400">settings module</div>
      <div className="text-sm font-semibold text-slate-100">Configurable capital (₹50,000–₹1,00,000+), risk guardrails, effective-dated NSE cost profile, provider diagnostics</div>
      {data && (
        <pre className="text-xs font-mono text-slate-300 bg-slate-950 p-3 rounded border border-slate-800 overflow-x-auto">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
};
