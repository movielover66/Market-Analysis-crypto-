-- TRADEIQ Migration 0001: Complete Initial Schema (Section 17 of Blueprint)
-- Target: Supabase PostgreSQL 15+

create extension if not exists "pgcrypto";
create schema if not exists private;

-- ============================================================
-- 1. GLOBAL / REFERENCE MARKET TABLES
-- ============================================================
create table if not exists public.market_instruments (
    id uuid primary key default gen_random_uuid(),
    exchange text not null default 'NSE' check (exchange in ('NSE')),
    segment text not null default 'CASH_EQUITY' check (segment in ('CASH_EQUITY')),
    symbol text not null,
    name text not null,
    provider_token text not null,
    isin text,
    tick_size numeric(10, 4) not null default 0.0500 check (tick_size > 0),
    lot_size integer not null default 1 check (lot_size >= 1),
    sector text,
    status text not null default 'ACTIVE' check (status in ('ACTIVE', 'SUSPENDED', 'UNAVAILABLE')),
    source text not null default 'ANGEL_ONE',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (exchange, symbol)
);

create table if not exists public.historical_prices (
    instrument_id uuid not null references public.market_instruments(id) on delete cascade,
    timeframe text not null check (timeframe in ('5m', '15m', '30m', '1h', '4h', '1d')),
    ts timestamptz not null,
    open numeric(14, 4) not null check (open > 0),
    high numeric(14, 4) not null,
    low numeric(14, 4) not null check (low > 0),
    close numeric(14, 4) not null check (close > 0),
    volume bigint not null default 0 check (volume >= 0),
    source text not null,
    ingested_at timestamptz not null default now(),
    is_closed boolean not null default true,
    freshness text not null default 'HISTORICAL' check (freshness in ('LIVE', 'DELAYED', 'HISTORICAL', 'SIMULATED', 'UNAVAILABLE')),
    quality_status text not null default 'VALID' check (quality_status in ('VALID', 'QUARANTINED', 'PROVISIONAL')),
    adjustment_status text not null default 'UNADJUSTED' check (adjustment_status in ('UNADJUSTED', 'ADJUSTED')),
    primary key (instrument_id, timeframe, ts),
    constraint ohlc_high_check check (high >= greatest(open, close)),
    constraint ohlc_low_check check (low <= least(open, close))
);

create table if not exists public.market_snapshots (
    id uuid primary key default gen_random_uuid(),
    instrument_id uuid not null references public.market_instruments(id) on delete cascade,
    ltp numeric(14, 4) not null check (ltp > 0),
    prev_close numeric(14, 4) not null check (prev_close > 0),
    open numeric(14, 4) not null,
    high numeric(14, 4) not null,
    low numeric(14, 4) not null,
    volume bigint not null default 0 check (volume >= 0),
    source text not null,
    exchange_ts timestamptz not null,
    received_ts timestamptz not null default now(),
    freshness text not null check (freshness in ('LIVE', 'DELAYED', 'HISTORICAL', 'SIMULATED', 'STALE', 'UNAVAILABLE')),
    quality_status text not null default 'VALID' check (quality_status in ('VALID', 'QUARANTINED'))
);

create table if not exists public.technical_indicators (
    instrument_id uuid not null references public.market_instruments(id) on delete cascade,
    timeframe text not null,
    ts timestamptz not null,
    values_json jsonb not null default '{}'::jsonb,
    source text not null,
    computed_at timestamptz not null default now(),
    primary key (instrument_id, timeframe, ts)
);

create table if not exists public.market_news (
    id uuid primary key default gen_random_uuid(),
    related_symbol text,
    headline text not null,
    summary_interpretation text,
    source_name text not null,
    source_url text not null,
    published_at timestamptz not null,
    ingested_at timestamptz not null default now(),
    content_hash text not null unique
);
create index if not exists idx_market_news_symbol_pub on public.market_news (related_symbol, published_at desc);

create table if not exists public.exchange_calendar (
    calendar_date date primary key,
    exchange text not null default 'NSE',
    is_trading_day boolean not null default true,
    holiday_name text,
    session_open_ist time not null default '09:15:00',
    session_close_ist time not null default '15:30:00'
);

-- ============================================================
-- 2. STRATEGY & TRADE-PLAN TABLES
-- ============================================================
create table if not exists public.trading_strategies (
    id uuid primary key default gen_random_uuid(),
    code text not null unique check (code in ('BREAKOUT_RETEST', 'SUPPORT_BOUNCE', 'TREND_FOLLOWING', 'BREAKDOWN_RETEST')),
    name text not null,
    version text not null default '1.0.0',
    description text not null,
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists public.strategy_parameters (
    id uuid primary key default gen_random_uuid(),
    strategy_id uuid not null references public.trading_strategies(id) on delete cascade,
    version text not null,
    params_json jsonb not null,
    created_at timestamptz not null default now(),
    unique (strategy_id, version)
);

create table if not exists public.trade_plans (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    instrument_id uuid not null references public.market_instruments(id),
    strategy_code text not null,
    strategy_version text not null,
    trading_mode text not null check (trading_mode in ('INTRADAY', 'SWING')),
    direction text not null check (direction in ('LONG', 'SHORT')),
    status text not null check (status in ('CANDIDATE', 'WAITING', 'CONFIRMED', 'REJECTED', 'EXPIRED', 'INVALIDATED', 'EXECUTED_PAPER')),
    entry_price numeric(14, 4) not null,
    stop_loss numeric(14, 4) not null,
    target_price numeric(14, 4) not null,
    quantity integer not null default 0 check (quantity >= 0),
    capital_used_inr numeric(14, 2) not null,
    risk_amount_inr numeric(14, 2) not null,
    estimated_charges_inr numeric(14, 2) not null,
    net_rr numeric(8, 3) not null,
    freshness text not null,
    rejection_code text,
    rejection_reason text,
    evidence_packet_json jsonb not null default '{}'::jsonb,
    ai_explanation_json jsonb,
    expires_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists idx_trade_plans_user_status on public.trade_plans (user_id, status, created_at desc);

create table if not exists public.trade_plan_events (
    id uuid primary key default gen_random_uuid(),
    plan_id uuid not null references public.trade_plans(id) on delete cascade,
    user_id uuid not null,
    from_status text,
    to_status text not null,
    reason text not null,
    created_at timestamptz not null default now()
);

-- ============================================================
-- 3. USER TABLES
-- ============================================================
create table if not exists public.user_profiles (
    user_id uuid primary key,
    email text,
    display_name text,
    disclaimer_accepted_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.user_settings (
    user_id uuid primary key,
    capital_inr numeric(14, 2) not null default 75000.00 check (capital_inr >= 1000.00),
    risk_per_trade_pct numeric(5, 2) not null default 0.50 check (risk_per_trade_pct > 0 and risk_per_trade_pct <= 5.00),
    daily_loss_limit_pct numeric(5, 2) not null default 1.00 check (daily_loss_limit_pct > 0 and daily_loss_limit_pct <= 10.00),
    max_open_positions integer not null default 2 check (max_open_positions >= 1 and max_open_positions <= 10),
    max_single_position_exposure_pct numeric(5, 2) not null default 25.00 check (max_single_position_exposure_pct > 0 and max_single_position_exposure_pct <= 100.00),
    minimum_net_rr numeric(5, 2) not null default 2.00 check (minimum_net_rr >= 1.00),
    slippage_bps numeric(6, 2) not null default 10.00 check (slippage_bps >= 0),
    theme text not null default 'dark' check (theme in ('dark', 'light')),
    updated_at timestamptz not null default now()
);

create table if not exists public.watchlists (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    name text not null,
    created_at timestamptz not null default now()
);

create table if not exists public.watchlist_stocks (
    watchlist_id uuid not null references public.watchlists(id) on delete cascade,
    user_id uuid not null,
    instrument_id uuid not null references public.market_instruments(id) on delete cascade,
    added_at timestamptz not null default now(),
    primary key (watchlist_id, instrument_id)
);

create table if not exists public.price_alerts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    instrument_id uuid not null references public.market_instruments(id) on delete cascade,
    condition_type text not null check (condition_type in ('CROSSES_ABOVE', 'CROSSES_BELOW', 'SETUP_CONFIRMED', 'PLAN_INVALIDATED')),
    threshold_price numeric(14, 4),
    active boolean not null default true,
    triggered_at timestamptz,
    created_at timestamptz not null default now()
);
create index if not exists idx_price_alerts_user_active on public.price_alerts (user_id, active);

create table if not exists public.user_notifications (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    title text not null,
    body text not null,
    is_read boolean not null default false,
    created_at timestamptz not null default now()
);

create table if not exists public.paper_positions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    plan_id uuid references public.trade_plans(id),
    instrument_id uuid not null references public.market_instruments(id),
    symbol text not null,
    direction text not null check (direction in ('LONG', 'SHORT')),
    status text not null check (status in ('OPEN', 'CLOSED')),
    quantity integer not null check (quantity >= 1),
    entry_price numeric(14, 4) not null,
    stop_loss numeric(14, 4) not null,
    target_price numeric(14, 4) not null,
    exit_price numeric(14, 4),
    gross_pnl_inr numeric(14, 2) not null default 0.00,
    charges_inr numeric(14, 2) not null default 0.00,
    net_pnl_inr numeric(14, 2) not null default 0.00,
    opened_at timestamptz not null default now(),
    closed_at timestamptz
);
create index if not exists idx_paper_positions_user_status on public.paper_positions (user_id, status);

create table if not exists public.paper_orders (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    position_id uuid references public.paper_positions(id) on delete cascade,
    instrument_id uuid not null references public.market_instruments(id),
    side text not null check (side in ('BUY', 'SELL')),
    order_type text not null default 'PAPER_MARKET' check (order_type in ('PAPER_MARKET', 'PAPER_LIMIT', 'PAPER_SL', 'PAPER_TARGET')),
    quantity integer not null check (quantity >= 1),
    planned_price numeric(14, 4) not null,
    simulated_fill_price numeric(14, 4) not null,
    slippage_inr numeric(14, 2) not null default 0.00,
    charges_inr numeric(14, 2) not null default 0.00,
    status text not null default 'FILLED' check (status in ('FILLED', 'CANCELLED')),
    created_at timestamptz not null default now()
);

create table if not exists public.trading_journal (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    position_id uuid references public.paper_positions(id) on delete set null,
    plan_id uuid references public.trade_plans(id) on delete set null,
    symbol text not null,
    strategy_code text not null,
    strategy_version text not null default '1.0.0',
    trade_date date not null default current_date,
    setup_quality text not null check (setup_quality in ('A', 'B', 'C')),
    entry_reason text not null,
    exit_reason text,
    rule_adherence boolean not null default true,
    emotion_notes text,
    mistake_tags text[],
    lesson_learned text,
    screenshot_url text,
    gross_pnl_inr numeric(14, 2) not null default 0.00,
    net_pnl_inr numeric(14, 2) not null default 0.00,
    created_at timestamptz not null default now()
);
create index if not exists idx_trading_journal_user_date on public.trading_journal (user_id, trade_date desc);

-- ============================================================
-- 4. BACKTEST TABLES
-- ============================================================
create table if not exists public.backtest_runs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null,
    strategy_code text not null,
    strategy_version text not null,
    parameter_hash text not null,
    data_hash text not null,
    git_sha text not null,
    start_date date not null,
    end_date date not null,
    oos_split_date date,
    assumptions_json jsonb not null,
    metrics_in_sample_json jsonb not null,
    metrics_out_of_sample_json jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.backtest_trades (
    id uuid primary key default gen_random_uuid(),
    run_id uuid not null references public.backtest_runs(id) on delete cascade,
    user_id uuid not null,
    symbol text not null,
    direction text not null,
    signal_bar_ts timestamptz not null,
    entry_bar_ts timestamptz not null,
    exit_bar_ts timestamptz not null,
    entry_fill_price numeric(14, 4) not null,
    exit_fill_price numeric(14, 4) not null,
    quantity integer not null,
    gross_pnl_inr numeric(14, 2) not null,
    charges_inr numeric(14, 2) not null,
    net_pnl_inr numeric(14, 2) not null,
    exit_reason text not null,
    is_out_of_sample boolean not null default false
);

-- ============================================================
-- 5. PRIVATE BACKEND-ONLY SCHEMA
-- ============================================================
create table if not exists private.provider_credentials (
    user_id uuid not null,
    provider_name text not null check (provider_name in ('ANGEL_ONE')),
    encrypted_payload bytea not null,
    key_version integer not null default 1,
    token_expires_at timestamptz,
    status text not null default 'DISCONNECTED' check (status in ('CONNECTED', 'EXPIRED', 'RECONNECT_REQUIRED', 'DISCONNECTED')),
    updated_at timestamptz not null default now(),
    primary key (user_id, provider_name)
);

create table if not exists private.audit_log (
    id uuid primary key default gen_random_uuid(),
    user_id uuid,
    action text not null,
    entity_type text not null,
    entity_id text,
    metadata_json jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);
