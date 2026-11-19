-- TRADEIQ Demo Seed Data: Clearly labelled SIMULATED / DEMO data only
insert into public.trading_strategies (code, name, version, description) values
  ('BREAKOUT_RETEST', 'Strategy A — Breakout + Retest', '1.0.0', 'Resistance breakout on closed candle with volume confirmation and retest hold.'),
  ('SUPPORT_BOUNCE', 'Strategy B — Support Bounce', '1.0.0', 'Support zone test with closed-candle rejection and momentum confirmation.'),
  ('TREND_FOLLOWING', 'Strategy C — Trend Following', '1.0.0', 'Aligned HH/HL trend structure with EMA 9/20/50 alignment and pullback trigger.'),
  ('BREAKDOWN_RETEST', 'Strategy D — Breakdown + Retest', '1.0.0', 'Support breakdown on closed candle with failed retest.')
on conflict (code) do nothing;

insert into public.market_instruments (exchange, segment, symbol, name, provider_token, tick_size, lot_size, sector, status, source) values
  ('NSE', 'CASH_EQUITY', 'RELIANCE', 'Reliance Industries Ltd', '2885', 0.0500, 1, 'Energy', 'ACTIVE', 'DEMO'),
  ('NSE', 'CASH_EQUITY', 'TCS', 'Tata Consultancy Services Ltd', '11536', 0.0500, 1, 'IT', 'ACTIVE', 'DEMO'),
  ('NSE', 'CASH_EQUITY', 'HDFCBANK', 'HDFC Bank Ltd', '1333', 0.0500, 1, 'Banking', 'ACTIVE', 'DEMO'),
  ('NSE', 'CASH_EQUITY', 'ICICIBANK', 'ICICI Bank Ltd', '4963', 0.0500, 1, 'Banking', 'ACTIVE', 'DEMO'),
  ('NSE', 'CASH_EQUITY', 'SBIN', 'State Bank of India', '3045', 0.0500, 1, 'Banking', 'ACTIVE', 'DEMO'),
  ('NSE', 'CASH_EQUITY', 'INFY', 'Infosys Ltd', '1594', 0.0500, 1, 'IT', 'ACTIVE', 'DEMO')
on conflict (exchange, symbol) do nothing;
