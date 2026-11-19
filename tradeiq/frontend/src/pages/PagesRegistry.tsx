/**
 * Section 20 — Canonical Registry of all 17 Application Pages in TRADEIQ.
 */
export interface PageMetadata {
  id: string;
  title: string;
  section20Name: string;
  mobileNavGroup: 'home' | 'scanner' | 'planner' | 'paper' | 'more';
  description: string;
}

export const SECTION_20_PAGES: PageMetadata[] = [
  { id: 'auth', title: 'Owner Session', section20Name: 'Login / Signup', mobileNavGroup: 'more', description: 'Single-owner login, secure session, sign out, disclaimer acknowledgement' },
  { id: 'dashboard', title: 'Dashboard', section20Name: 'Dashboard', mobileNavGroup: 'home', description: 'Market state, index snapshots, breadth, sector overview, open plans, risk-lock status' },
  { id: 'market', title: 'Market Overview', section20Name: 'Market Overview', mobileNavGroup: 'more', description: 'Breadth, sectors, volatility context, volume participation' },
  { id: 'scanner', title: 'Stock Scanner', section20Name: 'Stock Scanner', mobileNavGroup: 'scanner', description: 'Filterable stock list, strategy category, freshness and liquidity warning' },
  { id: 'stock-detail', title: 'Stock Detail', section20Name: 'Stock Detail', mobileNavGroup: 'scanner', description: 'Price, indicators, zones, multi-timeframe state, rule checklist, news' },
  { id: 'chart', title: 'Technical Chart', section20Name: 'Technical Chart', mobileNavGroup: 'scanner', description: 'Candles, EMA/VWAP, RSI/MACD, volume, S/R zones, plan levels' },
  { id: 'planner', title: 'AI Trade Planner', section20Name: 'AI Trade Planner', mobileNavGroup: 'planner', description: 'Plan, status, expiry, evidence, risk decision, rejection reasons' },
  { id: 'risk', title: 'Risk Calculator', section20Name: 'Risk Calculator', mobileNavGroup: 'planner', description: 'Capital, stop, quantity, charges, exposure and daily lock' },
  { id: 'watchlists', title: 'Watchlists & Alerts', section20Name: 'Watchlists', mobileNavGroup: 'more', description: 'Multiple lists, price change, setup status, alerts' },
  { id: 'paper', title: 'Paper Trading', section20Name: 'Paper Trading', mobileNavGroup: 'paper', description: 'Virtual capital, open positions, orders, fills, P&L' },
  { id: 'journal', title: 'Trading Journal', section20Name: 'Trading Journal', mobileNavGroup: 'more', description: 'Entry/exit, screenshots, setup, emotions, mistakes, lessons' },
  { id: 'backtest', title: 'Backtesting', section20Name: 'Backtesting', mobileNavGroup: 'more', description: 'Strategy/version, universe, date range, metrics, equity, drawdown, OOS' },
  { id: 'news', title: 'News & Events', section20Name: 'News & Events', mobileNavGroup: 'more', description: 'Source-linked feed, timestamps, AI summary labelled interpretation' },
  { id: 'assistant', title: 'AI Assistant', section20Name: 'AI Assistant', mobileNavGroup: 'more', description: 'Grounded explanations and read-only queries' },
  { id: 'analytics', title: 'Performance Analytics', section20Name: 'Performance Analytics', mobileNavGroup: 'more', description: 'Win rate, expectancy, R, drawdown, costs, strategy/month views' },
  { id: 'settings', title: 'Settings', section20Name: 'Settings', mobileNavGroup: 'more', description: 'Capital, risk limits, cost/slippage assumptions, theme' },
  { id: 'diagnostics', title: 'Provider Settings', section20Name: 'Provider Settings', mobileNavGroup: 'more', description: 'Connect/disconnect, token status, freshness diagnostics; never display secrets' },
];

export const MANDATORY_PLAN_DISCLAIMER =
  'Analytical & educational paper-trading tool only. Not investment advice, a guarantee of results, or a substitute for independent judgement.';
