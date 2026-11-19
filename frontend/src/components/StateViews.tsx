import React from 'react';
import { FreshnessClass } from '../lib/api';

const FRESHNESS_STYLES: Record<FreshnessClass, string> = {
  LIVE: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  DELAYED: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  HISTORICAL: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
  SIMULATED: 'bg-purple-500/20 text-purple-200 border-purple-400/50',
  STALE: 'bg-rose-500/20 text-rose-300 border-rose-500/40',
  UNAVAILABLE: 'bg-slate-800 text-slate-400 border-slate-700'
};

export const FreshnessChip: React.FC<{ freshness: FreshnessClass; source?: string }> = ({ freshness, source }) => (
  <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold border ${FRESHNESS_STYLES[freshness]}`}>
    <span>{freshness}</span>
    {source ? <span className="opacity-75">· {source}</span> : null}
  </span>
);

export const LoadingState: React.FC<{ label?: string }> = ({ label = 'Loading verified data…' }) => (
  <div className="rounded-xl bg-surface border border-border p-4 text-sm text-muted animate-pulse">
    {label}
  </div>
);

export const EmptyState: React.FC<{ title: string; subtitle?: string }> = ({ title, subtitle }) => (
  <div className="rounded-xl bg-surface border border-border p-5 text-center">
    <p className="font-semibold text-ink">{title}</p>
    {subtitle ? <p className="text-xs text-muted mt-1">{subtitle}</p> : null}
  </div>
);

export const ErrorState: React.FC<{ message: string; onRetry?: () => void }> = ({ message, onRetry }) => (
  <div className="rounded-xl bg-rose-950/30 border border-rose-500/40 p-4 text-sm text-rose-200">
    <p className="font-semibold">Data Unavailable / Error</p>
    <p className="text-xs mt-1">{message}</p>
    {onRetry ? (
      <button onClick={onRetry} className="touch-target mt-3 px-3 py-1.5 rounded-lg bg-rose-500/20 border border-rose-400/40 text-xs font-semibold">
        Retry
      </button>
    ) : null}
  </div>
);

/* Banner-style aliases used by the app shell (App.tsx). */
export const LoadingBanner: React.FC<{ message?: string }> = ({ message }) => <LoadingState label={message} />;

export const ErrorBanner: React.FC<{ error: string; onRetry?: () => void }> = ({ error, onRetry }) => (
  <ErrorState message={error} onRetry={onRetry} />
);

export const EmptyStateBanner: React.FC<{ title: string; subtitle?: string }> = ({ title, subtitle }) => (
  <EmptyState title={title} subtitle={subtitle} />
);
