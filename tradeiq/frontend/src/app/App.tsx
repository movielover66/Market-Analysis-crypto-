import React, { useEffect, useState } from 'react';
import { Layout } from '../components/Layout';
import { LoadingBanner, ErrorBanner, EmptyStateBanner } from '../components/StateViews';
import { TechnicalChartCanvas } from '../charts/TechnicalChartCanvas';
import { MANDATORY_PLAN_DISCLAIMER, SECTION_20_PAGES } from '../pages/PagesRegistry';
import { LoginScreen } from '../components/LoginScreen';
import {
  apiGet,
  apiPost,
  clearSession,
  Envelope,
  fetchDemoOverview,
  fetchHealth,
  getTokenExpiryMs,
  isUnauthorizedError,
  logout,
  onUnauthorized,
  restoreSession,
} from '../lib/api';

interface AuthenticatedAppProps {
  onLogout: () => void;
}

/** The existing TRADEIQ workspace. Only mounted while an owner session exists, so no private API is called before login. */
const AuthenticatedApp: React.FC<AuthenticatedAppProps> = ({ onLogout }) => {
  const [activeTab, setActiveTab] = useState<string>('dashboard');
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [overview, setOverview] = useState<Envelope<any> | null>(null);
  const [health, setHealth] = useState<any | null>(null);
  const [disclaimerAccepted, setDisclaimerAccepted] = useState<boolean>(true);
  const [actionError, setActionError] = useState<string | null>(null);

  const [scannerData, setScannerData] = useState<any | null>(null);
  const [plannerSymbol, setPlannerSymbol] = useState<string>('RELIANCE');
  const [plannerMode, setPlannerMode] = useState<string>('SWING');
  const [plannerStrategy, setPlannerStrategy] = useState<string>('BREAKOUT_RETEST');
  const [plannerData, setPlannerData] = useState<any | null>(null);
  const [chartCandles, setChartCandles] = useState<any[]>([]);
  const [indicatorData, setIndicatorData] = useState<any | null>(null);

  const [riskForm, setRiskForm] = useState({
    symbol: 'RELIANCE',
    direction: 'LONG',
    trading_mode: 'SWING',
    entry_price: 1285.5,
    stop_loss: 1265.0,
    target_price: 1340.0,
    capital_inr: 75000,
  });
  const [riskResult, setRiskResult] = useState<any | null>(null);

  const [paperSummary, setPaperSummary] = useState<any | null>(null);
  const [journalData, setJournalData] = useState<any | null>(null);
  const [analyticsData, setAnalyticsData] = useState<any | null>(null);
  const [backtestData, setBacktestData] = useState<any | null>(null);
  const [newsData, setNewsData] = useState<any | null>(null);
  const [assistantQuestion, setAssistantQuestion] = useState<string>('Explain the current RELIANCE setup and risk limits.');
  const [assistantReply, setAssistantReply] = useState<any | null>(null);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, o] = await Promise.all([fetchHealth(), fetchDemoOverview()]);
      setHealth(h);
      setOverview(o);
    } catch (err: any) {
      setError(err?.message || 'Failed to connect to TRADEIQ backend');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // Runs a private API call. 401s are handled globally (session cleared -> login screen), so they are not shown here.
  const guarded = async (fn: () => Promise<void>) => {
    setActionError(null);
    try {
      await fn();
    } catch (err: any) {
      if (isUnauthorizedError(err)) return;
      setActionError(err?.message || 'Request failed. Please try again.');
    }
  };

  const runScanner = (mode = 'SWING', strategy = 'BREAKOUT_RETEST') =>
    guarded(async () => {
      setScannerData(await apiGet(`/api/v1/scanner?trading_mode=${mode}&strategy_code=${strategy}`));
    });

  const runPlanner = () =>
    guarded(async () => {
      setPlannerData(
        await apiPost('/api/v1/planner/evaluate', {
          symbol: plannerSymbol,
          trading_mode: plannerMode,
          strategy_code: plannerStrategy,
          capital_inr: 75000,
        }),
      );
    });

  const loadChartAndIndicators = (sym = plannerSymbol) =>
    guarded(async () => {
      const [cRes, iRes] = await Promise.allSettled([
        apiGet<any>(`/api/v1/demo/candles/${encodeURIComponent(sym)}?timeframe=15m&limit=30`),
        apiGet<any>(`/api/v1/market/indicators/${encodeURIComponent(sym)}?timeframe=15m`),
      ]);
      for (const r of [cRes, iRes]) {
        if (r.status === 'rejected' && isUnauthorizedError(r.reason)) throw r.reason;
      }
      // Candles are optional for the chart (the endpoint may not exist on this backend); indicators errors are shown.
      if (cRes.status === 'fulfilled') setChartCandles(cRes.value?.data?.candles || []);
      if (iRes.status === 'fulfilled') setIndicatorData(iRes.value);
      else throw iRes.reason;
    });

  const runRiskCalc = () =>
    guarded(async () => {
      setRiskResult(await apiPost('/api/v1/risk/calculate', riskForm));
    });

  const loadPaperSummary = () =>
    guarded(async () => {
      setPaperSummary(await apiGet('/api/v1/paper/summary'));
    });

  const openSamplePaperTrade = () =>
    guarded(async () => {
      await apiPost('/api/v1/paper/positions', {
        symbol: 'RELIANCE',
        direction: 'LONG',
        trading_mode: 'SWING',
        strategy_code: 'BREAKOUT_RETEST',
        quantity: 5,
        entry_price: 1285.5,
        stop_loss: 1265.0,
        target_price: 1340.0,
      });
      setPaperSummary(await apiGet('/api/v1/paper/summary'));
    });

  const closePaperTrade = (positionId: string) =>
    guarded(async () => {
      await apiPost(`/api/v1/paper/positions/${encodeURIComponent(positionId)}/close`, {
        exit_price: 1335.0,
        exit_reason: 'TARGET_HIT',
      });
      setPaperSummary(await apiGet('/api/v1/paper/summary'));
    });

  const loadJournal = () =>
    guarded(async () => {
      setJournalData(await apiGet('/api/v1/journal'));
    });

  const loadAnalytics = () =>
    guarded(async () => {
      setAnalyticsData(await apiGet('/api/v1/analytics'));
    });

  const runBacktest = (symbol = 'RELIANCE', strategy = 'BREAKOUT_RETEST') =>
    guarded(async () => {
      setBacktestData(await apiPost('/api/v1/backtest/run', { symbol, timeframe: '1d', strategy_code: strategy }));
    });

  const loadNews = () =>
    guarded(async () => {
      setNewsData(await apiGet('/api/v1/news'));
    });

  const askAssistant = () =>
    guarded(async () => {
      setAssistantReply(
        await apiPost('/api/v1/assistant/query', {
          question: assistantQuestion,
          symbol: plannerSymbol,
          trading_mode: plannerMode,
        }),
      );
    });

  useEffect(() => {
    setActionError(null);
    if (activeTab === 'scanner' && !scannerData) runScanner();
    if ((activeTab === 'planner' || activeTab === 'stock-detail') && !plannerData) runPlanner();
    if (activeTab === 'chart' || activeTab === 'stock-detail') loadChartAndIndicators(plannerSymbol);
    if (activeTab === 'risk' && !riskResult) runRiskCalc();
    if (activeTab === 'paper') loadPaperSummary();
    if (activeTab === 'journal') loadJournal();
    if (activeTab === 'analytics') loadAnalytics();
    if (activeTab === 'backtest' && !backtestData) runBacktest();
    if (activeTab === 'news' && !newsData) loadNews();
  }, [activeTab]);

  const quotes = overview?.data?.quotes || [];

  return (
    <Layout
      activeTab={activeTab}
      onSelectTab={(t) => setActiveTab(t)}
      source={overview?.source || 'DEMO'}
      freshness={overview?.freshness || 'SIMULATED'}
      asOfUtc={overview?.as_of_utc || new Date().toISOString()}
      marketSession={overview?.market_session || 'CLOSED'}
      onLogout={onLogout}
    >
      {/* Mandatory Disclaimer Banner (Section 20 line 823) */}
      <div className="mb-4 px-3 py-2 rounded-lg bg-slate-900 border border-slate-800 text-[11px] text-slate-400 flex items-center justify-between">
        <span>{MANDATORY_PLAN_DISCLAIMER}</span>
        <span className="font-mono text-amber-300">17/17 Section 20 Pages Active</span>
      </div>

      {loading && <LoadingBanner message="Loading TRADEIQ NSE Cash Equity workspace..." />}
      {error && <ErrorBanner error={error} onRetry={loadData} />}
      {actionError && <div className="mb-4"><ErrorBanner error={actionError} /></div>}

      {/* 1. Owner Session Page (single-owner access; no signup) */}
      {!loading && !error && activeTab === 'auth' && (
        <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-4 max-w-lg">
          <h2 className="text-base font-bold text-slate-100">Owner Session (Single-Owner Access)</h2>
          <p className="text-xs text-slate-400">
            You are signed in as the owner. The session token is kept only for this browser tab and is cleared when you sign out,
            close the tab, or the session expires.
          </p>
          <label className="flex items-start gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={disclaimerAccepted}
              onChange={(e) => setDisclaimerAccepted(e.target.checked)}
              className="mt-0.5"
            />
            <span>I acknowledge that TRADEIQ is a personal analytical & educational paper-trading tool only, not investment advice.</span>
          </label>
          <button onClick={onLogout} className="touch-target px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs font-semibold text-slate-100">
            Sign out
          </button>
        </div>
      )}

      {/* 2. Dashboard Page */}
      {!loading && !error && activeTab === 'dashboard' && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="p-4 rounded-xl bg-slate-900 border border-slate-800">
              <div className="text-xs text-slate-400 uppercase">Paper Capital (INR)</div>
              <div className="text-2xl font-bold text-slate-100 mt-1">₹75,000.00</div>
              <div className="text-xs text-emerald-400 mt-1">Max Risk/Trade: 0.50% (₹375.00)</div>
            </div>
            <div className="p-4 rounded-xl bg-slate-900 border border-slate-800">
              <div className="text-xs text-slate-400 uppercase">Daily Loss Lock</div>
              <div className="text-2xl font-bold text-slate-100 mt-1">1.00% (₹750.00)</div>
              <div className="text-xs text-slate-400 mt-1">Max Open Positions: 2 | Exposure Cap: 25%</div>
            </div>
            <div className="p-4 rounded-xl bg-slate-900 border border-slate-800">
              <div className="text-xs text-slate-400 uppercase">Execution Mode</div>
              <div className="text-2xl font-bold text-amber-400 mt-1">PAPER ONLY</div>
              <div className="text-xs text-slate-400 mt-1">NSE Cash Equities (-EQ) | Min Net R:R 1:2.0</div>
            </div>
          </div>

          <div className="rounded-xl bg-slate-900 border border-slate-800 overflow-hidden">
            <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-200">NSE Cash Watchlist Snapshot (Demo Mode)</h2>
              <span className="text-xs text-amber-300 font-mono">PROVENANCE: {overview?.freshness}</span>
            </div>
            {quotes.length === 0 ? (
              <EmptyStateBanner title="No instruments in watchlist" subtitle="Add NSE -EQ symbols in settings." />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-slate-950/60 text-xs uppercase text-slate-400">
                    <tr>
                      <th className="px-4 py-3">Symbol</th>
                      <th className="px-4 py-3">Exchange</th>
                      <th className="px-4 py-3">LTP (₹)</th>
                      <th className="px-4 py-3">Volume</th>
                      <th className="px-4 py-3">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800">
                    {quotes.map((q: any) => (
                      <tr key={q.symbol} className="hover:bg-slate-800/40">
                        <td className="px-4 py-3 font-semibold text-slate-100">{q.symbol}</td>
                        <td className="px-4 py-3 text-xs text-slate-400">{q.exchange}</td>
                        <td className="px-4 py-3 font-mono text-slate-100">₹{q.ltp}</td>
                        <td className="px-4 py-3 font-mono text-slate-300">{q.volume?.toLocaleString('en-IN')}</td>
                        <td className="px-4 py-3 flex gap-2">
                          <button
                            onClick={() => { setPlannerSymbol(q.symbol); setActiveTab('stock-detail'); }}
                            className="px-2.5 py-1 rounded bg-sky-500/20 text-sky-300 text-xs"
                          >
                            Stock Detail
                          </button>
                          <button
                            onClick={() => { setPlannerSymbol(q.symbol); setActiveTab('planner'); }}
                            className="px-2.5 py-1 rounded bg-emerald-500/20 text-emerald-300 text-xs"
                          >
                            Trade Plan
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 3. Market Overview Page */}
      {!loading && !error && activeTab === 'market' && (
        <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
          <h2 className="text-base font-bold text-slate-100">Market Overview — NSE Breadth, Sectors & Volatility Context</h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs font-mono">
            <div className="p-3 rounded bg-slate-950 border border-slate-800">
              <div className="text-slate-400">NSE Cash Universe Breadth</div>
              <div className="text-emerald-400 mt-1">Advances: 4 | Declines: 2 (SIMULATED)</div>
            </div>
            <div className="p-3 rounded bg-slate-950 border border-slate-800">
              <div className="text-slate-400">Sector Participation</div>
              <div className="text-slate-200 mt-1">Energy, IT, Banking Active (-EQ only)</div>
            </div>
            <div className="p-3 rounded bg-slate-950 border border-slate-800">
              <div className="text-slate-400">Session Anchor</div>
              <div className="text-amber-300 mt-1">09:15–15:30 Asia/Kolkata (IST)</div>
            </div>
          </div>
        </div>
      )}

      {/* 4. Stock Scanner Page */}
      {!loading && !error && activeTab === 'scanner' && (
        <div className="space-y-4">
          <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-slate-100">NSE Multi-Timeframe Strategy Scanner</h2>
              <p className="text-xs text-slate-400">Deterministic Strategies A–D + MTF Alignment + Decimal Risk Gate</p>
            </div>
            <div className="flex gap-2">
              <button onClick={() => runScanner('INTRADAY', 'BREAKOUT_RETEST')} className="px-3 py-1.5 rounded bg-slate-800 text-xs text-slate-200">
                Scan Intraday
              </button>
              <button onClick={() => runScanner('SWING', 'TREND_FOLLOWING')} className="px-3 py-1.5 rounded bg-emerald-600 text-xs text-white">
                Scan Swing
              </button>
            </div>
          </div>
          {scannerData && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {scannerData.results.map((item: any) => (
                <div key={item.symbol} className="p-4 rounded-xl bg-slate-900 border border-slate-800 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-slate-100">{item.symbol} ({item.trading_mode})</span>
                    <span className="px-2 py-0.5 rounded text-xs font-mono bg-amber-500/20 text-amber-300">
                      {item.trade_plan.status}
                    </span>
                  </div>
                  <div className="text-xs text-slate-300 font-mono">
                    Strategy: {item.strategy_code} | Close: ₹{item.latest_close}
                  </div>
                  <div className="text-xs text-slate-300 bg-slate-950/60 p-2 rounded border border-slate-800">
                    {item.ai_explanation.analysis}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 5. Stock Detail Page & 6. Technical Chart Page */}
      {!loading && !error && (activeTab === 'stock-detail' || activeTab === 'chart') && (
        <div className="space-y-4">
          <TechnicalChartCanvas
            symbol={plannerSymbol}
            timeframe="15m"
            candles={chartCandles}
            indicators={indicatorData?.indicators}
          />
          {plannerData && (
            <pre className="p-3 rounded bg-slate-900 border border-slate-800 text-xs font-mono text-slate-300 overflow-x-auto">
              {JSON.stringify(plannerData.trade_plan, null, 2)}
            </pre>
          )}
        </div>
      )}

      {/* 7. AI Trade Planner Page */}
      {!loading && !error && activeTab === 'planner' && (
        <div className="space-y-4">
          <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 flex flex-wrap items-center gap-3">
            <input
              value={plannerSymbol}
              onChange={(e) => setPlannerSymbol(e.target.value.toUpperCase())}
              className="px-3 py-1.5 rounded bg-slate-950 border border-slate-700 text-sm text-slate-100"
            />
            <select
              value={plannerStrategy}
              onChange={(e) => setPlannerStrategy(e.target.value)}
              className="px-3 py-1.5 rounded bg-slate-950 border border-slate-700 text-sm text-slate-100"
            >
              <option value="BREAKOUT_RETEST">Strategy A — Breakout + Retest</option>
              <option value="SUPPORT_BOUNCE">Strategy B — Support Bounce</option>
              <option value="TREND_FOLLOWING">Strategy C — Trend Following</option>
              <option value="BREAKDOWN_RETEST">Strategy D — Breakdown + Retest</option>
            </select>
            <button onClick={runPlanner} className="px-4 py-1.5 rounded bg-emerald-600 text-xs font-semibold text-white">
              Evaluate Trade Plan
            </button>
          </div>
          {plannerData && (
            <pre className="p-4 rounded-xl bg-slate-900 border border-slate-800 text-xs font-mono text-slate-300 overflow-x-auto">
              {JSON.stringify(plannerData, null, 2)}
            </pre>
          )}
        </div>
      )}

      {/* 8. Risk Calculator Page */}
      {!loading && !error && activeTab === 'risk' && (
        <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
          <h2 className="text-base font-semibold text-slate-100">Deterministic Decimal Risk & NSE Cost Calculator</h2>
          <button onClick={runRiskCalc} className="px-4 py-1.5 rounded bg-emerald-600 text-xs font-semibold text-white">
            Calculate Position Size & Charges
          </button>
          {riskResult?.decision && (
            <pre className="p-3 rounded bg-slate-950 border border-slate-800 text-xs font-mono text-slate-300 overflow-x-auto">
              {JSON.stringify(riskResult.decision, null, 2)}
            </pre>
          )}
        </div>
      )}

      {/* 9. Watchlists & Alerts Page */}
      {!loading && !error && activeTab === 'watchlists' && (
        <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-3">
          <h2 className="text-base font-semibold text-slate-100">Watchlists & Closed-Candle Alerts</h2>
          <p className="text-xs text-slate-400">
            Alerts evaluate strictly on closed candles (`is_closed = true`) with deduplication cooldown.
          </p>
        </div>
      )}

      {/* 10. Paper Trading Page */}
      {!loading && !error && activeTab === 'paper' && (
        <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-slate-100">Paper Trading Simulator & Ledger (Zero Real Money)</h2>
            <button onClick={openSamplePaperTrade} className="px-3 py-1.5 rounded bg-emerald-600 text-xs font-semibold text-white">
              + Open Sample RELIANCE Paper Trade
            </button>
          </div>
          {paperSummary && (
            <div className="space-y-3">
              <pre className="p-3 rounded bg-slate-950 border border-slate-800 text-xs font-mono text-slate-300 overflow-x-auto">
                {JSON.stringify(paperSummary, null, 2)}
              </pre>
              {paperSummary.open_positions?.map((pos: any) => (
                <button
                  key={pos.position_id}
                  onClick={() => closePaperTrade(pos.position_id)}
                  className="px-3 py-1.5 rounded bg-amber-500/20 text-amber-300 text-xs font-mono"
                >
                  Close Position {pos.position_id} @ ₹1,335.00
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 11. Trading Journal Page */}
      {!loading && !error && activeTab === 'journal' && (
        <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
          <h2 className="text-base font-semibold text-slate-100">Trading Journal & Rule Adherence Log</h2>
          <pre className="p-3 rounded bg-slate-950 border border-slate-800 text-xs font-mono text-slate-300 overflow-x-auto">
            {JSON.stringify(journalData, null, 2)}
          </pre>
        </div>
      )}

      {/* 12. Backtesting Page */}
      {!loading && !error && activeTab === 'backtest' && (
        <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-slate-100">Bar-by-Bar Backtester (In-Sample / Out-of-Sample Split)</h2>
            <button onClick={() => runBacktest('RELIANCE', 'BREAKOUT_RETEST')} className="px-3 py-1.5 rounded bg-emerald-600 text-xs font-semibold text-white">
              Run Walk-Forward Backtest
            </button>
          </div>
          {backtestData?.backtest && (
            <pre className="p-3 rounded bg-slate-950 border border-slate-800 text-xs font-mono text-slate-300 overflow-x-auto">
              {JSON.stringify(backtestData.backtest, null, 2)}
            </pre>
          )}
        </div>
      )}

      {/* 13. News & Events Page */}
      {!loading && !error && activeTab === 'news' && (
        <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
          <h2 className="text-base font-semibold text-slate-100">NSE Corporate Filings & Market News</h2>
          <pre className="p-3 rounded bg-slate-950 border border-slate-800 text-xs font-mono text-slate-300 overflow-x-auto">
            {JSON.stringify(newsData, null, 2)}
          </pre>
        </div>
      )}

      {/* 14. AI Assistant Page */}
      {!loading && !error && activeTab === 'assistant' && (
        <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
          <h2 className="text-base font-semibold text-slate-100">Read-Only Grounded AI Assistant (`get_analysis`, `get_plan`, `calc_quantity`, `get_journal_stats`)</h2>
          <div className="flex gap-2">
            <input
              value={assistantQuestion}
              onChange={(e) => setAssistantQuestion(e.target.value)}
              className="flex-1 px-3 py-1.5 rounded bg-slate-950 border border-slate-700 text-xs text-slate-100"
            />
            <button onClick={askAssistant} className="px-4 py-1.5 rounded bg-emerald-600 text-xs font-semibold text-white">
              Ask Grounded Assistant
            </button>
          </div>
          {assistantReply && (
            <pre className="p-3 rounded bg-slate-950 border border-slate-800 text-xs font-mono text-slate-300 overflow-x-auto">
              {JSON.stringify(assistantReply, null, 2)}
            </pre>
          )}
        </div>
      )}

      {/* 15. Performance Analytics Page */}
      {!loading && !error && activeTab === 'analytics' && (
        <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-4">
          <h2 className="text-base font-semibold text-slate-100">Performance Analytics (Win Rate, Profit Factor, Expectancy, Drawdown)</h2>
          <pre className="p-3 rounded bg-slate-950 border border-slate-800 text-xs font-mono text-slate-300 overflow-x-auto">
            {JSON.stringify(analyticsData, null, 2)}
          </pre>
        </div>
      )}

      {/* 16. Settings Page */}
      {!loading && !error && activeTab === 'settings' && (
        <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-3">
          <h2 className="text-base font-semibold text-slate-100">Risk, Capital & Effective-Dated NSE Cost Settings</h2>
          <p className="text-xs text-slate-400">
            Default Paper Capital: ₹75,000.00 (Range ₹50,000–₹1,00,000) | Risk/Trade: 0.5% | Daily Lock: 1.0% | Max Open Positions: 2 | Max Exposure: 25% | Min Net R:R: 1:2.0
          </p>
        </div>
      )}

      {/* 17. Provider Settings Page */}
      {!loading && !error && activeTab === 'diagnostics' && (
        <div className="p-5 rounded-xl bg-slate-900 border border-slate-800 space-y-3">
          <h2 className="text-sm font-semibold text-slate-200">Provider Settings & Connection Diagnostics (Secrets Never Displayed)</h2>
          <pre className="text-xs font-mono text-slate-300 bg-slate-950 p-4 rounded-lg overflow-x-auto">
            {JSON.stringify({ health, provenance: { source: overview?.source, freshness: overview?.freshness }, registered_pages: SECTION_20_PAGES.length }, null, 2)}
          </pre>
        </div>
      )}
    </Layout>
  );
};

const SESSION_EXPIRED_NOTICE = 'Your session has expired. Please sign in again.';

/** App shell: shows the owner login until a valid session exists; any HTTP 401 returns here. */
export const App: React.FC = () => {
  const [authed, setAuthed] = useState<boolean>(() => restoreSession());
  const [notice, setNotice] = useState<string | null>(null);

  // Any private call answered with 401 clears the token (in api.ts) and lands here.
  useEffect(
    () =>
      onUnauthorized(() => {
        setAuthed((was) => {
          if (was) setNotice(SESSION_EXPIRED_NOTICE);
          return false;
        });
      }),
    [],
  );

  // Sign out proactively when the token's own expiry passes.
  useEffect(() => {
    if (!authed) return;
    const exp = getTokenExpiryMs();
    if (exp === null) return;
    const delay = Math.min(Math.max(0, exp - Date.now()), 2 ** 31 - 1);
    const timer = setTimeout(() => {
      clearSession();
      setNotice(SESSION_EXPIRED_NOTICE);
      setAuthed(false);
    }, delay);
    return () => clearTimeout(timer);
  }, [authed]);

  const handleLogout = () => {
    logout();
    setNotice(null);
    setAuthed(false);
  };

  if (!authed) {
    return (
      <LoginScreen
        notice={notice}
        onAuthenticated={() => {
          setNotice(null);
          setAuthed(true);
        }}
      />
    );
  }
  return <AuthenticatedApp onLogout={handleLogout} />;
};
