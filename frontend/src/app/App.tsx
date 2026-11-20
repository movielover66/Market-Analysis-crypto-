import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ApiError,
  apiGet,
  apiPost,
  apiPut,
  fetchDemoOverview,
  getAuthToken,
  getTokenExpiryMs,
  isUnauthorizedError,
  logout,
  onUnauthorized,
  restoreSession,
} from '../lib/api';
import { TechnicalChartCanvas } from '../charts/TechnicalChartCanvas';
import { LoginScreen } from '../components/LoginScreen';

type SectionId = 'desk' | 'scanner' | 'portfolio' | 'chart' | 'news' | 'system';
type HorizonId = 'intraday' | 'swing' | 'positional' | 'indices';
type ScanStatusFilter = 'ALL' | 'CONFIRMED' | 'WATCHLIST' | 'REJECTED';
type ScanHorizonFilter = 'ALL' | 'intraday' | 'swing' | 'positional';

function errMsg(ex: unknown, fallback: string): string {
  if (ex instanceof ApiError) return ex.message || fallback;
  if (ex instanceof Error) return ex.message || fallback;
  return fallback;
}

function fmtINR(v: unknown): string {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n)) return '₹0.00';
  const sign = n < 0 ? '-' : '';
  return `${sign}₹${Math.abs(n).toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function fmtNum(v: unknown, digits = 2): string {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n)) return '0.00';
  return n.toLocaleString('en-IN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function inferHorizonBucket(engineCode?: string, tradingMode?: string, explicit?: string): 'intraday' | 'swing' | 'positional' {
  if (explicit === 'intraday' || explicit === 'swing' || explicit === 'positional') return explicit;
  const ec = String(engineCode || '').toUpperCase();
  const tm = String(tradingMode || '').toUpperCase();
  if (ec.includes('ENGINE_1') || ec.includes('ORB') || tm === 'INTRADAY') return 'intraday';
  if (ec.includes('ENGINE_2B') || ec.includes('RSI2')) return 'swing';
  if (ec.includes('ENGINE_2A') || ec.includes('VCP')) return 'positional';
  return 'swing';
}

function inferHoldLabel(engineCode?: string, tradingMode?: string, explicit?: string): string {
  if (explicit) return explicit;
  const bucket = inferHorizonBucket(engineCode, tradingMode);
  if (bucket === 'intraday') return 'Intraday • 2–5 hrs (Auto-Exit 3:15 PM IST)';
  if (bucket === 'positional') return 'Positional • 1–4 Weeks';
  return 'Short Swing • 2–4 Trading Days';
}

export const App: React.FC = () => {
  const [signedIn, setSignedIn] = useState<boolean>(() => restoreSession());
  const [sessionNotice, setSessionNotice] = useState<string | null>(null);

  useEffect(() => {
    return onUnauthorized(() => {
      setSignedIn(false);
      setSessionNotice('Your session expired. Please enter your owner password to continue.');
    });
  }, []);

  useEffect(() => {
    if (!signedIn) return;
    const exp = getTokenExpiryMs();
    if (!exp) return;
    const ms = exp - Date.now();
    if (ms <= 0) {
      logout();
      setSignedIn(false);
      setSessionNotice('Your session expired. Please sign in again.');
      return;
    }
    const timer = setTimeout(() => {
      logout();
      setSignedIn(false);
      setSessionNotice('Your session expired. Please sign in again.');
    }, ms);
    return () => clearTimeout(timer);
  }, [signedIn]);

  if (!signedIn) {
    return (
      <LoginScreen
        notice={sessionNotice}
        onAuthenticated={() => {
          setSessionNotice(null);
          setSignedIn(true);
        }}
      />
    );
  }

  return (
    <AuthenticatedApp
      onLogout={() => {
        logout();
        setSignedIn(false);
        setSessionNotice(null);
      }}
    />
  );
};

interface AuthenticatedAppProps {
  onLogout: () => void;
}

const AuthenticatedApp: React.FC<AuthenticatedAppProps> = ({ onLogout }) => {
  // Navigation & Drawer
  const [section, setSection] = useState<SectionId>('desk');
  const [horizon, setHorizon] = useState<HorizonId>('intraday');
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Core Live Data
  const [indices, setIndices] = useState<any[]>([]);
  const [overview, setOverview] = useState<any>(null);
  const [ledgerData, setLedgerData] = useState<any>(null);
  const [autoStatus, setAutoStatus] = useState<any>(null);
  const [connectivity, setConnectivity] = useState<any>(null);
  const [engineScan, setEngineScan] = useState<any>(null);
  const [wsLive, setWsLive] = useState(false);
  const [lastTickIst, setLastTickIst] = useState<string>('');

  // Scanner Filters
  const [scanStatusFilter, setScanStatusFilter] = useState<ScanStatusFilter>('ALL');
  const [scanHorizonFilter, setScanHorizonFilter] = useState<ScanHorizonFilter>('ALL');

  // Chart & Depth
  const [chartSymbol, setChartSymbol] = useState('RELIANCE');
  const [chartTf, setChartTf] = useState('15m');
  const [candles, setCandles] = useState<any[]>([]);
  const [indicators, setIndicators] = useState<any>(null);
  const [depthData, setDepthData] = useState<any>(null);
  const [chartLoading, setChartLoading] = useState(false);

  // News, AI & Journal
  const [newsData, setNewsData] = useState<any>(null);
  const [morningBrief, setMorningBrief] = useState<any>(null);
  const [aiQuestion, setAiQuestion] = useState('');
  const [aiAnswer, setAiAnswer] = useState<any>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [journalList, setJournalList] = useState<any[]>([]);
  const [journalNote, setJournalNote] = useState('');

  // Config & Actions
  const [cfgBalance, setCfgBalance] = useState('100000');
  const [cfgPerTrade, setCfgPerTrade] = useState('20000');
  const [cfgMaxPos, setCfgMaxPos] = useState('4');
  const [cfgDailyLoss, setCfgDailyLoss] = useState('1.50');
  const [cfgEnabled, setCfgEnabled] = useState(true);
  const [cfgE1, setCfgE1] = useState(true);
  const [cfgE2a, setCfgE2a] = useState(true);
  const [cfgE2b, setCfgE2b] = useState(true);
  const [cfgNewsShield, setCfgNewsShield] = useState(true);

  const [scanning, setScanning] = useState(false);
  const [savingCfg, setSavingCfg] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const notify = useCallback((msg: string) => {
    setToast(msg);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3200);
    return () => clearTimeout(t);
  }, [toast]);

  // Core data loader
  const loadCore = useCallback(async () => {
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
  }, [notify]);

  useEffect(() => {
    loadCore();
    const timer = setInterval(loadCore, 30000);
    return () => clearInterval(timer);
  }, [loadCore]);

  // Real-Time 500ms WebSocket Live Ticker (/api/v1/ws/live)
  useEffect(() => {
    const token = getAuthToken();
    if (!token || typeof window === 'undefined') return;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let syncGraceTimer: ReturnType<typeof setTimeout> | null = null;
    let closedByUnmount = false;

    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws = new WebSocket(`${proto}//${window.location.host}/api/v1/ws/live?token=${encodeURIComponent(token)}`);
      ws.onopen = () => {
        if (syncGraceTimer) { clearTimeout(syncGraceTimer); syncGraceTimer = null; }
        setWsLive(true);
      };
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
        if (!syncGraceTimer) {
          syncGraceTimer = setTimeout(() => setWsLive(false), 3500);
        }
        if (!closedByUnmount) {
          reconnectTimer = setTimeout(connect, 1500);
        }
      };
      ws.onerror = () => {
        try {
          ws?.close();
        } catch {
          /* ignore */
        }
      };
    };

    connect();
    return () => {
      closedByUnmount = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (syncGraceTimer) clearTimeout(syncGraceTimer);
      try {
        ws?.close();
      } catch {
        /* ignore */
      }
    };
  }, []);

  // Section-specific data fetches
  useEffect(() => {
    if (section === 'scanner') {
      apiGet<any>('/api/v1/engines/scan?limit=12').then(setEngineScan).catch(() => {});
    }
    if (section === 'news') {
      apiGet<any>('/api/v1/news').then(setNewsData).catch(() => {});
      apiGet<any>('/api/v1/assistant/morning-brief').then(setMorningBrief).catch(() => {});
      apiGet<any>('/api/v1/journal').then((r) => setJournalList(r?.entries || [])).catch(() => {});
    }
  }, [section]);

  useEffect(() => {
    if (section !== 'chart') return;
    setChartLoading(true);
    Promise.all([
      apiGet<any>(`/api/v1/market/candles/${encodeURIComponent(chartSymbol)}?timeframe=${encodeURIComponent(chartTf)}&limit=60`).catch(() => null),
      apiGet<any>(`/api/v1/market/indicators/${encodeURIComponent(chartSymbol)}?timeframe=${encodeURIComponent(chartTf)}`).catch(() => null),
      apiGet<any>(`/api/v1/market/depth/${encodeURIComponent(chartSymbol)}`).catch(() => null),
    ]).then(([cRes, iRes, dRes]) => {
      setCandles(cRes?.candles || []);
      setIndicators(iRes?.indicators || null);
      setDepthData(dRes || null);
      setChartLoading(false);
    });
  }, [section, chartSymbol, chartTf]);

  // Actions
  const handleRunCycle = async (executeEntries: boolean) => {
    setScanning(true);
    try {
      const res = await apiPost<any>('/api/v1/auto-trader/run', { execute_entries: executeEntries, limit: 12 });
      const opened = (res?.opened_trades || []).length;
      const ev = res?.signals_evaluated ?? 0;
      const conf = res?.confirmed_signals ?? 0;
      notify(`Scan complete: ${ev} setups evaluated • ${conf} confirmed • ${opened} auto-trade(s) opened`);
      await loadCore();
      apiGet<any>('/api/v1/engines/scan?limit=12').then(setEngineScan).catch(() => {});
    } catch (ex) {
      notify(errMsg(ex, 'Auto-trader scan failed'));
    } finally {
      setScanning(false);
    }
  };

  const handleSaveConfig = async (resetKillSwitch = false) => {
    setSavingCfg(true);
    try {
      await apiPut('/api/v1/auto-trader/config', {
        enabled: cfgEnabled,
        starting_balance_inr: Number(cfgBalance) || 100000,
        per_trade_amount_inr: Number(cfgPerTrade) || 20000,
        max_open_positions: Number(cfgMaxPos) || 4,
        daily_loss_limit_pct: Number(cfgDailyLoss) || 1.5,
        enable_engine_1_orb: cfgE1,
        enable_engine_2a_vcp: cfgE2a,
        enable_engine_2b_rsi2: cfgE2b,
        enable_news_shield: cfgNewsShield,
        reset_kill_switch: resetKillSwitch,
      });
      notify(resetKillSwitch ? 'Kill-switch reset and settings saved' : 'Paper trading settings saved');
      await loadCore();
    } catch (ex) {
      notify(errMsg(ex, 'Could not update settings'));
    } finally {
      setSavingCfg(false);
    }
  };

  const handleClosePosition = async (posId: string, sym: string) => {
    try {
      await apiPost(`/api/v1/paper/positions/${encodeURIComponent(posId)}/close`, { exit_reason: 'MANUAL_UI_CLOSE' });
      notify(`Closed paper position for ${sym}`);
      await loadCore();
    } catch (ex) {
      notify(errMsg(ex, `Could not close ${sym}`));
    }
  };

  const handleDailySummary = async (sendTg: boolean) => {
    try {
      await apiPost<any>('/api/v1/auto-trader/daily-summary', { send_telegram: sendTg });
      notify(sendTg ? 'Daily Paper Summary sent to Telegram' : 'Daily Paper Summary refreshed');
    } catch (ex) {
      notify(errMsg(ex, 'Summary generation failed'));
    }
  };

  const handleAskAi = async () => {
    if (!aiQuestion.trim()) return;
    setAiLoading(true);
    try {
      const r = await apiPost<any>('/api/v1/assistant/query', {
        question: aiQuestion.trim(),
        symbol: chartSymbol,
        trading_mode: 'INTRADAY',
      });
      setAiAnswer(r);
    } catch (ex) {
      notify(errMsg(ex, 'AI assistant error'));
    } finally {
      setAiLoading(false);
    }
  };

  // Derived data (with safe fallbacks so 0-out-of-0 NEVER appears!)
  const quotes: any[] = overview?.data?.quotes || [];
  const ledger = ledgerData?.ledger || autoStatus?.ledger || {};
  const openPositions: any[] = ledger?.open_positions_live || ledger?.open_positions || [];
  const closedTrades: any[] = ledgerData?.closed_trades || [];

  const watchedSymbols: string[] = autoStatus?.watched_symbols?.length
    ? autoStatus.watched_symbols
    : quotes.map((q: any) => q.symbol).filter(Boolean);
  const totalUniverseCount: number = autoStatus?.total_universe_count || watchedSymbols.length;

  const allSignals: any[] = useMemo(() => {
    const raw = (engineScan?.signals?.length ? engineScan.signals : autoStatus?.recent_signals) || [];
    return raw.map((s: any) => ({
      ...s,
      horizon_bucket: inferHorizonBucket(s.engine_code, s.trading_mode, s.horizon_bucket),
      hold_horizon: inferHoldLabel(s.engine_code, s.trading_mode, s.hold_horizon),
    }));
  }, [engineScan, autoStatus]);

  const scanStats = useMemo(() => {
    const confirmed = autoStatus?.scan_stats?.confirmed_count ?? allSignals.filter((s) => s.status === 'CONFIRMED').length;
    const watchlist = autoStatus?.scan_stats?.watchlist_count ?? allSignals.filter((s) => s.status === 'WATCHLIST').length;
    const rejected = autoStatus?.scan_stats?.rejected_count ?? allSignals.filter((s) => s.status === 'REJECTED').length;
    return {
      watched: watchedSymbols.length,
      universe: totalUniverseCount,
      confirmed,
      watchlist,
      rejected,
    };
  }, [autoStatus, allSignals, watchedSymbols.length, totalUniverseCount]);

  // API Health — ONLY show red alert pill on front screen if an API is genuinely down
  const apiChecks = useMemo(() => {
    const c = connectivity?.checks || connectivity?.services || {};
    const items = [
      { key: 'angel_one', name: 'Angel One SmartAPI', ok: c?.angel_one?.ok ?? c?.angel_one?.connected ?? true, detail: c?.angel_one?.detail || 'Live NSE EQ & Indices Feed' },
      { key: 'websocket', name: 'Live WebSocket (500ms)', ok: wsLive, detail: wsLive ? 'Streaming real-time ticks' : 'Reconnecting...' },
      { key: 'gemini', name: 'Gemini AI & News Shield', ok: c?.gemini?.ok ?? c?.gemini?.configured ?? true, detail: c?.gemini?.detail || 'Sentiment & Q&A active' },
      { key: 'supabase', name: 'Supabase Paper Ledger', ok: c?.supabase?.ok ?? c?.supabase?.configured ?? true, detail: c?.supabase?.detail || 'Cloud persistence' },
      { key: 'telegram', name: 'Telegram Bot Alerts', ok: c?.telegram?.ok ?? c?.telegram?.configured ?? true, detail: c?.telegram?.detail || 'Emoji + Hold Timeframe alerts' },
    ];
    return items;
  }, [connectivity, wsLive]);

  const anyBackendApiDown = apiChecks.some((a) => a.key !== 'websocket' && !a.ok);

  const openByHorizon = useMemo(() => {
    return {
      intraday: openPositions.filter((p) => inferHorizonBucket(p.engine_code, p.trading_mode, p.horizon_bucket) === 'intraday'),
      swing: openPositions.filter((p) => inferHorizonBucket(p.engine_code, p.trading_mode, p.horizon_bucket) === 'swing'),
      positional: openPositions.filter((p) => inferHorizonBucket(p.engine_code, p.trading_mode, p.horizon_bucket) === 'positional'),
    };
  }, [openPositions]);

  const totalNetPnl = Number(ledger?.realised_net_pnl_inr ?? 0) + Number(ledger?.unrealised_net_pnl_inr ?? 0);
  const regime = autoStatus?.last_regime;

  const sectionLabelMap: Record<SectionId, string> = {
    desk: 'Trading Desk • Paper Mode',
    scanner: 'Live Scanner Telemetry',
    portfolio: 'Paper Portfolio & P&L',
    chart: 'Live Chart & 5-Level Depth',
    news: 'News, AI & Journal',
    system: 'System & APIs',
  };

  return (
    <div className="min-h-screen flex flex-col text-slate-100" style={{ backgroundColor: '#06080D' }}>
      {/* Clean Single-Row Top Header (NO API Status Clutter on Front Screen) */}
      <header
        className="sticky top-0 z-30 border-b"
        style={{ backgroundColor: 'rgba(6, 8, 13, 0.96)', borderColor: '#1E293B', backdropFilter: 'blur(10px)' }}
      >
        <div className="max-w-5xl mx-auto px-3 sm:px-5 h-14 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <button
              onClick={() => setDrawerOpen(true)}
              aria-label="Open Menu"
              className="w-10 h-10 rounded-xl flex items-center justify-center text-white border transition active:scale-95 shrink-0"
              style={{ backgroundColor: '#0F1420', borderColor: '#243045' }}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="2.2" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-base sm:text-lg font-extrabold tracking-tight text-white">
                  TRADE<span style={{ color: '#00E676' }}>IQ</span>
                </span>
                <span
                  className="text-[10px] font-bold px-1.5 py-0.5 rounded border"
                  style={{ backgroundColor: 'rgba(0,230,118,0.14)', color: '#00E676', borderColor: 'rgba(0,230,118,0.35)' }}
                >
                  v2.0
                </span>
              </div>
              <p className="text-[11px] text-slate-400 truncate leading-none mt-0.5">{sectionLabelMap[section]}</p>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {anyBackendApiDown && (
              <button
                onClick={() => setSection('system')}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-bold border animate-pulse"
                style={{ backgroundColor: 'rgba(255,59,92,0.15)', borderColor: 'rgba(255,59,92,0.45)', color: '#FF3B5C' }}
              >
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: '#FF3B5C' }} />
                <span>API Issue</span>
              </button>
            )}

            <div
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl border"
              style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}
            >
              <span
                className="w-2 h-2 rounded-full"
                style={{ backgroundColor: wsLive ? '#00E676' : '#FFB800' }}
              />
              <span className="text-xs font-bold font-mono" style={{ color: wsLive ? '#00E676' : '#FFB800' }}>
                {wsLive ? 'LIVE' : 'SYNC'}
              </span>
              {lastTickIst && (
                <span className="hidden sm:inline text-[11px] text-slate-400 font-mono">
                  {String(lastTickIst).slice(11, 19)} IST
                </span>
              )}
            </div>

            <button
              onClick={() => handleRunCycle(true)}
              disabled={scanning}
              className="px-3 py-1.5 rounded-xl font-bold text-xs transition active:scale-95 disabled:opacity-60"
              style={{ backgroundColor: '#00E676', color: '#04120B' }}
            >
              {scanning ? 'Scanning...' : 'Scan'}
            </button>
          </div>
        </div>

        {/* Stock-Market Style Indices Ticker Bar: SENSEX | NIFTY 50 | BANKNIFTY */}
        <div className="border-t" style={{ borderColor: '#151D2E', backgroundColor: '#090D16' }}>
          <div className="max-w-5xl mx-auto px-3 sm:px-5 py-2 flex items-center gap-2 sm:gap-3 overflow-x-auto">
            {(indices.length ? indices : [
              { symbol: 'SENSEX', ltp: 0, change_pct: 0 },
              { symbol: 'NIFTY 50', ltp: 0, change_pct: 0 },
              { symbol: 'BANKNIFTY', ltp: 0, change_pct: 0 },
            ]).map((idx: any) => {
              const chg = Number(idx.change_pct ?? idx.pct_change ?? 0);
              const up = chg >= 0;
              return (
                <button
                  key={idx.symbol || idx.name}
                  onClick={() => {
                    setSection('desk');
                    setHorizon('indices');
                  }}
                  className="shrink-0 flex items-center gap-2 px-3 py-1.5 rounded-xl border transition text-left"
                  style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}
                >
                  <span className="text-[11px] font-bold tracking-wide text-slate-300">
                    {idx.symbol || idx.name}
                  </span>
                  <span className="text-xs font-bold text-white font-mono">
                    {Number(idx.ltp || 0) > 0 ? fmtNum(idx.ltp, 2) : '—'}
                  </span>
                  <span
                    className="text-[11px] font-bold font-mono px-1.5 py-0.5 rounded-md border"
                    style={{
                      color: up ? '#00E676' : '#FF3B5C',
                      backgroundColor: up ? 'rgba(0,230,118,0.1)' : 'rgba(255,59,92,0.1)',
                      borderColor: up ? 'rgba(0,230,118,0.28)' : 'rgba(255,59,92,0.28)',
                    }}
                  >
                    {up ? '+' : ''}
                    {chg.toFixed(2)}%
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </header>

      {/* Slide-out Navigation Drawer */}
      {drawerOpen && (
        <div className="fixed inset-0 z-50 flex">
          <div
            className="fixed inset-0"
            style={{ backgroundColor: 'rgba(2, 6, 15, 0.75)', backdropFilter: 'blur(6px)' }}
            onClick={() => setDrawerOpen(false)}
          />
          <aside
            className="relative z-10 w-72 max-w-[85vw] h-full border-r flex flex-col justify-between p-4 shadow-2xl overflow-y-auto"
            style={{ backgroundColor: '#0A0E17', borderColor: '#1E293B' }}
          >
            <div>
              <div className="flex items-center justify-between pb-4 border-b" style={{ borderColor: '#1E293B' }}>
                <div>
                  <div className="text-lg font-extrabold text-white">
                    TRADE<span style={{ color: '#00E676' }}>IQ</span>{' '}
                    <span className="text-xs font-semibold text-slate-400">MENU</span>
                  </div>
                  <div className="text-[11px] text-slate-400 mt-0.5">NSE Cash Equity • Paper Desk</div>
                </div>
                <button
                  onClick={() => setDrawerOpen(false)}
                  className="w-8 h-8 rounded-lg text-slate-300 hover:text-white flex items-center justify-center"
                  style={{ backgroundColor: '#151C2C' }}
                >
                  ✕
                </button>
              </div>

              <nav className="mt-4 space-y-1.5">
                {(
                  [
                    { id: 'desk', icon: '⚡', label: 'Trading Desk', sub: 'Intraday • Swing • Positional • Indices' },
                    { id: 'scanner', icon: '📡', label: 'Live Scanner Telemetry', sub: `${scanStats.watched}/${scanStats.universe} NSE Stocks • Rules` },
                    { id: 'portfolio', icon: '💼', label: 'Paper Portfolio & P&L', sub: 'Balance, Open Trades & Ledger' },
                    { id: 'chart', icon: '📊', label: 'Live Chart & 5-Level Depth', sub: 'Candlesticks + Order Book' },
                    { id: 'news', icon: '📰', label: 'News, AI & Journal', sub: 'Gemini Shield + Morning Brief' },
                    {
                      id: 'system',
                      icon: '⚙️',
                      label: 'System & APIs',
                      sub: anyBackendApiDown ? 'Check API Status' : 'All APIs Connected',
                    },
                  ] as Array<{ id: SectionId; icon: string; label: string; sub: string }>
                ).map((item) => {
                  const active = section === item.id;
                  return (
                    <button
                      key={item.id}
                      onClick={() => {
                        setSection(item.id);
                        setDrawerOpen(false);
                      }}
                      className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-left border transition"
                      style={{
                        backgroundColor: active ? 'rgba(0,230,118,0.14)' : 'transparent',
                        borderColor: active ? 'rgba(0,230,118,0.4)' : 'transparent',
                      }}
                    >
                      <span className="text-base">{item.icon}</span>
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-bold text-white truncate">{item.label}</div>
                        <div className="text-[11px] text-slate-400 truncate">{item.sub}</div>
                      </div>
                    </button>
                  );
                })}
              </nav>

              <div className="mt-5 pt-4 border-t" style={{ borderColor: '#1E293B' }}>
                <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500 px-2 mb-2">
                  Trading Horizons
                </div>
                <div className="space-y-1">
                  {(
                    [
                      { id: 'intraday', label: '⚡ Intraday (Today)', badge: 'Exit 3:15 PM', color: '#00E676' },
                      { id: 'swing', label: '🌊 Short Swing', badge: '2–4 Days', color: '#00D4FF' },
                      { id: 'positional', label: '🏔️ Positional / Long', badge: '1–4 Weeks', color: '#FFB800' },
                      { id: 'indices', label: '📈 Indices & Regime', badge: 'SENSEX / NIFTY', color: '#C084FC' },
                    ] as Array<{ id: HorizonId; label: string; badge: string; color: string }>
                  ).map((sc) => (
                    <button
                      key={sc.id}
                      onClick={() => {
                        setSection('desk');
                        setHorizon(sc.id);
                        setDrawerOpen(false);
                      }}
                      className="w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs font-medium text-slate-300 hover:bg-slate-800/60"
                    >
                      <span>{sc.label}</span>
                      <span className="text-[10px] font-bold" style={{ color: sc.color }}>
                        {sc.badge}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="pt-4 border-t space-y-2.5" style={{ borderColor: '#1E293B' }}>
              <div className="flex items-center justify-between px-2 text-xs">
                <span className="text-slate-400">Scheduler</span>
                <span className="font-bold" style={{ color: autoStatus?.scheduler_active ? '#00E676' : '#FFB800' }}>
                  {autoStatus?.scheduler_active ? 'AUTO ON' : 'READY'}
                </span>
              </div>
              <button
                onClick={onLogout}
                className="w-full py-2 rounded-xl text-xs font-bold border text-slate-300 hover:text-white"
                style={{ backgroundColor: '#151C2C', borderColor: '#263449' }}
              >
                Lock / Sign Out
              </button>
            </div>
          </aside>
        </div>
      )}

      {/* Toast Notification */}
      {toast && (
        <div
          className="fixed bottom-4 right-4 z-50 max-w-xs px-4 py-3 rounded-xl shadow-2xl text-xs font-semibold text-white border"
          style={{ backgroundColor: '#151C2C', borderColor: 'rgba(0,230,118,0.45)' }}
        >
          {toast}
        </div>
      )}

      {/* Main Content */}
      <main className="flex-1 max-w-5xl w-full mx-auto px-3 sm:px-5 py-4 space-y-4">
        {/* ================= SECTION 1: TRADING DESK ================= */}
        {section === 'desk' && (
          <>
            {/* Compact 4-Card KPI Strip */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5 sm:gap-3">
              <div className="p-3.5 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
                <div className="text-[11px] font-medium text-slate-400">Total Live Equity</div>
                <div className="text-lg sm:text-xl font-extrabold text-white font-mono mt-1">
                  {fmtINR(ledger?.total_equity_inr ?? autoStatus?.config?.starting_balance_inr ?? 100000)}
                </div>
                <div className="text-[10px] text-slate-500 mt-0.5">
                  Start: {fmtINR(autoStatus?.config?.starting_balance_inr ?? 100000)}
                </div>
              </div>

              <div className="p-3.5 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
                <div className="text-[11px] font-medium text-slate-400">Total Live Net P&L</div>
                <div
                  className="text-lg sm:text-xl font-extrabold font-mono mt-1"
                  style={{ color: totalNetPnl >= 0 ? '#00E676' : '#FF3B5C' }}
                >
                  {totalNetPnl >= 0 ? '+' : ''}
                  {fmtINR(totalNetPnl)}
                </div>
                <div className="text-[10px] text-slate-500 mt-0.5">After NSE charges</div>
              </div>

              <div className="p-3.5 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
                <div className="text-[11px] font-medium text-slate-400">Available Cash</div>
                <div className="text-lg sm:text-xl font-extrabold font-mono mt-1" style={{ color: '#00D4FF' }}>
                  {fmtINR(ledger?.available_cash_inr ?? 0)}
                </div>
                <div className="text-[10px] text-slate-500 mt-0.5">
                  Deployed: {fmtINR(ledger?.deployed_capital_inr ?? 0)}
                </div>
              </div>

              <div className="p-3.5 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
                <div className="text-[11px] font-medium text-slate-400">Live Accuracy</div>
                <div className="text-lg sm:text-xl font-extrabold font-mono mt-1" style={{ color: '#00E676' }}>
                  {ledger?.accuracy_pct ?? '0.0'}%
                </div>
                <div className="text-[10px] text-slate-500 mt-0.5">
                  {ledger?.wins ?? 0}W / {ledger?.losses ?? 0}L • PF {ledger?.profit_factor ?? '0.00'}
                </div>
              </div>
            </div>

            {/* Live Auto-Trader Scanner Telemetry Strip (FIXED: Never shows 0 out of 0!) */}
            <div
              className="p-3.5 sm:p-4 rounded-2xl border"
              style={{
                backgroundColor: '#0F1420',
                borderColor: 'rgba(0,230,118,0.28)',
                boxShadow: '0 0 22px rgba(0,230,118,0.08)',
              }}
            >
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="w-2 h-2 rounded-full" style={{ backgroundColor: '#00E676' }} />
                    <span className="text-xs sm:text-sm font-extrabold text-white">
                      Auto-Trader Radar • {scanStats.watched} / {scanStats.universe} NSE Stocks Active
                    </span>
                    <span
                      className="text-[10px] font-bold px-2 py-0.5 rounded-full border"
                      style={{
                        backgroundColor: autoStatus?.scheduler_active ? 'rgba(0,230,118,0.14)' : 'rgba(255,184,0,0.14)',
                        color: autoStatus?.scheduler_active ? '#00E676' : '#FFB800',
                        borderColor: autoStatus?.scheduler_active ? 'rgba(0,230,118,0.35)' : 'rgba(255,184,0,0.35)',
                      }}
                    >
                      {autoStatus?.scheduler_active ? 'AUTO SCHEDULER ON (09:00–15:30 IST)' : 'LIVE SCANNER READY'}
                    </span>
                  </div>
                  <div className="text-[11px] text-slate-400 mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span>
                      Last Scan:{' '}
                      <strong className="text-slate-200 font-mono">
                        {autoStatus?.last_cycle_ist ? `${String(autoStatus.last_cycle_ist).slice(11, 19)} IST` : 'Ready'}
                      </strong>
                    </span>
                    <span style={{ color: '#00E676' }} className="font-semibold">
                      ● {scanStats.confirmed} Confirmed
                    </span>
                    <span style={{ color: '#FFB800' }} className="font-semibold">
                      ● {scanStats.watchlist} Watchlist
                    </span>
                    <span className="text-slate-400">● {scanStats.rejected} Rejected</span>
                    <span className="text-slate-300">● {openPositions.length} Active Trade(s)</span>
                  </div>
                </div>
                <button
                  onClick={() => setSection('scanner')}
                  className="shrink-0 px-3.5 py-2 rounded-xl border text-xs font-bold transition"
                  style={{ backgroundColor: '#151C2C', borderColor: '#2B3B55', color: '#00D4FF' }}
                >
                  Inspect All Scans →
                </button>
              </div>
            </div>

            {/* 4 Clean Horizon Sub-Tabs */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {(
                [
                  { id: 'intraday', label: 'Intraday', sub: 'Today • 3:15 PM', count: openByHorizon.intraday.length, color: '#00E676' },
                  { id: 'swing', label: 'Short Swing', sub: '2–4 Days', count: openByHorizon.swing.length, color: '#00D4FF' },
                  { id: 'positional', label: 'Positional', sub: '1–4 Weeks', count: openByHorizon.positional.length, color: '#FFB800' },
                  { id: 'indices', label: 'Indices', sub: 'Regime Gate', count: indices.length || 3, color: '#C084FC' },
                ] as Array<{ id: HorizonId; label: string; sub: string; count: number; color: string }>
              ).map((tab) => {
                const active = horizon === tab.id;
                return (
                  <button
                    key={tab.id}
                    onClick={() => setHorizon(tab.id)}
                    className="p-3 rounded-2xl border text-left transition active:scale-[0.98]"
                    style={{
                      backgroundColor: active ? '#151C2C' : '#0F1420',
                      borderColor: active ? tab.color : '#1E293B',
                    }}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs sm:text-sm font-extrabold text-white">{tab.label}</span>
                      <span
                        className="text-[11px] font-bold font-mono px-1.5 py-0.5 rounded-md"
                        style={{ backgroundColor: 'rgba(0,0,0,0.35)', color: active ? tab.color : '#94A3B8' }}
                      >
                        {tab.count}
                      </span>
                    </div>
                    <div className="text-[10px] text-slate-400 mt-0.5">{tab.sub}</div>
                  </button>
                );
              })}
            </div>

            {/* Active Horizon Content */}
            {horizon === 'indices' ? (
              <div className="space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  {indices.map((idx: any) => {
                    const chg = Number(idx.change_pct ?? idx.pct_change ?? 0);
                    const up = chg >= 0;
                    return (
                      <div key={idx.symbol} className="p-4 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-bold text-slate-400">{idx.symbol}</span>
                          <span
                            className="text-[10px] font-bold px-2 py-0.5 rounded-full"
                            style={{ backgroundColor: 'rgba(0,230,118,0.14)', color: '#00E676' }}
                          >
                            LIVE
                          </span>
                        </div>
                        <div className="text-2xl font-extrabold text-white font-mono mt-2">{fmtNum(idx.ltp, 2)}</div>
                        <div className="text-xs font-bold font-mono mt-1" style={{ color: up ? '#00E676' : '#FF3B5C' }}>
                          {up ? '▲ +' : '▼ '}
                          {chg.toFixed(2)}% today
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="p-4 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <div>
                      <div className="text-sm font-extrabold text-white">Engine 0 • Market Regime Gate</div>
                      <div className="text-xs text-slate-400 mt-0.5">
                        {regime?.summary || 'Monitors NIFTY 50, BANKNIFTY, SENSEX breadth and volatility before allowing auto entries.'}
                      </div>
                    </div>
                    <span
                      className="px-3 py-1 rounded-full text-xs font-bold border"
                      style={{
                        backgroundColor: 'rgba(0,230,118,0.14)',
                        borderColor: 'rgba(0,230,118,0.4)',
                        color: '#00E676',
                      }}
                    >
                      {regime?.regime || 'TRENDING'}
                    </span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                {/* Horizon Info Header */}
                <div
                  className="p-3.5 rounded-2xl border flex flex-col sm:flex-row sm:items-center justify-between gap-2"
                  style={{ backgroundColor: '#151C2C', borderColor: '#263449' }}
                >
                  <div>
                    <div className="text-sm font-extrabold text-white">
                      {horizon === 'intraday'
                        ? 'Intraday Desk (Today)'
                        : horizon === 'swing'
                        ? 'Short Swing Desk (2–4 Days)'
                        : 'Positional / Long-Term Desk (1–4 Weeks)'}
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5">
                      {horizon === 'intraday'
                        ? 'Engine 1: 15m ORB + VWAP + Volume Surge + 5-Level Buy/Sell Depth'
                        : horizon === 'swing'
                        ? 'Engine 2B: Connors RSI(2) Mean-Reversion Pullback above 200-SMA'
                        : 'Engine 2A: Mark Minervini Stage-2 Trend Template + VCP Breakout'}
                    </div>
                  </div>
                  <span
                    className="self-start sm:self-center text-[11px] font-bold px-2.5 py-1 rounded-lg border"
                    style={{
                      color: horizon === 'intraday' ? '#00E676' : horizon === 'swing' ? '#00D4FF' : '#FFB800',
                      borderColor: horizon === 'intraday' ? 'rgba(0,230,118,0.35)' : horizon === 'swing' ? 'rgba(0,212,255,0.35)' : 'rgba(255,184,0,0.35)',
                      backgroundColor: 'rgba(0,0,0,0.25)',
                    }}
                  >
                    ⏱{' '}
                    {horizon === 'intraday'
                      ? '2–5 hrs • Auto-Exit 3:15 PM IST'
                      : horizon === 'swing'
                      ? 'Hold Window: 2–4 Trading Days'
                      : 'Hold Window: 1–4 Weeks'}
                  </span>
                </div>

                {/* Active Paper Trades for This Horizon */}
                <div className="space-y-2.5">
                  <div className="flex items-center justify-between px-1">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
                      Active Paper Trades ({openByHorizon[horizon].length})
                    </h3>
                    <span className="text-[11px] text-slate-500">Net P&L after NSE charges</span>
                  </div>

                  {openByHorizon[horizon].length === 0 ? (
                    <div
                      className="p-5 rounded-2xl border text-center"
                      style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}
                    >
                      <div className="text-sm font-bold text-slate-300">No open {horizon} trades right now</div>
                      <div className="text-xs text-slate-500 mt-1">
                        The bot opens paper trades automatically when a setup passes all rules and News Shield.
                      </div>
                    </div>
                  ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {openByHorizon[horizon].map((pos: any) => {
                        const net = Number(pos.unrealised_net_inr ?? pos.unrealised_net_pnl_inr ?? pos.net_pnl_inr ?? 0);
                        const up = net >= 0;
                        const holdLabel = inferHoldLabel(pos.engine_code, pos.trading_mode, pos.hold_horizon);
                        return (
                          <div
                            key={pos.position_id}
                            className="p-4 rounded-2xl border"
                            style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}
                          >
                            <div className="flex items-start justify-between gap-2">
                              <div>
                                <div className="flex items-center gap-2">
                                  <span className="text-base font-extrabold text-white">{pos.symbol}</span>
                                  <span
                                    className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                                    style={{
                                      backgroundColor: pos.direction === 'LONG' ? 'rgba(0,230,118,0.14)' : 'rgba(255,59,92,0.14)',
                                      color: pos.direction === 'LONG' ? '#00E676' : '#FF3B5C',
                                    }}
                                  >
                                    {pos.direction} ×{pos.quantity}
                                  </span>
                                </div>
                                <div className="text-[11px] text-slate-400 mt-0.5">{pos.engine_code || pos.trading_mode}</div>
                              </div>
                              <div className="text-right">
                                <div
                                  className="text-base font-extrabold font-mono"
                                  style={{ color: up ? '#00E676' : '#FF3B5C' }}
                                >
                                  {up ? '+' : ''}
                                  {fmtINR(net)}
                                </div>
                                <div className="text-[10px] text-slate-500">Live Net P&L</div>
                              </div>
                            </div>

                            <div
                              className="mt-2.5 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-[11px] font-semibold"
                              style={{ backgroundColor: '#151C2C', borderColor: '#24324A', color: '#00D4FF' }}
                            >
                              <span>⏱</span>
                              <span>{holdLabel}</span>
                            </div>

                            <div
                              className="grid grid-cols-4 gap-2 mt-3 pt-3 border-t text-center"
                              style={{ borderColor: '#1E293B' }}
                            >
                              <div>
                                <div className="text-[10px] text-slate-500">Entry</div>
                                <div className="text-xs font-bold text-slate-200 font-mono">₹{fmtNum(pos.entry_fill_price)}</div>
                              </div>
                              <div>
                                <div className="text-[10px] text-slate-500">Live LTP</div>
                                <div className="text-xs font-bold text-white font-mono">
                                  ₹{fmtNum(pos.current_ltp ?? pos.live_ltp ?? pos.entry_fill_price)}
                                </div>
                              </div>
                              <div>
                                <div className="text-[10px] text-slate-500">Stop Loss</div>
                                <div className="text-xs font-bold font-mono" style={{ color: '#FF3B5C' }}>
                                  ₹{fmtNum(pos.stop_loss)}
                                </div>
                              </div>
                              <div>
                                <div className="text-[10px] text-slate-500">Target</div>
                                <div className="text-xs font-bold font-mono" style={{ color: '#00E676' }}>
                                  ₹{fmtNum(pos.target_price)}
                                </div>
                              </div>
                            </div>

                            <div className="mt-3 flex items-center gap-2">
                              <button
                                onClick={() => {
                                  setChartSymbol(pos.symbol);
                                  setSection('chart');
                                }}
                                className="flex-1 py-2 rounded-xl border text-xs font-bold text-white"
                                style={{ backgroundColor: '#151C2C', borderColor: '#2A3950' }}
                              >
                                📊 Live Chart & Depth
                              </button>
                              <button
                                onClick={() => handleClosePosition(pos.position_id, pos.symbol)}
                                className="px-3.5 py-2 rounded-xl border text-xs font-bold"
                                style={{
                                  backgroundColor: 'rgba(255,59,92,0.12)',
                                  borderColor: 'rgba(255,59,92,0.35)',
                                  color: '#FF3B5C',
                                }}
                              >
                                Exit Now
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Live NSE Market Pulse Cards (Clean Mobile-First Grid) */}
                <div className="p-4 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <div className="text-sm font-extrabold text-white">Live NSE Market Pulse ({quotes.length})</div>
                      <div className="text-[11px] text-slate-400">Real-time WebSocket LTP stream • Tap any stock for Chart & 5-Level Depth</div>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
                    {quotes.slice(0, 12).map((q: any) => {
                      const chg = Number(q.change_pct ?? q.pct_change ?? 0);
                      const up = chg >= 0;
                      return (
                        <button
                          key={q.symbol}
                          onClick={() => {
                            setChartSymbol(q.symbol);
                            setSection('chart');
                          }}
                          className="p-3 rounded-xl border flex items-center justify-between text-left transition hover:border-slate-500"
                          style={{ backgroundColor: '#0A0E17', borderColor: '#1B2436' }}
                        >
                          <div>
                            <div className="text-sm font-extrabold text-white">{q.symbol}</div>
                            <div className="text-[10px] text-slate-500">{q.sector || 'NSE_EQ'}</div>
                          </div>
                          <div className="text-right">
                            <div className="text-sm font-bold text-white font-mono">₹{fmtNum(q.ltp)}</div>
                            <div className="text-[11px] font-bold font-mono" style={{ color: up ? '#00E676' : '#FF3B5C' }}>
                              {up ? '+' : ''}
                              {chg.toFixed(2)}%
                            </div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        {/* ================= SECTION 2: LIVE SCANNER TELEMETRY ================= */}
        {section === 'scanner' && (
          <div className="space-y-4">
            <div className="p-4 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div>
                  <h2 className="text-base font-extrabold text-white">Live Scanner Telemetry (Full Transparency)</h2>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Every evaluated stock, its hold timeframe, score, and exact passed/failed rules.
                  </p>
                </div>
                <button
                  onClick={() => handleRunCycle(false)}
                  disabled={scanning}
                  className="self-start px-3.5 py-2 rounded-xl font-bold text-xs"
                  style={{ backgroundColor: '#00E676', color: '#04120B' }}
                >
                  {scanning ? 'Scanning...' : 'Refresh Live Scan'}
                </button>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                {(['ALL', 'CONFIRMED', 'WATCHLIST', 'REJECTED'] as ScanStatusFilter[]).map((f) => (
                  <button
                    key={f}
                    onClick={() => setScanStatusFilter(f)}
                    className="px-3 py-1.5 rounded-xl text-xs font-bold border"
                    style={{
                      backgroundColor: scanStatusFilter === f ? '#00E676' : '#151C2C',
                      color: scanStatusFilter === f ? '#04120B' : '#CBD5E1',
                      borderColor: scanStatusFilter === f ? '#00E676' : '#263449',
                    }}
                  >
                    {f}
                  </button>
                ))}
                {(
                  [
                    { id: 'ALL', label: 'All Timeframes' },
                    { id: 'intraday', label: 'Intraday' },
                    { id: 'swing', label: 'Short Swing' },
                    { id: 'positional', label: 'Positional' },
                  ] as Array<{ id: ScanHorizonFilter; label: string }>
                ).map((h) => (
                  <button
                    key={h.id}
                    onClick={() => setScanHorizonFilter(h.id)}
                    className="px-3 py-1.5 rounded-xl text-xs font-bold border"
                    style={{
                      backgroundColor: scanHorizonFilter === h.id ? 'rgba(0,212,255,0.16)' : '#0A0E17',
                      color: scanHorizonFilter === h.id ? '#00D4FF' : '#94A3B8',
                      borderColor: scanHorizonFilter === h.id ? '#00D4FF' : '#1E293B',
                    }}
                  >
                    {h.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-2.5">
              {allSignals
                .filter((s) => (scanStatusFilter === 'ALL' || s.status === scanStatusFilter) && (scanHorizonFilter === 'ALL' || s.horizon_bucket === scanHorizonFilter))
                .map((s, idx) => {
                  const statusColor =
                    s.status === 'CONFIRMED' ? '#00E676' : s.status === 'WATCHLIST' ? '#FFB800' : '#94A3B8';
                  return (
                    <div
                      key={`${s.symbol}-${s.engine_code}-${idx}`}
                      className="p-3.5 rounded-2xl border"
                      style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-sm font-extrabold text-white">{s.symbol}</span>
                            <span className="text-xs font-mono text-slate-300">Entry ₹{fmtNum(s.entry_price)}</span>
                            <span
                              className="text-[10px] font-bold px-2 py-0.5 rounded"
                              style={{ backgroundColor: '#151C2C', color: '#00D4FF' }}
                            >
                              {s.hold_horizon}
                            </span>
                          </div>
                          <div className="text-[11px] text-slate-500 mt-0.5">
                            {s.engine_name || s.engine_code} • Score {s.score}/100 • Net R:R {s.expected_net_rr}
                          </div>
                        </div>
                        <span
                          className="text-[10px] font-bold px-2.5 py-1 rounded-lg border shrink-0"
                          style={{ color: statusColor, borderColor: statusColor, backgroundColor: 'rgba(0,0,0,0.25)' }}
                        >
                          {s.status}
                        </span>
                      </div>

                      <div className="flex flex-wrap gap-1.5 mt-2.5">
                        {(s.passed_rules || []).map((r: string, i: number) => (
                          <span
                            key={`p-${i}`}
                            className="text-[11px] px-2 py-1 rounded-lg border"
                            style={{ backgroundColor: 'rgba(0,230,118,0.08)', borderColor: 'rgba(0,230,118,0.25)', color: '#A7F3D0' }}
                          >
                            ✅ {r}
                          </span>
                        ))}
                        {(s.failed_rules || []).map((r: string, i: number) => (
                          <span
                            key={`f-${i}`}
                            className="text-[11px] px-2 py-1 rounded-lg border"
                            style={{ backgroundColor: 'rgba(255,59,92,0.08)', borderColor: 'rgba(255,59,92,0.25)', color: '#FECDD3' }}
                          >
                            ❌ {r}
                          </span>
                        ))}
                      </div>
                    </div>
                  );
                })}
            </div>
          </div>
        )}

        {/* ================= SECTION 3: PAPER PORTFOLIO & CONFIG ================= */}
        {section === 'portfolio' && (
          <div className="space-y-4">
            <div className="p-4 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div>
                  <h2 className="text-base font-extrabold text-white">Paper Capital & Auto-Trader Controls</h2>
                  <p className="text-xs text-slate-400 mt-0.5">100% virtual paper balance — edit anytime.</p>
                </div>
                <button
                  onClick={() => handleDailySummary(true)}
                  className="px-3 py-1.5 rounded-xl border text-xs font-bold"
                  style={{ backgroundColor: 'rgba(0,212,255,0.12)', borderColor: 'rgba(0,212,255,0.35)', color: '#00D4FF' }}
                >
                  ✈️ Send Daily Summary to Telegram
                </button>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mt-4">
                <div>
                  <label className="text-[11px] font-semibold text-slate-400">Starting Balance (₹)</label>
                  <input
                    type="number"
                    value={cfgBalance}
                    onChange={(e) => setCfgBalance(e.target.value)}
                    className="mt-1 w-full px-3 py-2 rounded-xl border text-white font-mono text-sm"
                    style={{ backgroundColor: '#0A0E17', borderColor: '#24324A' }}
                  />
                </div>
                <div>
                  <label className="text-[11px] font-semibold text-slate-400">Per-Trade Amount (₹)</label>
                  <input
                    type="number"
                    value={cfgPerTrade}
                    onChange={(e) => setCfgPerTrade(e.target.value)}
                    className="mt-1 w-full px-3 py-2 rounded-xl border text-white font-mono text-sm"
                    style={{ backgroundColor: '#0A0E17', borderColor: '#24324A' }}
                  />
                </div>
                <div>
                  <label className="text-[11px] font-semibold text-slate-400">Max Open Positions</label>
                  <input
                    type="number"
                    value={cfgMaxPos}
                    onChange={(e) => setCfgMaxPos(e.target.value)}
                    className="mt-1 w-full px-3 py-2 rounded-xl border text-white font-mono text-sm"
                    style={{ backgroundColor: '#0A0E17', borderColor: '#24324A' }}
                  />
                </div>
                <div>
                  <label className="text-[11px] font-semibold text-slate-400">Daily Loss Limit (%)</label>
                  <input
                    type="number"
                    step="0.25"
                    value={cfgDailyLoss}
                    onChange={(e) => setCfgDailyLoss(e.target.value)}
                    className="mt-1 w-full px-3 py-2 rounded-xl border text-white font-mono text-sm"
                    style={{ backgroundColor: '#0A0E17', borderColor: '#24324A' }}
                  />
                </div>
              </div>

              <div className="flex flex-wrap gap-3 mt-4 text-xs">
                {[
                  { label: 'Auto-Trader Enabled', val: cfgEnabled, set: setCfgEnabled },
                  { label: 'Engine 1 (Intraday ORB)', val: cfgE1, set: setCfgE1 },
                  { label: 'Engine 2B (Short Swing RSI-2)', val: cfgE2b, set: setCfgE2b },
                  { label: 'Engine 2A (Positional VCP)', val: cfgE2a, set: setCfgE2a },
                  { label: 'Gemini News Shield', val: cfgNewsShield, set: setCfgNewsShield },
                ].map((t) => (
                  <label
                    key={t.label}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-xl border cursor-pointer"
                    style={{ backgroundColor: '#0A0E17', borderColor: '#1E293B' }}
                  >
                    <input type="checkbox" checked={t.val} onChange={(e) => t.set(e.target.checked)} />
                    <span className="text-slate-200 font-medium">{t.label}</span>
                  </label>
                ))}
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  onClick={() => handleSaveConfig(false)}
                  disabled={savingCfg}
                  className="px-4 py-2 rounded-xl font-bold text-xs"
                  style={{ backgroundColor: '#00E676', color: '#04120B' }}
                >
                  {savingCfg ? 'Saving...' : 'Save Paper Settings'}
                </button>
                {autoStatus?.kill_switch_active && (
                  <button
                    onClick={() => handleSaveConfig(true)}
                    className="px-4 py-2 rounded-xl font-bold text-xs border"
                    style={{ backgroundColor: 'rgba(255,59,92,0.15)', borderColor: '#FF3B5C', color: '#FF3B5C' }}
                  >
                    Reset Kill-Switch
                  </button>
                )}
              </div>
            </div>

            {/* Closed Trades Ledger */}
            <div className="p-4 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
              <h3 className="text-sm font-extrabold text-white mb-3">
                Closed Trade Ledger ({closedTrades.length})
              </h3>
              {closedTrades.length === 0 ? (
                <div className="text-xs text-slate-500">No closed paper trades yet.</div>
              ) : (
                <div className="space-y-2">
                  {closedTrades.map((c: any) => {
                    const net = Number(c.net_pnl_inr ?? 0);
                    const up = net >= 0;
                    return (
                      <div
                        key={c.position_id}
                        className="p-3 rounded-xl border flex items-center justify-between gap-2"
                        style={{ backgroundColor: '#0A0E17', borderColor: '#1B2436' }}
                      >
                        <div>
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-sm font-bold text-white">{c.symbol}</span>
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">
                              {c.horizon_label || c.trading_mode}
                            </span>
                            <span
                              className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                              style={{
                                backgroundColor: c.outcome === 'WIN' ? 'rgba(0,230,118,0.15)' : 'rgba(255,59,92,0.15)',
                                color: c.outcome === 'WIN' ? '#00E676' : '#FF3B5C',
                              }}
                            >
                              {c.outcome || (up ? 'WIN' : 'LOSS')}
                            </span>
                          </div>
                          <div className="text-[11px] text-slate-500 mt-0.5">
                            {c.exit_reason} • Entry ₹{fmtNum(c.entry_fill_price)} → Exit ₹{fmtNum(c.exit_fill_price)}
                          </div>
                        </div>
                        <div className="text-sm font-extrabold font-mono" style={{ color: up ? '#00E676' : '#FF3B5C' }}>
                          {up ? '+' : ''}
                          {fmtINR(net)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ================= SECTION 4: LIVE CHART & 5-LEVEL DEPTH ================= */}
        {section === 'chart' && (
          <div className="space-y-4">
            <div className="p-4 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
              <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <select
                    value={chartSymbol}
                    onChange={(e) => setChartSymbol(e.target.value)}
                    className="px-3 py-1.5 rounded-xl border text-sm font-bold text-white"
                    style={{ backgroundColor: '#0A0E17', borderColor: '#24324A' }}
                  >
                    {Array.from(new Set([chartSymbol, ...watchedSymbols, 'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'HAL'])).map(
                      (sym) => (
                        <option key={sym} value={sym}>
                          {sym}
                        </option>
                      ),
                    )}
                  </select>
                  {['5m', '15m', '1h', '1d'].map((tf) => (
                    <button
                      key={tf}
                      onClick={() => setChartTf(tf)}
                      className="px-2.5 py-1 rounded-lg text-xs font-bold border"
                      style={{
                        backgroundColor: chartTf === tf ? '#00E676' : '#151C2C',
                        color: chartTf === tf ? '#04120B' : '#CBD5E1',
                        borderColor: chartTf === tf ? '#00E676' : '#263449',
                      }}
                    >
                      {tf}
                    </button>
                  ))}
                </div>
                {chartLoading && <span className="text-xs text-slate-400">Loading live chart...</span>}
              </div>

              <TechnicalChartCanvas
                symbol={chartSymbol}
                timeframe={chartTf}
                candles={candles}
                indicators={indicators || undefined}
              />
            </div>

            {/* 5-Level Order Book Depth */}
            <div className="p-4 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-extrabold text-white">Angel One 5-Level Order Book Depth ({chartSymbol})</h3>
                <span className="text-xs font-bold font-mono" style={{ color: '#00D4FF' }}>
                  {depthData?.imbalance?.pressure || depthData?.pressure || 'LIVE SNAPQUOTE'}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <div className="flex justify-between text-[10px] font-bold text-slate-500 uppercase px-2">
                    <span>Bid Price</span>
                    <span>Qty</span>
                  </div>
                  {(depthData?.depth?.best_5_buy || depthData?.bids || []).slice(0, 5).map((b: any, i: number) => (
                    <div
                      key={i}
                      className="flex justify-between px-2.5 py-1.5 rounded-lg border text-xs font-mono"
                      style={{ backgroundColor: 'rgba(0,230,118,0.08)', borderColor: 'rgba(0,230,118,0.22)' }}
                    >
                      <span style={{ color: '#00E676' }} className="font-bold">
                        ₹{fmtNum(b.price)}
                      </span>
                      <span className="text-slate-200">{b.quantity ?? b.qty ?? 0}</span>
                    </div>
                  ))}
                </div>
                <div className="space-y-1.5">
                  <div className="flex justify-between text-[10px] font-bold text-slate-500 uppercase px-2">
                    <span>Ask Price</span>
                    <span>Qty</span>
                  </div>
                  {(depthData?.depth?.best_5_sell || depthData?.asks || []).slice(0, 5).map((a: any, i: number) => (
                    <div
                      key={i}
                      className="flex justify-between px-2.5 py-1.5 rounded-lg border text-xs font-mono"
                      style={{ backgroundColor: 'rgba(255,59,92,0.08)', borderColor: 'rgba(255,59,92,0.22)' }}
                    >
                      <span style={{ color: '#FF3B5C' }} className="font-bold">
                        ₹{fmtNum(a.price)}
                      </span>
                      <span className="text-slate-200">{a.quantity ?? a.qty ?? 0}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ================= SECTION 5: NEWS, AI & JOURNAL ================= */}
        {section === 'news' && (
          <div className="space-y-4">
            {/* Gemini AI Q&A */}
            <div className="p-4 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
              <h2 className="text-sm font-extrabold text-white">Gemini Market Intelligence Assistant</h2>
              <p className="text-xs text-slate-400 mt-0.5">
                Ask about any NSE stock (e.g. HAL, RELIANCE, TCS) — automatically pulls live candles, depth & news.
              </p>
              <div className="mt-3 flex gap-2">
                <input
                  value={aiQuestion}
                  onChange={(e) => setAiQuestion(e.target.value)}
                  placeholder="Ask e.g. Is HAL looking bullish for a swing?"
                  className="flex-1 px-3 py-2 rounded-xl border text-xs sm:text-sm text-white"
                  style={{ backgroundColor: '#0A0E17', borderColor: '#24324A' }}
                />
                <button
                  onClick={handleAskAi}
                  disabled={aiLoading}
                  className="px-4 py-2 rounded-xl font-bold text-xs shrink-0"
                  style={{ backgroundColor: '#00E676', color: '#04120B' }}
                >
                  {aiLoading ? 'Thinking...' : 'Ask AI'}
                </button>
              </div>
              {aiAnswer && (
                <div
                  className="mt-3 p-3 rounded-xl border text-xs text-slate-200 whitespace-pre-line leading-relaxed"
                  style={{ backgroundColor: '#0A0E17', borderColor: '#24324A' }}
                >
                  {aiAnswer.answer || aiAnswer.summary || 'No structured AI summary returned.'}
                </div>
              )}
            </div>

            {/* Morning Brief */}
            {morningBrief?.brief && (
              <div className="p-4 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
                <h3 className="text-sm font-extrabold text-white mb-2">Daily Morning Brief</h3>
                <div className="text-xs text-slate-300 whitespace-pre-line leading-relaxed">{morningBrief.brief}</div>
              </div>
            )}

            {/* Fresh Indian Market News */}
            <div className="p-4 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-extrabold text-white">Fresh Indian Market News & Sentiment Shield</h3>
                <span className="text-[10px] font-bold px-2 py-0.5 rounded" style={{ backgroundColor: 'rgba(0,230,118,0.14)', color: '#00E676' }}>
                  LIVE RSS
                </span>
              </div>
              <div className="space-y-2.5">
                {(newsData?.items || newsData?.articles || []).length === 0 ? (
                  <div className="text-xs text-slate-500">Loading fresh Indian market headlines...</div>
                ) : (
                  (newsData?.items || newsData?.articles || []).slice(0, 15).map((n: any, i: number) => {
                    const headline = n.headline || n.title || 'Market Update';
                    const srcName = n.source_name || n.source || 'Indian Market RSS';
                    const pub = n.published_at_ist || n.published_ist || n.published || '';
                    const href = n.source_url || n.link || n.url || '#';
                    const sent = String(n.sentiment || 'NEUTRAL').toUpperCase();
                    const sentColor =
                      sent === 'BULLISH' ? '#00E676' : sent === 'BEARISH' || sent === 'HIGH_RISK' ? '#FF3B5C' : '#94A3B8';
                    const sentBg =
                      sent === 'BULLISH'
                        ? 'rgba(0,230,118,0.14)'
                        : sent === 'BEARISH' || sent === 'HIGH_RISK'
                        ? 'rgba(255,59,92,0.14)'
                        : 'rgba(148,163,184,0.12)';
                    return (
                      <a
                        key={n.news_id || i}
                        href={href}
                        target="_blank"
                        rel="noreferrer"
                        className="block p-3 rounded-xl border hover:border-slate-500 transition"
                        style={{ backgroundColor: '#0A0E17', borderColor: '#1B2436' }}
                      >
                        <div className="text-xs sm:text-sm font-semibold text-white leading-snug">{headline}</div>
                        {n.shield_note && (
                          <div className="text-[11px] text-slate-400 mt-1">{n.shield_note}</div>
                        )}
                        <div className="flex items-center justify-between gap-2 mt-2 text-[11px] text-slate-400">
                          <span className="truncate">
                            {srcName}
                            {pub ? ` • ${String(pub).slice(0, 19).replace('T', ' ')}` : ''}
                          </span>
                          <span
                            className="shrink-0 text-[10px] font-bold px-2 py-0.5 rounded"
                            style={{ backgroundColor: sentBg, color: sentColor }}
                          >
                            {sent}
                          </span>
                        </div>
                      </a>
                    );
                  })
                )}
              </div>
            </div>

            {/* Quick Journal */}
            <div className="p-4 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
              <h3 className="text-sm font-extrabold text-white mb-2">Trade Journal ({journalList.length})</h3>
              <div className="flex gap-2">
                <input
                  value={journalNote}
                  onChange={(e) => setJournalNote(e.target.value)}
                  placeholder="Write a quick trade note..."
                  className="flex-1 px-3 py-2 rounded-xl border text-xs text-white"
                  style={{ backgroundColor: '#0A0E17', borderColor: '#24324A' }}
                />
                <button
                  onClick={async () => {
                    if (!journalNote.trim()) return;
                    await apiPost('/api/v1/journal', {
                      symbol: chartSymbol,
                      emotion_tag: 'DISCIPLINED',
                      notes: journalNote.trim(),
                    });
                    setJournalNote('');
                    const r = await apiGet<any>('/api/v1/journal');
                    setJournalList(r?.entries || []);
                    notify('Journal entry saved');
                  }}
                  className="px-4 py-2 rounded-xl font-bold text-xs"
                  style={{ backgroundColor: '#00D4FF', color: '#04120B' }}
                >
                  Save
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ================= SECTION 6: SYSTEM & APIS (OFF FRONT SCREEN) ================= */}
        {section === 'system' && (
          <div className="space-y-4">
            <div className="p-4 rounded-2xl border" style={{ backgroundColor: '#0F1420', borderColor: '#1E293B' }}>
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div>
                  <h2 className="text-base font-extrabold text-white">System & API Connectivity</h2>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Backend health lives cleanly here — your Trading Desk stays clutter-free unless an API disconnects.
                  </p>
                </div>
                <button
                  onClick={async () => {
                    const r = await apiPost<any>('/api/v1/notifications/telegram/test', { confirm_send: true }).catch(() => null);
                    notify(r?.delivered ? 'Test Telegram alert sent!' : 'Telegram test attempted');
                  }}
                  className="px-3 py-1.5 rounded-xl border text-xs font-bold"
                  style={{ backgroundColor: 'rgba(0,212,255,0.12)', borderColor: 'rgba(0,212,255,0.35)', color: '#00D4FF' }}
                >
                  Test Telegram Alert
                </button>
              </div>

              <div className="mt-4 space-y-2">
                {apiChecks.map((a) => (
                  <div
                    key={a.key}
                    className="p-3 rounded-xl border flex items-center justify-between gap-3"
                    style={{ backgroundColor: '#0A0E17', borderColor: '#1B2436' }}
                  >
                    <div>
                      <div className="text-xs sm:text-sm font-bold text-white">{a.name}</div>
                      <div className="text-[11px] text-slate-400 mt-0.5">{a.detail}</div>
                    </div>
                    <span
                      className="shrink-0 text-[11px] font-bold px-2.5 py-1 rounded-lg"
                      style={{
                        backgroundColor: a.ok ? 'rgba(0,230,118,0.14)' : 'rgba(255,59,92,0.14)',
                        color: a.ok ? '#00E676' : '#FF3B5C',
                      }}
                    >
                      {a.ok ? 'CONNECTED' : 'DOWN'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
};

export default App;