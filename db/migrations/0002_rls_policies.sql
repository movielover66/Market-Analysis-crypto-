-- TRADEIQ Migration 0002: Row Level Security (RLS) & Access Lockdown (Section 17 & 18)

-- Lock down private schema from anon and authenticated frontend roles
revoke all on schema private from public, anon, authenticated;
revoke all on all tables in schema private from public, anon, authenticated;

-- Enable RLS on all public tables
alter table public.market_instruments enable row level security;
alter table public.historical_prices enable row level security;
alter table public.market_snapshots enable row level security;
alter table public.technical_indicators enable row level security;
alter table public.market_news enable row level security;
alter table public.exchange_calendar enable row level security;
alter table public.trading_strategies enable row level security;
alter table public.strategy_parameters enable row level security;

alter table public.trade_plans enable row level security;
alter table public.trade_plan_events enable row level security;
alter table public.user_profiles enable row level security;
alter table public.user_settings enable row level security;
alter table public.watchlists enable row level security;
alter table public.watchlist_stocks enable row level security;
alter table public.price_alerts enable row level security;
alter table public.user_notifications enable row level security;
alter table public.paper_positions enable row level security;
alter table public.paper_orders enable row level security;
alter table public.trading_journal enable row level security;
alter table public.backtest_runs enable row level security;
alter table public.backtest_trades enable row level security;

-- Global Reference Tables: Authenticated users read-only (service_role writes)
create policy "auth_read_market_instruments" on public.market_instruments for select to authenticated using (true);
create policy "auth_read_historical_prices" on public.historical_prices for select to authenticated using (true);
create policy "auth_read_market_snapshots" on public.market_snapshots for select to authenticated using (true);
create policy "auth_read_technical_indicators" on public.technical_indicators for select to authenticated using (true);
create policy "auth_read_market_news" on public.market_news for select to authenticated using (true);
create policy "auth_read_exchange_calendar" on public.exchange_calendar for select to authenticated using (true);
create policy "auth_read_trading_strategies" on public.trading_strategies for select to authenticated using (true);
create policy "auth_read_strategy_parameters" on public.strategy_parameters for select to authenticated using (true);

-- User-Owned Tables: Strict auth.uid() = user_id isolation for SELECT, INSERT, UPDATE, DELETE
create policy "own_user_profiles" on public.user_profiles for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own_user_settings" on public.user_settings for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own_trade_plans" on public.trade_plans for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own_trade_plan_events" on public.trade_plan_events for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own_watchlists" on public.watchlists for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own_watchlist_stocks" on public.watchlist_stocks for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own_price_alerts" on public.price_alerts for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own_user_notifications" on public.user_notifications for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own_paper_positions" on public.paper_positions for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own_paper_orders" on public.paper_orders for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own_trading_journal" on public.trading_journal for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own_backtest_runs" on public.backtest_runs for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "own_backtest_trades" on public.backtest_trades for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
