import React, { useState } from 'react';
import { SECTION_20_PAGES } from '../pages/PagesRegistry';

export interface LayoutProps {
  activeTab: string;
  onSelectTab: (tab: string) => void;
  source: string;
  freshness: string;
  asOfUtc: string;
  marketSession: string;
  /** When provided, a Sign out control is shown in the header. */
  onLogout?: () => void;
  children: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({
  activeTab,
  onSelectTab,
  source,
  freshness,
  asOfUtc,
  marketSession,
  onLogout,
  children,
}) => {
  const [moreOpen, setMoreOpen] = useState(false);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col overflow-x-hidden">
      {/* Top Provenance & Market State Bar (Section 19) */}
      <header className="sticky top-0 z-30 bg-slate-900/95 backdrop-blur border-b border-slate-800 px-4 py-2.5 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <span className="text-base font-extrabold tracking-tight text-emerald-400">TRADEIQ</span>
          <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-amber-500/20 text-amber-300 border border-amber-500/40">
            DEMO MODE • PAPER ONLY
          </span>
          <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-slate-800 text-slate-200">
            NSE SESSION: {marketSession}
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs font-mono">
          <span className="px-2 py-0.5 rounded bg-slate-800 text-sky-300">SOURCE: {source}</span>
          <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300">FRESHNESS: {freshness}</span>
          <span className="text-slate-400 hidden sm:inline">AS OF: {asOfUtc}</span>
          {onLogout && (
            <button
              onClick={onLogout}
              className="touch-target px-3 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium border border-slate-700"
            >
              Sign out
            </button>
          )}
        </div>
      </header>

      <div className="flex-1 flex">
        {/* Desktop Sidebar with all 17 Pages from Section 20 */}
        <aside className="hidden md:flex md:w-64 flex-col bg-slate-900 border-r border-slate-800 p-3 space-y-1">
          <div className="px-2 py-1.5 text-[11px] font-mono uppercase text-slate-400">
            Workspace Navigation (17 Pages)
          </div>
          {SECTION_20_PAGES.map((p) => (
            <button
              key={p.id}
              onClick={() => onSelectTab(p.id)}
              className={`w-full text-left px-3 py-2 rounded-lg text-xs font-medium min-h-[44px] flex items-center justify-between transition ${
                activeTab === p.id
                  ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                  : 'text-slate-300 hover:bg-slate-800/60'
              }`}
            >
              <span>{p.title}</span>
            </button>
          ))}
        </aside>

        {/* Main Content Area */}
        <main className="flex-1 p-4 pb-24 md:pb-8 max-w-7xl mx-auto w-full">{children}</main>
      </div>

      {/* Section 19 Mobile Bottom Navigation (5 items: Home, Scanner, Planner, Paper, More; >= 44px touch targets) */}
      <nav
        aria-label="Mobile bottom navigation"
        className="md:hidden fixed bottom-0 inset-x-0 z-40 bg-slate-900 border-t border-slate-800 grid grid-cols-5"
      >
        <button
          onClick={() => { setMoreOpen(false); onSelectTab('dashboard'); }}
          className={`min-h-[48px] text-xs font-medium flex flex-col items-center justify-center ${
            activeTab === 'dashboard' ? 'text-emerald-400' : 'text-slate-300'
          }`}
        >
          Home
        </button>
        <button
          onClick={() => { setMoreOpen(false); onSelectTab('scanner'); }}
          className={`min-h-[48px] text-xs font-medium flex flex-col items-center justify-center ${
            activeTab === 'scanner' ? 'text-emerald-400' : 'text-slate-300'
          }`}
        >
          Scanner
        </button>
        <button
          onClick={() => { setMoreOpen(false); onSelectTab('planner'); }}
          className={`min-h-[48px] text-xs font-medium flex flex-col items-center justify-center ${
            activeTab === 'planner' ? 'text-emerald-400' : 'text-slate-300'
          }`}
        >
          Planner
        </button>
        <button
          onClick={() => { setMoreOpen(false); onSelectTab('paper'); }}
          className={`min-h-[48px] text-xs font-medium flex flex-col items-center justify-center ${
            activeTab === 'paper' ? 'text-emerald-400' : 'text-slate-300'
          }`}
        >
          Paper
        </button>
        <button
          onClick={() => setMoreOpen(!moreOpen)}
          className={`min-h-[48px] text-xs font-medium flex flex-col items-center justify-center ${
            moreOpen ? 'text-emerald-400' : 'text-slate-300'
          }`}
        >
          More
        </button>
      </nav>

      {/* Mobile "More" Drawer linking to Journal, Backtesting, Watchlists, Alerts, News, Analytics, Settings, etc. */}
      {moreOpen && (
        <div className="md:hidden fixed inset-x-0 bottom-12 z-40 bg-slate-900 border-t border-slate-700 p-3 grid grid-cols-2 gap-2 max-h-[65vh] overflow-y-auto">
          {SECTION_20_PAGES.map((p) => (
            <button
              key={p.id}
              onClick={() => {
                setMoreOpen(false);
                onSelectTab(p.id);
              }}
              className="min-h-[44px] px-3 py-2 rounded bg-slate-800 text-left text-xs text-slate-200 hover:bg-slate-700"
            >
              {p.title}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};
