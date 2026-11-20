import React, { useCallback, useEffect, useState } from 'react';
import { TechnicalChartCanvas } from '../charts/TechnicalChartCanvas';
import {
  apiGet,
  apiPost,
  apiPut,
  fetchDemoOverview,
  getAuthToken,
  getTokenExpiryMs,
  ApiError,
  isUnauthorizedError,
  loginOwner,
  logout,
  onUnauthorized,
  restoreSession,
} from '../lib/api';

type TabKey =
  | 'dashboard'
  | 'autotrader'
  | 'charts'
  | 'engines'
  | 'positions'
  | 'accuracy'
  | 'news'
  | 'planner'
  | 'assistant'
  | 'journal'
  | 'system';

const MENU_TABS: { key: TabKey; label: string }[] = [
  { key: 'dashboard', label: 'Dashboard' },
  { key: 'autotrader', label: 'Auto-Trader' },
  { key: 'charts', label: 'Live Charts' },
  { key: 'engines', label: '3-Engine Scanner' },
  { key: 'positions', label: 'Paper Positions' },
  { key: 'accuracy', label: 'Accuracy & P&L' },
  { key: 'news', label: 'Market News' },
  { key: 'planner', label: 'Planner & Risk' },
  { key: 'assistant', label: 'AI Assistant' },
  { key: 'journal', label: 'Journal & Backtest' },
  { key: 'system', label: 'System & Settings' },
];

const fmtInr = (val: string | number | null | undefined, signed = false) => {
  const n = Number(val ?? 0);
  if (!Number.isFinite(n)) return '₹0.00';
  const absStr = Math.abs(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n < 0) return `-₹${absStr}`;
  if (signed && n > 0) return `+₹${absStr}`;
  return `₹${absStr}`;
};

const pnlColor = (val: string | number | null | undefined) => {
  const n = Number(val ?? 0);
  if (n > 0) return 'text-emerald-400';
  if (n < 0) return 'text-rose-400';
  return 'text-slate-300';
};

const errMsg = (ex: unknown, fallback: string): string => {
  if (ex instanceof ApiError) return ex.message;
  if (ex instanceof Error && ex.message) return ex.message;
  return fallback;
};

function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
      </svg>
      {label && <span>{label}</span>}
    </span>
  );
}

function Badge({ children, tone = 'slate' }: { children: React.ReactNode; tone?: 'emerald' | 'rose' | 'amber' | 'sky' | 'slate' }) {
  const cls: Record<string, string> = {
    emerald: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
    rose: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
    amber: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
    sky: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
    slate: 'bg-slate-800/80 text-slate-300 border-slate-700',
  };
  return <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border ${cls[tone]}`}>{children}</span>;
}

function Card({ title, subtitle, action, children }: { title?: string; subtitle?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-slate-800/90 bg-slate-900/75 backdrop-blur-md p-4 shadow-lg shadow-black/20">
      {(title || action) && (
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <div>
            {title && <h3 className="text-sm font-semibold text-slate-100 tracking-tight">{title}</h3>}
            {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

function LoginScreen({ notice, onSignedIn }: { notice: string | null; onSignedIn: () => void }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password) return setErr('Enter your owner password.');
    setBusy(true);
    setErr(null);
    try {
      await loginOwner(password);
      setPassword('');
      onSignedIn();
    } catch (ex) {
      setErr(errMsg(ex, 'Unable to reach TRADEIQ server.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center p-4">
      <div className="w-full max-w-md rounded-3xl border border-slate-800 bg-gradient-to-b from-slate-900 to-slate-950 p-7 shadow-2xl">
        <div className="flex items-center justify-between mb-6">
          <div>
            <div className="text-xl font-bold tracking-tight text-white">TRADEIQ <span className="text-sky-400">v2.0</span></div>
            <div className="text-xs text-slate-400 mt-0.5">3-Engine Live Auto Paper-Trading Terminal</div>
          </div>
          <Badge tone="sky">OWNER ONLY</Badge>
        </div>
        {notice && <div className="mb-4 rounded-xl bg-amber-500/10 border border-amber-500/30 p-3 text-xs text-amber-200">{notice}</div>}
        {err && <div className="mb-4 rounded-xl bg-rose-500/10 border border-rose-500/30 p-3 text-xs text-rose-200">{err}</div>}
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="block text-xs text-slate-400 mb-1.5">Owner Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter password to unlock terminal"
              autoFocus
              className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-2.5 text-sm text-white focus:border-sky-500 focus:outline-none"
            />
          </div>
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-xl bg-sky-500 hover:bg-sky-400 disabled:opacity-50 py-2.5 text-sm font-semibold text-slate-950 transition"
          >
            {busy ? 'Unlocking Terminal...' : 'Unlock Live Workspace'}
          </button>
        </form>
        <p className="mt-5 text-[11px] text-slate-500 text-center">
          Paper-Trading &amp; Analytical Platform Only — Zero real-money broker orders.
        </p>
      </div>
    </div>
  );
}

function AuthenticatedApp({ onSignOut }: { onSignOut: () => void }) {
  const signedIn = true;
  const [activeTab, setActiveTab] = useState<TabKey>('dashboard');
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [wsLive, setWsLive] = useState(false);
  const [lastTickIst, setLastTickIst] = useState<string>('');

  // Live core state
  const [indices, setIndices] = useState<any[]>([]);
  const [overview, setOverview] = useState<any>(null);
  const [ledgerData, setLedgerData] = useState<any>(null);
  const [autoStatus, setAutoStatus] = useState<any>(null);
  const [engineScan, setEngineScan] = useState<any>(null);
  const [newsData, setNewsData] = useState<any>(null);
  const [newsSymbol, setNewsSymbol] = useState('');
  const [integrations, setIntegrations] = useState<any>(null);
  const [connectivity, setConnectivity] = useState<any>(null);

  // Chart & Depth state
  const [chartSymbol, setChartSymbol] = useState('RELIANCE');
  const [chartTf, setChartTf] = useState('15m');
  const [depthData, setDepthData] = useState<any>(null);
  const [chartCandles, setChartCandles] = useState<any[]>([]);
  const [chartIndicators, setChartIndicators] = useState<any>(undefined);

  // Auto-Trader Config form
  const [cfgBalance, setCfgBalance] = useState('100000');
  const [cfgPerTrade, setCfgPerTrade] = useState('20000');
  const [cfgMaxPos, setCfgMaxPos] = useState('4');
  const [cfgDailyLoss, setCfgDailyLoss] = useState('1.5');
  const [cfgEnabled, setCfgEnabled] = useState(true);
  const [cfgE1, setCfgE1] = useState(true);
  const [cfgE2a, setCfgE2a] = useState(true);
  const [cfgE2b, setCfgE2b] = useState(true);
  const [cfgNewsShield, setCfgNewsShield] = useState(true);

  // Planner / Risk / AI / Journal / Backtest
  const [planSymbol, setPlanSymbol] = useState('RELIANCE');
  const [planMode, setPlanMode] = useState('SWING');
  const [planResult, setPlanResult] = useState<any>(null);
  const [aiQuestion, setAiQuestion] = useState('Why did RELIANCE pass or fail?');
  const [aiReply, setAiReply] = useState<any>(null);
  const [morningBrief, setMorningBrief] = useState<any>(null);
  const [btSymbol, setBtSymbol] = useState('RELIANCE');
  const [btResult, setBtResult] = useState<any>(null);
  const [journalList, setJournalList] = useState<any[]>([]);
  const [journalNote, setJournalNote] = useState('');

  const notify = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast((cur) => (cur === msg ? null : cur)), 4500);
  };

  useEffect(() => {
    return onUnauthorized(() => onSignOut());
  }, []);

  useEffect(() => {
    if (!signedIn) return;
    const exp = getTokenExpiryMs();
    if (!exp) return;
    const ms = exp - Date.now();
    if (ms <= 0) {
      logout();
      onSignOut();
      return;
    }
    const t = setTimeout(() => {
      logout();
      onSignOut();
    }, ms);
    return () => clearTimeout(t);
  }, [signedIn]);

  const loadCore = useCallback(async () => {
    if (!signedIn) return;
    try {
      const [idxRes, ovRes, ledRes, atRes, connRes] = await Promise.all([
        apiGet<any>('/api/v1/market/indices'),
        fetchDemoOverview(),
        apiGet<any>('/api/v1/paper/ledger'),
        apiGet<any>('/api/v1/auto-trader/status'),
        apiGet<any>('/api/v1/system/connectivity'),
      ]);
      setIndices(idxRes?.indices || []);
      setOverview(ovRes);
      setLedgerData(ledRes);
      setAutoStatus(atRes);
      setConnectivity(connRes);
      if (atRes?.config) {
        setCfgBalance(String(atRes.config.starting_balance_inr));
        setCfgPerTrade(String(atRes.config.per_trade_amount_inr));
        setCfgMaxPos(String(atRes.config.max_open_positions));
        setCfgDailyLoss(String(atRes.config.daily_loss_limit_pct));
        setCfgEnabled(Boolean(atRes.config.enabled));
        setCfgE1(Boolean(atRes.config.enable_engine_1_orb));
        setCfgE2a(Boolean(atRes.config.enable_engine_2a_vcp));
        setCfgE2b(Boolean(atRes.config.enable_engine_2b_rsi2));
        setCfgNewsShield(Boolean(atRes.config.enable_news_shield));
      }
    } catch (ex) {
      if (!isUnauthorizedError(ex)) notify(errMsg(ex, 'Live feed refresh warning'));
    }
  }, [signedIn]);

  useEffect(() => {
    if (!signedIn) return;
    loadCore();
    const timer = setInterval(loadCore, 30000);
    return () => clearInterval(timer);
  }, [signedIn, loadCore]);

  // Real-Time 500ms WebSocket Live Ticker (/api/v1/ws/live)
  useEffect(() => {
    if (!signedIn) return;
    const token = getAuthToken();
    if (!token || typeof window === 'undefined') return;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closedByUnmount = false;

    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(`${proto}//${window.location.host}/api/v1/ws/live?token=${encodeURIComponent(token)}`);
      ws.onopen = () => setWsLive(true);
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg?.type !== 'LIVE_TICK') return;
          if (msg.ts_ist) setLastTickIst(msg.ts_ist);
          if (Array.isArray(msg.indices) && msg.indices.length > 0) setIndices(msg.indices);
          if (Array.isArray(msg.quotes) && msg.quotes.length > 0) {
            setOverview((prev: any) => ({
              ...(prev || {}),
              freshness: msg.freshness || prev?.freshness || 'LIVE',
              data: { ...(prev?.data || {}), quotes: msg.quotes },
            }));
          }
          if (msg.ledger) {
            setLedgerData((prev: any) => ({ ...(prev || {}), ledger: msg.ledger }));
          }
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        setWsLive(false);
        if (!closedByUnmount) {
          reconnectTimer = setTimeout(connect, 2500);
        }
      };
      ws.onerror = () => {
        try { ws?.close(); } catch { /* ignore */ }
      };
    };

    connect();
    return () => {
      closedByUnmount = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      try { ws?.close(); } catch { /* ignore */ }
    };
  }, [signedIn]);

  useEffect(() => {
    if (!signedIn) return;
    if (activeTab === 'engines' && !engineScan) {
      apiGet<any>('/api/v1/engines/scan?limit=12').then(setEngineScan).catch(() => {});
    }
    if (activeTab === 'news' && !newsData) {
      apiGet<any>('/api/v1/news').then(setNewsData).catch(() => {});
    }
    if (activeTab === 'charts') {
      apiGet<any>(`/api/v1/market/depth/${encodeURIComponent(chartSymbol)}`).then(setDepthData).catch(() => {});
      apiGet<any>(`/api/v1/market/candles/${encodeURIComponent(chartSymbol)}?timeframe=${encodeURIComponent(chartTf)}&limit=45`)
        .then((r) => setChartCandles(r?.candles || []))
        .catch(() => setChartCandles([]));
      apiGet<any>(`/api/v1/market/indicators/${encodeURIComponent(chartSymbol)}?timeframe=${encodeURIComponent(chartTf)}`)
        .then((r) => setChartIndicators(r?.indicators || undefined))
        .catch(() => setChartIndicators(undefined));
    }
    if (activeTab === 'system') {
      apiGet<any>('/api/v1/integrations/status').then(setIntegrations).catch(() => {});
    }
    if (activeTab === 'journal') {
      apiGet<any>('/api/v1/journal').then((r) => setJournalList(r?.entries || [])).catch(() => {});
    }
    if (activeTab === 'assistant' && !morningBrief) {
      apiGet<any>('/api/v1/assistant/morning-brief').then(setMorningBrief).catch(() => {});
    }
  }, [activeTab, signedIn, chartSymbol, chartTf, engineScan, newsData, morningBrief]);



  const ledger = ledgerData?.ledger || autoStatus?.ledger || {};
  const openRows: any[] = ledger?.open_positions_live || [];
  const closedTrades: any[] = ledgerData?.closed_trades || [];
  const regime = autoStatus?.last_regime || engineScan?.regime;

  const saveAutoConfig = async (resetKill = false) => {
    setBusy(true);
    try {
      await apiPut('/api/v1/auto-trader/config', {
        enabled: cfgEnabled,
        starting_balance_inr: Number(cfgBalance),
        per_trade_amount_inr: Number(cfgPerTrade),
        max_open_positions: Number(cfgMaxPos),
        daily_loss_limit_pct: Number(cfgDailyLoss),
        enable_engine_1_orb: cfgE1,
        enable_engine_2a_vcp: cfgE2a,
        enable_engine_2b_rsi2: cfgE2b,
        enable_news_shield: cfgNewsShield,
        reset_kill_switch: resetKill,
      });
      await loadCore();
      notify(resetKill ? 'Kill-switch reset & config saved.' : 'Auto-Trader balance & rules saved.');
    } catch (ex) {
      notify(errMsg(ex, 'Failed to save config'));
    } finally {
      setBusy(false);
    }
  };

  const runAutoCycle = async (executeEntries: boolean) => {
    setBusy(true);
    try {
      const res = await apiPost<any>('/api/v1/auto-trader/run', { execute_entries: executeEntries, limit: 12 });
      setEngineScan(res);
      await loadCore();
      notify(`Cycle complete: ${res.confirmed_signals} confirmed, ${res.opened_trades?.length || 0} opened, ${res.monitor_actions?.length || 0} managed.`);
    } catch (ex) {
      notify(errMsg(ex, 'Auto cycle failed'));
    } finally {
      setBusy(false);
    }
  };

  const closePositionNow = async (posId: string, ltp: string) => {
    setBusy(true);
    try {
      await apiPost(`/api/v1/paper/positions/${encodeURIComponent(posId)}/close`, {
        exit_price: Number(ltp),
        exit_reason: 'MANUAL_LIVE_EXIT',
      });
      await loadCore();
      notify('Position closed at live LTP with exact NSE charges.');
    } catch (ex) {
      notify(errMsg(ex, 'Close failed'));
    } finally {
      setBusy(false);
    }
  };

  const sendDailySummary = async (sendTg: boolean) => {
    setBusy(true);
    try {
      const res = await apiPost<any>('/api/v1/auto-trader/daily-summary', { send_telegram: sendTg });
      notify(sendTg ? (res?.telegram?.delivered ? 'Daily Summary sent to Telegram!' : 'Summary built (Telegram not configured).') : 'Daily Summary refreshed.');
    } catch (ex) {
      notify(errMsg(ex, 'Daily summary failed'));
    } finally {
      setBusy(false);
    }
  };

  const refreshNews = async () => {
    setBusy(true);
    try {
      const q = newsSymbol.trim() ? `?symbol=${encodeURIComponent(newsSymbol.trim().toUpperCase())}&refresh=true` : '?refresh=true';
      const res = await apiGet<any>(`/api/v1/news${q}`);
      setNewsData(res);
      notify(`Loaded ${res?.count || 0} live market news stories.`);
    } catch (ex) {
      notify(errMsg(ex, 'News refresh failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* Top Institutional Header + Live Indian Indices Strip */}
      <header className="sticky top-0 z-30 border-b border-slate-800/90 bg-slate-950/90 backdrop-blur-xl">
        <div className="max-w-7xl mx-auto px-4 py-2.5 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="h-8 w-8 rounded-xl bg-gradient-to-br from-sky-400 to-emerald-400 flex items-center justify-center text-slate-950 font-black text-sm">TQ</div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-bold text-white tracking-tight">TRADEIQ v2.0</span>
                <Badge tone={overview?.freshness === 'LIVE' ? 'emerald' : 'amber'}>
                  {overview?.freshness === 'LIVE' ? 'LIVE FEED' : overview?.freshness || 'CHECKING'}
                </Badge>
                <Badge tone="sky">PAPER ONLY</Badge>
                {wsLive && <Badge tone="emerald">⚡ {lastTickIst || "STREAMING"}</Badge>}
              </div>
              <div className="text-[11px] text-slate-400">3-Engine Live Auto Paper-Trading • Penny-Accurate P&amp;L</div>
            </div>
          </div>

          {/* Live NIFTY 50 / BANKNIFTY / SENSEX Strip */}
          <div className="flex flex-wrap items-center gap-2">
            {indices.map((idx) => (
              <div key={idx.symbol} className="rounded-xl border border-slate-800 bg-slate-900/90 px-3 py-1.5 flex items-center gap-2 text-xs">
                <span className="font-semibold text-slate-300">{idx.symbol}</span>
                <span className="font-mono font-semibold text-white">{idx.ltp ? `₹${Number(idx.ltp).toLocaleString('en-IN')}` : 'OFFLINE'}</span>
                {idx.change_pct !== null && idx.change_pct !== undefined && (
                  <span className={`font-mono text-[11px] ${pnlColor(idx.change_pct)}`}>
                    {Number(idx.change_pct) > 0 ? '+' : ''}{idx.change_pct}%
                  </span>
                )}
              </div>
            ))}
            <button
              onClick={loadCore}
              className="rounded-xl border border-slate-700 hover:border-sky-500 px-3 py-1.5 text-xs text-slate-200 transition"
            >{busy ? <Spinner label="Syncing" /> : 'Refresh'}</button>
          </div>
        </div>

        {/* Always-Visible Live API Connection Status Strip */}
        <div className="border-t border-slate-800/60 bg-slate-950/80">
          <div className="max-w-7xl mx-auto px-4 py-1.5 flex flex-wrap items-center justify-between gap-2 text-[11px]">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-slate-400 font-medium mr-1">API Status:</span>
              {(connectivity?.services || []).map((s: any) => {
                const dot = s.state === 'CONNECTED' ? 'bg-emerald-400' : s.state === 'PARTIAL' || s.state === 'DEMO_MODE' ? 'bg-amber-400' : 'bg-rose-500';
                const pill = s.state === 'CONNECTED' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200' : s.state === 'PARTIAL' || s.state === 'DEMO_MODE' ? 'border-amber-500/30 bg-amber-500/10 text-amber-200' : 'border-rose-500/40 bg-rose-500/15 text-rose-200';
                return (
                  <button
                    key={s.key}
                    onClick={() => setActiveTab('system')}
                    title={s.detail}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 transition hover:opacity-90 ${pill}`}
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
                    <span>{s.name}</span>
                    <span className="opacity-75">• {s.state === 'CONNECTED' ? 'OK' : s.state === 'DEMO_MODE' ? 'DEMO' : s.state === 'PARTIAL' ? 'READY' : 'MISSING API'}</span>
                  </button>
                );
              })}
            </div>
            {connectivity?.demo_mode && (
              <span className="text-amber-300 font-medium">Demo Mode Active — Set DEMO_MODE=false in Render Environment for 100% Live</span>
            )}
          </div>
        </div>

        {/* Dedicated Clean Menubar — ZERO "Others/More" hidden drawer! Every section shows its own live API dot */}
        <nav className="border-t border-slate-800/80 bg-slate-900/60">
          <div className="max-w-7xl mx-auto px-3 py-1.5 flex items-center gap-1.5 overflow-x-auto no-scrollbar">
            {MENU_TABS.map((t) => {
              const active = activeTab === t.key;
              const pInfo = (connectivity?.panels || []).find((x: any) => x.tab_key === t.key);
              const dotColor = !pInfo ? 'bg-slate-500' : pInfo.state === 'CONNECTED' ? 'bg-emerald-400' : pInfo.state === 'DEMO_MODE' || pInfo.state === 'PARTIAL' ? 'bg-amber-400' : 'bg-rose-500';
              return (
                <button
                  key={t.key}
                  onClick={() => setActiveTab(t.key)}
                  className={`shrink-0 inline-flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-xs font-medium transition ${
                    active
                      ? 'bg-sky-500 text-slate-950 font-semibold shadow-md shadow-sky-500/20'
                      : 'text-slate-300 hover:bg-slate-800 hover:text-white'
                  }`}
                >
                  <span className={`h-1.5 w-1.5 rounded-full ${active ? 'bg-slate-950' : dotColor}`} />
                  <span>{t.label}</span>
                </button>
              );
            })}
          </div>
        </nav>
      </header>

      {busy && (
        <div className="fixed top-0 left-0 right-0 z-50 h-0.5 bg-sky-500/20 overflow-hidden">
          <div className="h-full w-1/3 bg-sky-400 animate-pulse" />
        </div>
      )}
      {toast && (
        <div className="fixed bottom-4 right-4 z-50 rounded-xl border border-sky-500/40 bg-slate-900/95 px-4 py-2.5 text-xs text-sky-200 shadow-2xl">
          {toast}
        </div>
      )}

      {/* Main Workspace Container */}
      <main className="max-w-7xl w-full mx-auto px-4 py-5 flex-1 space-y-5">
        {/* TAB 1: DASHBOARD */}
        {activeTab === 'dashboard' && (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
              <Card>
                <div className="text-[11px] text-slate-400">Total Live Equity</div>
                <div className="text-xl font-mono font-bold text-white mt-1">{fmtInr(ledger.total_equity_inr)}</div>
                <div className="text-[11px] text-slate-400 mt-1">Start: {fmtInr(ledger.starting_balance_inr)}</div>
              </Card>
              <Card>
                <div className="text-[11px] text-slate-400">Total Live Net P&amp;L</div>
                <div className={`text-xl font-mono font-bold mt-1 ${pnlColor(ledger.total_net_pnl_inr)}`}>
                  {fmtInr(ledger.total_net_pnl_inr, true)}
                </div>
                <div className="text-[11px] text-slate-400 mt-1">After NSE charges</div>
              </Card>
              <Card>
                <div className="text-[11px] text-slate-400">Available Cash</div>
                <div className="text-xl font-mono font-bold text-sky-300 mt-1">{fmtInr(ledger.available_cash_inr)}</div>
                <div className="text-[11px] text-slate-400 mt-1">Deployed: {fmtInr(ledger.deployed_capital_inr)}</div>
              </Card>
              <Card>
                <div className="text-[11px] text-slate-400">Live Accuracy</div>
                <div className="text-xl font-mono font-bold text-emerald-300 mt-1">{ledger.accuracy_pct || '0.0'}%</div>
                <div className="text-[11px] text-slate-400 mt-1">{ledger.wins || 0}W / {ledger.losses || 0}L • PF {ledger.profit_factor || '0.00'}</div>
              </Card>
              <Card>
                <div className="text-[11px] text-slate-400">Engine 0 Regime Gate</div>
                <div className="mt-1.5 flex items-center gap-2">
                  <Badge tone={regime?.regime === 'TRENDING' ? 'emerald' : regime?.regime === 'CHOPPY' ? 'rose' : 'amber'}>
                    {regime?.regime || 'READY TO SCAN'}
                  </Badge>
                </div>
                <div className="text-[11px] text-slate-400 mt-1.5">
                  Auto-Trader: {autoStatus?.config?.enabled ? 'ACTIVE' : 'PAUSED'}
                </div>
              </Card>
            </div>

            <Card
              title="Live Auto-Trader Radar (Bot Watching Right Now)"
              subtitle={`Watching ${autoStatus?.watched_symbols?.length || 0} active NSE stocks (out of ${autoStatus?.total_universe_count || 0}) • Last Scan: ${autoStatus?.last_cycle_ist ? String(autoStatus.last_cycle_ist).slice(11, 19) + ' IST' : 'Not scanned yet — click Run 3-Engine Auto Cycle'}`}
              action={
                <Badge tone={autoStatus?.scheduler_active ? 'emerald' : 'amber'}>
                  {autoStatus?.scheduler_active ? 'AUTO SCHEDULER ON (09:00-15:30 IST)' : 'MANUAL / DEPLOY main.py FOR AUTO'}
                </Badge>
              }
            >
              <div className="space-y-2.5 text-xs">
                <div className="flex flex-wrap gap-1.5">
                  {(autoStatus?.watched_symbols || []).map((sym: string) => (
                    <span key={sym} className="rounded-lg border border-slate-800 bg-slate-950 px-2 py-0.5 font-mono text-[11px] text-slate-300">{sym}</span>
                  ))}
                </div>
                <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-3 flex flex-wrap items-center justify-between gap-2">
                  <div className="text-slate-300">
                    {openRows.length > 0
                      ? `${openRows.length} live paper trade(s) currently active and monitored every 60s.`
                      : (autoStatus?.recent_signals?.length
                          ? `Scanned ${autoStatus.recent_signals.length} top setups — 0 CONFIRMED yet (bot only enters 100% confirmed setups, never forces bad trades). Closest: ${autoStatus.recent_signals[0].symbol} (${autoStatus.recent_signals[0].score}% • ${autoStatus.recent_signals[0].engine_code}).`
                          : 'Waiting for first 09:30 IST scan — or tap "Run 3-Engine Auto Cycle" right now to scan all stocks immediately.')}
                  </div>
                  <button onClick={() => runAutoCycle(true)} disabled={busy} className="rounded-xl bg-emerald-500 hover:bg-emerald-400 px-3 py-1.5 text-xs font-semibold text-slate-950">
                    {busy ? <Spinner label="Scanning..." /> : 'Scan & Trade Now'}
                  </button>
                </div>
              </div>
            </Card>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <div className="lg:col-span-2">
                <Card
                  title="Live NSE Market Pulse"
                  subtitle="Click Chart or Plan on any live stock"
                  action={
                    <button onClick={() => runAutoCycle(true)} disabled={busy} className="rounded-xl bg-emerald-500 hover:bg-emerald-400 px-3 py-1.5 text-xs font-semibold text-slate-950">{busy ? <Spinner label="Scanning..." /> : 'Run 3-Engine Auto Cycle'}</button>
                  }
                >
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="text-slate-400 border-b border-slate-800">
                        <tr>
                          <th className="py-2 pr-2">Symbol</th>
                          <th className="py-2 px-2">Sector</th>
                          <th className="py-2 px-2 text-right">LTP</th>
                          <th className="py-2 px-2 text-right">Change</th>
                          <th className="py-2 pl-2 text-right">Actions</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/60">
                        {(overview?.data?.quotes || []).map((q: any) => (
                          <tr key={q.symbol} className="hover:bg-slate-800/40">
                            <td className="py-2.5 pr-2 font-semibold text-white">{q.symbol}</td>
                            <td className="py-2.5 px-2 text-slate-400">{q.sector || 'NSE'}</td>
                            <td className="py-2.5 px-2 text-right font-mono text-white">{fmtInr(q.ltp)}</td>
                            <td className={`py-2.5 px-2 text-right font-mono ${pnlColor(q.change_pct)}`}>
                              {Number(q.change_pct) > 0 ? '+' : ''}{q.change_pct}%
                            </td>
                            <td className="py-2.5 pl-2 text-right space-x-1.5">
                              <button
                                onClick={() => { setChartSymbol(q.symbol); setActiveTab('charts'); }}
                                className="rounded-lg bg-slate-800 hover:bg-sky-500/20 px-2 py-1 text-[11px] text-sky-300"
                              >
                                Chart
                              </button>
                              <button
                                onClick={() => { setPlanSymbol(q.symbol); setActiveTab('planner'); }}
                                className="rounded-lg bg-slate-800 hover:bg-emerald-500/20 px-2 py-1 text-[11px] text-emerald-300"
                              >
                                Plan
                              </button>
                            </td>
                          </tr>
                        ))}
                        {!(overview?.data?.quotes || []).length && (
                          <tr><td colSpan={5} className="py-6 text-center text-slate-400">Live Angel One feed not connected yet — check System &amp; Settings.</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </Card>
              </div>

              <Card title="Live Open Positions" subtitle={`${openRows.length} active trades (Net P&L after exit charges)`}>
                <div className="space-y-2.5">
                  {openRows.map((p) => (
                    <div key={p.position_id} className="rounded-xl border border-slate-800 bg-slate-950/70 p-3">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-white text-sm">{p.symbol}</span>
                          <Badge tone="sky">{p.engine_code}</Badge>
                        </div>
                        <span className={`font-mono text-sm font-bold ${pnlColor(p.unrealised_net_inr)}`}>
                          {fmtInr(p.unrealised_net_inr, true)}
                        </span>
                      </div>
                      <div className="mt-1.5 grid grid-cols-3 gap-1 text-[11px] text-slate-400 font-mono">
                        <div>Qty: {p.quantity}</div>
                        <div>Entry: ₹{p.entry_fill_price}</div>
                        <div>LTP: ₹{p.current_ltp}</div>
                      </div>
                      <div className="mt-1 flex items-center justify-between text-[11px] text-slate-400">
                        <span>Gross {fmtInr(p.unrealised_gross_inr, true)} | Chg ₹{p.total_estimated_charges_inr}</span>
                        <button onClick={() => closePositionNow(p.position_id, p.current_ltp)} className="text-rose-300 hover:text-rose-200 underline">
                          Close
                        </button>
                      </div>
                    </div>
                  ))}
                  {!openRows.length && <div className="text-xs text-slate-400 py-8 text-center">No open paper positions right now.</div>}
                </div>
              </Card>
            </div>
          </>
        )}

        {/* TAB 2: AUTO-TRADER */}
        {activeTab === 'autotrader' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card
              title="Auto Paper-Trading Controls"
              subtitle="Set your own Balance, Per-Trade Amount & 3-Engine toggles"
              action={
                <Badge tone={autoStatus?.kill_switch_active ? 'rose' : cfgEnabled ? 'emerald' : 'amber'}>
                  {autoStatus?.kill_switch_active ? 'KILL-SWITCH LOCKED' : cfgEnabled ? 'AUTO ON' : 'PAUSED'}
                </Badge>
              }
            >
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <label className="text-slate-400 block mb-1">Account Balance (₹)</label>
                  <input value={cfgBalance} onChange={(e) => setCfgBalance(e.target.value)} className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-white" />
                </div>
                <div>
                  <label className="text-slate-400 block mb-1">Per-Trade Amount (₹)</label>
                  <input value={cfgPerTrade} onChange={(e) => setCfgPerTrade(e.target.value)} className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-white" />
                </div>
                <div>
                  <label className="text-slate-400 block mb-1">Max Open Positions</label>
                  <input value={cfgMaxPos} onChange={(e) => setCfgMaxPos(e.target.value)} className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-white" />
                </div>
                <div>
                  <label className="text-slate-400 block mb-1">Daily Loss Kill-Switch (%)</label>
                  <input value={cfgDailyLoss} onChange={(e) => setCfgDailyLoss(e.target.value)} className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-white" />
                </div>
              </div>

              <div className="mt-4 space-y-2 text-xs">
                {[
                  ['Master Auto-Trader Enabled (Morning Scan + Live SL/Target/Trail)', cfgEnabled, setCfgEnabled],
                  ['Engine 1: Morning 15m ORB + VWAP + 5-Level Order-Book Imbalance', cfgE1, setCfgE1],
                  ['Engine 2A: Minervini 8-Point Trend + VCP Breakout (Swing)', cfgE2a, setCfgE2a],
                  ['Engine 2B: Connors RSI(2) Pullback Mean Reversion (Swing)', cfgE2b, setCfgE2b],
                  ['Gemini News Sentiment Shield (Block BUY on HIGH_RISK/BEARISH news)', cfgNewsShield, setCfgNewsShield],
                ].map(([label, val, setter]: any) => (
                  <label key={label} className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-2.5 cursor-pointer">
                    <span className="text-slate-200">{label}</span>
                    <input type="checkbox" checked={val} onChange={(e) => setter(e.target.checked)} className="h-4 w-4 accent-sky-500" />
                  </label>
                ))}
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                <button onClick={() => saveAutoConfig(false)} disabled={busy} className="rounded-xl bg-sky-500 hover:bg-sky-400 px-4 py-2 text-xs font-semibold text-slate-950">
                  Save Config
                </button>
                <button onClick={() => runAutoCycle(true)} disabled={busy} className="rounded-xl bg-emerald-500 hover:bg-emerald-400 px-4 py-2 text-xs font-semibold text-slate-950">{busy ? <Spinner label="Running..." /> : 'Run Auto-Trade Now'}</button>
                <button onClick={() => sendDailySummary(true)} disabled={busy} className="rounded-xl border border-sky-500/40 bg-sky-500/10 px-4 py-2 text-xs text-sky-200">
                  Send Daily Telegram Summary
                </button>
                {autoStatus?.kill_switch_active && (
                  <button onClick={() => saveAutoConfig(true)} className="rounded-xl bg-rose-500 px-4 py-2 text-xs font-semibold text-white">
                    Reset Kill-Switch
                  </button>
                )}
              </div>
            </Card>

            <Card title="Auto-Trader Execution Log" subtitle={regime?.summary || 'Run a cycle to evaluate Engine 0 Regime Gate'}>
              <div className="space-y-2 max-h-[420px] overflow-y-auto text-xs">
                {(autoStatus?.execution_log || []).map((log: any, idx: number) => (
                  <div key={idx} className="rounded-xl border border-slate-800 bg-slate-950/70 p-2.5">
                    <div className="flex items-center justify-between">
                      <Badge tone="sky">{log.event}</Badge>
                      <span className="text-[10px] font-mono text-slate-500">{String(log.ts_ist || '').slice(11, 19)} IST</span>
                    </div>
                    <div className="mt-1 text-slate-300 font-mono text-[11px] break-all">
                      {Object.entries(log.details || {}).map(([k, v]) => `${k}: ${v}`).join(' • ')}
                    </div>
                  </div>
                ))}
                {!(autoStatus?.execution_log || []).length && (
                  <div className="py-10 text-center text-slate-400">No auto-trader events logged yet. Click "Run Auto-Trade Now" to test.</div>
                )}
              </div>
            </Card>
          </div>
        )}

        {/* TAB 3: LIVE CHARTS & 5-LEVEL DEPTH */}
        {activeTab === 'charts' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="lg:col-span-2">
              <Card
                title={`Live Candlestick Chart — ${chartSymbol}`}
                action={
                  <div className="flex items-center gap-2">
                    <input
                      value={chartSymbol}
                      onChange={(e) => setChartSymbol(e.target.value.toUpperCase())}
                      className="w-28 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1 text-xs font-mono text-white"
                    />
                    {['5m', '15m', '1h', '1d'].map((tf) => (
                      <button
                        key={tf}
                        onClick={() => setChartTf(tf)}
                        className={`rounded-lg px-2.5 py-1 text-xs ${chartTf === tf ? 'bg-sky-500 text-slate-950 font-semibold' : 'bg-slate-800 text-slate-300'}`}
                      >
                        {tf}
                      </button>
                    ))}
                  </div>
                }
              >
                <TechnicalChartCanvas symbol={chartSymbol} timeframe={chartTf} candles={chartCandles} indicators={chartIndicators} />
              </Card>
            </div>
            <Card title="5-Level Order-Book Imbalance" subtitle="Angel One SnapQuote Depth Proxy">
              <div className="space-y-3 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Pressure</span>
                  <Badge tone={depthData?.imbalance?.pressure === 'BUY_IMBALANCE' ? 'emerald' : depthData?.imbalance?.pressure === 'SELL_IMBALANCE' ? 'rose' : 'slate'}>
                    {depthData?.imbalance?.pressure || 'BALANCED'} ({depthData?.imbalance?.imbalance_ratio || '1.00'}x)
                  </Badge>
                </div>
                <div className="grid grid-cols-2 gap-2 font-mono">
                  <div className="rounded-xl bg-emerald-500/10 border border-emerald-500/20 p-2.5">
                    <div className="text-[10px] text-emerald-300">Total Buy Qty</div>
                    <div className="text-sm font-bold text-white">{Number(depthData?.imbalance?.total_buy_qty || 0).toLocaleString('en-IN')}</div>
                  </div>
                  <div className="rounded-xl bg-rose-500/10 border border-rose-500/20 p-2.5">
                    <div className="text-[10px] text-rose-300">Total Sell Qty</div>
                    <div className="text-sm font-bold text-white">{Number(depthData?.imbalance?.total_sell_qty || 0).toLocaleString('en-IN')}</div>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2 pt-1 font-mono text-[11px]">
                  <div>
                    <div className="text-emerald-400 mb-1 font-semibold">Best 5 Bids</div>
                    {(depthData?.depth?.best_5_buy || []).map((b: any, i: number) => (
                      <div key={i} className="flex justify-between py-0.5 border-b border-slate-800/60">
                        <span>₹{Number(b.price).toFixed(2)}</span><span className="text-slate-400">{b.quantity}</span>
                      </div>
                    ))}
                  </div>
                  <div>
                    <div className="text-rose-400 mb-1 font-semibold">Best 5 Asks</div>
                    {(depthData?.depth?.best_5_sell || []).map((s: any, i: number) => (
                      <div key={i} className="flex justify-between py-0.5 border-b border-slate-800/60">
                        <span>₹{Number(s.price).toFixed(2)}</span><span className="text-slate-400">{s.quantity}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </Card>
          </div>
        )}

        {/* TAB 4: 3-ENGINE SCANNER */}
        {activeTab === 'engines' && (
          <Card
            title="3-Engine Live Setup Scanner"
            subtitle={engineScan?.regime?.summary || 'Engine 1 (ORB+VWAP+Depth), Engine 2A (Minervini VCP), Engine 2B (Connors RSI-2)'}
            action={
              <button onClick={() => runAutoCycle(false)} disabled={busy} className="rounded-xl bg-sky-500 hover:bg-sky-400 px-3.5 py-1.5 text-xs font-semibold text-slate-950">{busy ? <Spinner label="Scanning..." /> : 'Rescan All 3 Engines'}</button>
            }
          >
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-slate-400 border-b border-slate-800">
                  <tr>
                    <th className="py-2 pr-2">Symbol</th>
                    <th className="py-2 px-2">Engine</th>
                    <th className="py-2 px-2">Status</th>
                    <th className="py-2 px-2 text-right">Score</th>
                    <th className="py-2 px-2 text-right">Entry / SL / Target</th>
                    <th className="py-2 px-2 text-right">Net R:R</th>
                    <th className="py-2 pl-2">Order-Book</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {(engineScan?.top_signals || []).map((s: any, idx: number) => (
                    <tr key={`${s.symbol}-${s.engine_code}-${idx}`} className="hover:bg-slate-800/40">
                      <td className="py-2.5 pr-2 font-semibold text-white">{s.symbol}</td>
                      <td className="py-2.5 px-2"><Badge tone="sky">{s.engine_code}</Badge></td>
                      <td className="py-2.5 px-2">
                        <Badge tone={s.status === 'CONFIRMED' ? 'emerald' : s.status === 'WATCHLIST' ? 'amber' : 'slate'}>{s.status}</Badge>
                      </td>
                      <td className="py-2.5 px-2 text-right font-mono text-white">{s.score}%</td>
                      <td className="py-2.5 px-2 text-right font-mono text-slate-200">₹{s.entry_price} / ₹{s.stop_loss} / ₹{s.target_price}</td>
                      <td className="py-2.5 px-2 text-right font-mono text-emerald-300">{s.expected_net_rr}:1</td>
                      <td className="py-2.5 pl-2 text-[11px] text-slate-300">{s.order_book_pressure}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {/* TAB 5: PAPER POSITIONS (PENNY-ACCURATE LEDGER) */}
        {activeTab === 'positions' && (
          <div className="space-y-4">
            <Card title="Open Paper Positions (Live Mark-to-Market)" subtitle="Net P&L deducts BOTH entry AND estimated exit NSE charges — never misleading">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-2">Symbol</th>
                      <th className="py-2">Engine</th>
                      <th className="py-2 text-right">Qty</th>
                      <th className="py-2 text-right">Entry / LTP</th>
                      <th className="py-2 text-right">SL / Target</th>
                      <th className="py-2 text-right">Gross P&amp;L</th>
                      <th className="py-2 text-right">Est. Charges</th>
                      <th className="py-2 text-right">Live Net P&amp;L</th>
                      <th className="py-2 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60 font-mono">
                    {openRows.map((p) => (
                      <tr key={p.position_id}>
                        <td className="py-2.5 font-sans font-semibold text-white">{p.symbol}</td>
                        <td className="py-2.5 font-sans"><Badge tone="sky">{p.engine_code}</Badge></td>
                        <td className="py-2.5 text-right">{p.quantity}</td>
                        <td className="py-2.5 text-right">₹{p.entry_fill_price} / ₹{p.current_ltp}</td>
                        <td className="py-2.5 text-right">₹{p.stop_loss} / ₹{p.target_price}</td>
                        <td className={`py-2.5 text-right ${pnlColor(p.unrealised_gross_inr)}`}>{fmtInr(p.unrealised_gross_inr, true)}</td>
                        <td className="py-2.5 text-right text-amber-300">-₹{p.total_estimated_charges_inr}</td>
                        <td className={`py-2.5 text-right font-bold ${pnlColor(p.unrealised_net_inr)}`}>{fmtInr(p.unrealised_net_inr, true)}</td>
                        <td className="py-2.5 text-right font-sans">
                          <button onClick={() => closePositionNow(p.position_id, p.current_ltp)} className="rounded-lg bg-rose-500/20 hover:bg-rose-500/30 px-2.5 py-1 text-[11px] text-rose-200">Close</button>
                        </td>
                      </tr>
                    ))}
                    {!openRows.length && <tr><td colSpan={9} className="py-6 text-center font-sans text-slate-400">No open positions.</td></tr>}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card title="Closed Paper Trades Audit Ledger" subtitle="Strict WIN/LOSS classification based on Net P&L after all NSE charges">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-2">Symbol</th>
                      <th className="py-2">Engine</th>
                      <th className="py-2">Exit Reason</th>
                      <th className="py-2">Outcome</th>
                      <th className="py-2 text-right">Entry → Exit</th>
                      <th className="py-2 text-right">Gross</th>
                      <th className="py-2 text-right">Charges</th>
                      <th className="py-2 text-right">Net P&amp;L</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60 font-mono">
                    {closedTrades.map((t) => (
                      <tr key={t.position_id}>
                        <td className="py-2.5 font-sans font-semibold text-white">{t.symbol}</td>
                        <td className="py-2.5 font-sans"><Badge tone="slate">{t.engine_code || 'MANUAL'}</Badge></td>
                        <td className="py-2.5 font-sans text-slate-300">{t.exit_reason}</td>
                        <td className="py-2.5 font-sans">
                          <Badge tone={Number(t.net_pnl_inr) > 0 ? 'emerald' : Number(t.net_pnl_inr) < 0 ? 'rose' : 'slate'}>
                            {t.outcome || (Number(t.net_pnl_inr) > 0 ? 'WIN' : Number(t.net_pnl_inr) < 0 ? 'LOSS' : 'BREAKEVEN')}
                          </Badge>
                        </td>
                        <td className="py-2.5 text-right">₹{t.entry_fill_price} → ₹{t.exit_fill_price}</td>
                        <td className={`py-2.5 text-right ${pnlColor(t.gross_pnl_inr)}`}>{fmtInr(t.gross_pnl_inr, true)}</td>
                        <td className="py-2.5 text-right text-amber-300">-₹{(Number(t.entry_charges_inr || 0) + Number(t.exit_charges_inr || 0)).toFixed(2)}</td>
                        <td className={`py-2.5 text-right font-bold ${pnlColor(t.net_pnl_inr)}`}>{fmtInr(t.net_pnl_inr, true)}</td>
                      </tr>
                    ))}
                    {!closedTrades.length && <tr><td colSpan={8} className="py-6 text-center font-sans text-slate-400">No closed trades yet.</td></tr>}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        )}

        {/* TAB 6: ACCURACY & P&L */}
        {activeTab === 'accuracy' && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <Card><div className="text-xs text-slate-400">Overall Accuracy</div><div className="text-2xl font-mono font-bold text-emerald-400 mt-1">{ledger.accuracy_pct || '0.0'}%</div></Card>
              <Card><div className="text-xs text-slate-400">Profit Factor</div><div className="text-2xl font-mono font-bold text-sky-300 mt-1">{ledger.profit_factor || '0.00'}</div></Card>
              <Card><div className="text-xs text-slate-400">Realised Net P&amp;L</div><div className={`text-2xl font-mono font-bold mt-1 ${pnlColor(ledger.realised_net_pnl_inr)}`}>{fmtInr(ledger.realised_net_pnl_inr, true)}</div></Card>
              <Card><div className="text-xs text-slate-400">Total NSE Charges Paid</div><div className="text-2xl font-mono font-bold text-amber-300 mt-1">{fmtInr(ledger.realised_charges_inr)}</div></Card>
            </div>
            <Card title="Per-Engine Live Accuracy Breakdown" subtitle="Compare which engine actually delivers positive expectancy">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {(ledger.engine_accuracy || []).map((e: any) => (
                  <div key={e.engine_code} className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-white text-sm">{e.engine_code}</span>
                      <Badge tone={e.is_profit ? 'emerald' : 'rose'}>{e.accuracy_pct}% Win</Badge>
                    </div>
                    <div className={`mt-2 text-lg font-mono font-bold ${pnlColor(e.net_pnl_inr)}`}>{fmtInr(e.net_pnl_inr, true)}</div>
                    <div className="mt-1 text-xs text-slate-400">{e.wins} Wins • {e.losses} Losses • Charges ₹{e.charges_inr}</div>
                  </div>
                ))}
                {!(ledger.engine_accuracy || []).length && <div className="text-xs text-slate-400 py-6">Close at least 1 paper trade to see per-engine accuracy stats.</div>}
              </div>
            </Card>
          </div>
        )}

        {/* TAB 7: REAL MULTI-SOURCE MARKET NEWS */}
        {activeTab === 'news' && (
          <Card
            title="Live Indian Market News & Gemini Sentiment Shield"
            subtitle="ET Markets, Livemint, Moneycontrol & Google News India — with readable summaries"
            action={
              <div className="flex items-center gap-2">
                <input
                  value={newsSymbol}
                  onChange={(e) => setNewsSymbol(e.target.value.toUpperCase())}
                  placeholder="Optional symbol (e.g. RELIANCE)"
                  className="w-48 rounded-xl border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-white"
                />
                <button onClick={refreshNews} disabled={busy} className="rounded-xl bg-sky-500 hover:bg-sky-400 px-3.5 py-1.5 text-xs font-semibold text-slate-950">{busy ? <Spinner label="Fetching..." /> : 'Fetch Live News'}</button>
              </div>
            }
          >
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {(newsData?.items || []).map((n: any) => (
                <article key={n.news_id} className="rounded-xl border border-slate-800 bg-slate-950/75 p-4 flex flex-col justify-between">
                  <div>
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <span className="text-[11px] font-medium text-sky-300">{n.source_name}</span>
                      <Badge tone={n.sentiment === 'BULLISH' ? 'emerald' : n.sentiment === 'HIGH_RISK' || n.sentiment === 'BEARISH' ? 'rose' : 'slate'}>
                        {n.sentiment || 'NEUTRAL'}
                      </Badge>
                    </div>
                    <h4 className="text-sm font-semibold text-white leading-snug">{n.headline}</h4>
                    {n.ai_summary && <p className="mt-2 text-xs text-slate-300 leading-relaxed">{n.ai_summary}</p>}
                  </div>
                  <div className="mt-3 pt-2 border-t border-slate-800/70 flex items-center justify-between text-[11px] text-slate-400">
                    <span>{String(n.published_at_ist || '').slice(0, 22)}</span>
                    <a href={n.source_url} target="_blank" rel="noreferrer" className="text-sky-400 hover:text-sky-300 font-medium">
                      Read Full Article →
                    </a>
                  </div>
                </article>
              ))}
              {!(newsData?.items || []).length && (
                <div className="col-span-2 py-10 text-center text-xs text-slate-400">
                  Click "Fetch Live News" to pull top Indian market stories from ET Markets, Livemint &amp; Moneycontrol.
                </div>
              )}
            </div>
          </Card>
        )}

        {/* TAB 8: PLANNER & RISK */}
        {activeTab === 'planner' && (
          <Card
            title="Deterministic Trade Planner & Risk Engine"
            action={
              <div className="flex items-center gap-2">
                <input value={planSymbol} onChange={(e) => setPlanSymbol(e.target.value.toUpperCase())} className="w-28 rounded-xl border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs font-mono text-white" />
                <select value={planMode} onChange={(e) => setPlanMode(e.target.value)} className="rounded-xl border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-white">
                  <option value="SWING">SWING</option>
                  <option value="INTRADAY">INTRADAY</option>
                </select>
                <button
                  onClick={async () => {
                    setBusy(true);
                    try {
                      const r = await apiPost<any>('/api/v1/planner/evaluate', {
                        symbol: planSymbol,
                        trading_mode: planMode,
                        strategy_code: 'BREAKOUT_RETEST',
                        capital_inr: Number(cfgBalance) || 100000,
                      });
                      setPlanResult(r);
                    } catch (ex) {
                      notify(errMsg(ex, 'Evaluation failed'));
                    } finally {
                      setBusy(false);
                    }
                  }}
                  className="rounded-xl bg-sky-500 px-3.5 py-1.5 text-xs font-semibold text-slate-950"
                >{busy ? <Spinner label="Evaluating..." /> : 'Evaluate Setup'}</button>
              </div>
            }
          >
            {planResult ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4 space-y-2">
                  <div className="flex justify-between"><span className="text-slate-400">Symbol &amp; Status</span><Badge tone="sky">{planResult.symbol} — {planResult.trade_plan?.status}</Badge></div>
                  <div className="flex justify-between font-mono"><span className="text-slate-400">Entry / SL / Target</span><span>₹{planResult.trade_plan?.risk_decision?.entry_price ?? '—'} / ₹{planResult.trade_plan?.risk_decision?.stop_loss ?? '—'} / ₹{planResult.trade_plan?.risk_decision?.target_price ?? '—'}</span></div>
                  <div className="flex justify-between font-mono"><span className="text-slate-400">Quantity / Allowed Risk</span><span>{planResult.trade_plan?.risk_decision?.quantity ?? 0} • ₹{planResult.trade_plan?.risk_decision?.allowed_risk_inr ?? '0.00'}</span></div>
                  {planResult.trade_plan?.risk_decision?.rejection_reason && (
                    <div className="text-amber-300 text-[11px] pt-1">Note: {planResult.trade_plan.risk_decision.rejection_reason}</div>
                  )}
                </div>
                <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                  <div className="text-sky-300 font-semibold mb-1">Grounded AI Explanation ({planResult.ai_explanation?.source})</div>
                  <p className="text-slate-300 leading-relaxed">{planResult.ai_explanation?.analysis}</p>
                </div>
              </div>
            ) : (
              <div className="py-8 text-center text-xs text-slate-400">Enter any NSE symbol and click Evaluate Setup.</div>
            )}
          </Card>
        )}

        {/* TAB 9: AI ASSISTANT */}
        {activeTab === 'assistant' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card title="Gemini Morning Brief" subtitle={morningBrief?.source || 'Live indices + news'}>
              <p className="text-xs text-slate-300 leading-relaxed">{morningBrief?.brief || 'Loading Morning Brief...'}</p>
            </Card>
            <div className="lg:col-span-2">
              <Card title="Grounded AI Trading Assistant" subtitle="Explains verified facts (F01..Fnn) — refuses live broker order requests">
                <div className="flex gap-2 mb-3">
                  <input value={aiQuestion} onChange={(e) => setAiQuestion(e.target.value)} className="flex-1 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white" />
                  <button
                    onClick={async () => {
                      setBusy(true);
                      try {
                        const r = await apiPost<any>('/api/v1/assistant/query', { question: aiQuestion, symbol: planSymbol, trading_mode: planMode });
                        setAiReply(r);
                      } catch (ex) {
                        notify(errMsg(ex, 'Query failed'));
                      } finally {
                        setBusy(false);
                      }
                    }}
                    className="rounded-xl bg-sky-500 px-4 py-2 text-xs font-semibold text-slate-950"
                  >{busy ? <Spinner label="Thinking..." /> : 'Ask Gemini'}</button>
                </div>
                {aiReply && (
                  <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4 text-xs space-y-2">
                    <div className="flex items-center gap-2"><Badge tone="sky">{aiReply.mode}</Badge><Badge tone="slate">{aiReply.plan_status}</Badge></div>
                    <p className="text-slate-200 leading-relaxed">{aiReply.answer}</p>
                    {aiReply.gemini_last_error && (
                      <div className="mt-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-[11px] font-mono text-amber-200">
                        Gemini Diagnostic: {aiReply.gemini_last_error}
                      </div>
                    )}
                  </div>
                )}
              </Card>
            </div>
          </div>
        )}

        {/* TAB 10: JOURNAL & BACKTEST */}
        {activeTab === 'journal' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card title="Trading Journal" subtitle="Log discipline & emotion notes">
              <div className="flex gap-2 mb-3">
                <input value={journalNote} onChange={(e) => setJournalNote(e.target.value)} placeholder="Write trade lesson..." className="flex-1 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white" />
                <button
                  onClick={async () => {
                    if (!journalNote.trim()) return;
                    await apiPost('/api/v1/journal', { symbol: chartSymbol, emotion_tag: 'DISCIPLINED', notes: journalNote });
                    setJournalNote('');
                    const r = await apiGet<any>('/api/v1/journal');
                    setJournalList(r?.entries || []);
                  }}
                  className="rounded-xl bg-sky-500 px-3.5 py-2 text-xs font-semibold text-slate-950"
                >
                  Save
                </button>
              </div>
              <div className="space-y-2 text-xs">
                {journalList.map((j: any, i: number) => (
                  <div key={i} className="rounded-xl border border-slate-800 bg-slate-950/70 p-2.5">
                    <span className="font-semibold text-sky-300">{j.symbol}</span> — <span className="text-slate-300">{j.notes}</span>
                  </div>
                ))}
              </div>
            </Card>
            <Card title="Bar-by-Bar Backtest Runner">
              <div className="flex gap-2 mb-3">
                <input value={btSymbol} onChange={(e) => setBtSymbol(e.target.value.toUpperCase())} className="w-32 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs font-mono text-white" />
                <button
                  onClick={async () => {
                    const r = await apiPost<any>('/api/v1/backtest/run', { symbol: btSymbol, timeframe: '1d', strategy_code: 'BREAKOUT_RETEST' });
                    setBtResult(r?.backtest || r);
                  }}
                  className="rounded-xl bg-emerald-500 px-4 py-2 text-xs font-semibold text-slate-950"
                >{busy ? <Spinner label="Testing..." /> : 'Run Backtest'}</button>
              </div>
              {btResult && (
                <div className="grid grid-cols-2 gap-2 text-xs font-mono">
                  <div className="rounded-xl bg-slate-950 p-3 border border-slate-800">Trades: {btResult.trades?.length ?? btResult.in_sample_metrics?.trade_count ?? 0}</div>
                  <div className="rounded-xl bg-slate-950 p-3 border border-slate-800">Win Rate: {btResult.out_of_sample_metrics?.win_rate_pct ?? btResult.in_sample_metrics?.win_rate_pct ?? '0.00'}%</div>
                  <div className="rounded-xl bg-slate-950 p-3 border border-slate-800">Net P&amp;L: {fmtInr(btResult.out_of_sample_metrics?.net_pnl_inr ?? btResult.in_sample_metrics?.net_pnl_inr ?? 0, true)}</div>
                  <div className="rounded-xl bg-slate-950 p-3 border border-slate-800">Max DD: {fmtInr(btResult.out_of_sample_metrics?.max_drawdown_inr ?? btResult.in_sample_metrics?.max_drawdown_inr ?? 0)}</div>
                </div>
              )}
            </Card>
          </div>
        )}

        {/* TAB 11: SYSTEM & SETTINGS — Full API & 11-Panel Connectivity Audit */}
        {activeTab === 'system' && (
          <div className="space-y-4">
            <Card
              title="Live API Connection Status (Real-Time Check)"
              subtitle="Green = Connected • Amber = Demo/Market Closed • Red = Missing API Key"
              action={
                <div className="flex items-center gap-2">
                  <button
                    onClick={async () => {
                      const r = await apiPost<any>('/api/v1/notifications/telegram/test', { confirm_send: true }).catch(() => null);
                      notify(r?.delivered ? 'Telegram test message delivered!' : 'Telegram not configured or failed.');
                    }}
                    className="rounded-xl bg-sky-500/20 text-sky-200 border border-sky-500/30 px-3 py-1.5 text-xs"
                  >
                    Test Telegram Ping
                  </button>
                  <button onClick={() => { logout(); onSignOut(); }} className="rounded-xl bg-rose-500/20 hover:bg-rose-500/30 border border-rose-500/30 px-3.5 py-1.5 text-xs text-rose-200">
                    Sign Out
                  </button>
                </div>
              }
            >
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 text-xs">
                {(connectivity?.services || []).map((s: any) => (
                  <div key={s.key} className="rounded-xl border border-slate-800 bg-slate-950/75 p-4 space-y-1.5">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-white">{s.name}</span>
                      <Badge tone={s.state === 'CONNECTED' ? 'emerald' : s.state === 'DEMO_MODE' || s.state === 'PARTIAL' ? 'amber' : 'rose'}>
                        {s.state}
                      </Badge>
                    </div>
                    <p className="text-slate-400 leading-relaxed">{s.detail}</p>
                    {s.missing_env_vars?.length > 0 && (
                      <div className="pt-1 text-[11px] font-mono text-rose-300">
                        Required in Render: {s.missing_env_vars.join(', ')}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </Card>

            <Card title="All 11 Panels — API Connection Map" subtitle="See at a glance which panel is Live Connected vs which panel needs an API key">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-2 pr-3">Panel</th>
                      <th className="py-2 px-3">Status</th>
                      <th className="py-2 pl-3">Missing API / Action Needed</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60">
                    {(connectivity?.panels || []).map((p: any) => (
                      <tr key={p.tab_key} className="hover:bg-slate-800/40">
                        <td className="py-2.5 pr-3 font-semibold text-white">{p.panel_name}</td>
                        <td className="py-2.5 px-3">
                          <Badge tone={p.state === 'CONNECTED' ? 'emerald' : p.state === 'DEMO_MODE' || p.state === 'PARTIAL' ? 'amber' : 'rose'}>
                            {p.state === 'CONNECTED' ? 'CONNECTED' : p.state === 'DEMO_MODE' ? 'DEMO MODE' : p.state === 'PARTIAL' ? 'READY' : 'API NEEDED'}
                          </Badge>
                        </td>
                        <td className="py-2.5 pl-3 font-mono text-[11px] text-slate-300">
                          {p.missing_apis?.length > 0 ? p.missing_apis.join(', ') : 'All required APIs connected'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        )}
      </main>
    </div>
  );
}

export function App() {
  const [signedIn, setSignedIn] = useState<boolean>(() => restoreSession());
  const [authNotice, setAuthNotice] = useState<string | null>(null);
  useEffect(() => onUnauthorized(() => { setSignedIn(false); setAuthNotice('Session expired. Please sign in again.'); }), []);
  if (!signedIn) {
    return <LoginScreen notice={authNotice} onSignedIn={() => { setAuthNotice(null); setSignedIn(true); }} />;
  }
  return <AuthenticatedApp onSignOut={() => { setSignedIn(false); setAuthNotice('Signed out.'); }} />;
}

export default App;