"""
TRADEIQ Modular FastAPI Application Factory (Phases 1–6 + Dual-Mode Render Deployment).
"""
import datetime
import asyncio
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from .ai.llm_client import GeminiLLMClient
from .api.v1.router import api_v1_router
from .core.config import Settings, load_settings_from_env
from .core.auth import LoginThrottle
from .core.errors import register_exception_handlers
from .core.logging import configure_logging
from .core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from .domain.paper.simulator import PaperTradingSimulator
from .providers.angel_one_provider import AngelOneProvider
from .providers.demo_provider import DemoMarketDataProvider
from .domain.universe.nse_universe import NSE_LIQUID_CASH_UNIVERSE
from .jobs.auto_trader import AutoPaperTrader
from .jobs.cron_runner import ProtectedJobRunner, TelegramNotifier
from .repositories.supabase_bridge import SupabaseRestBridge
from .repositories.legacy_paper_trade_store import LegacyPaperTradeStore
from .repositories.user_repository import UserRepository
from .services.alerts_and_news import NewsAndAlertsService
from .services.instrument_service import InstrumentService

logger = configure_logging()
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_DIST = _PROJECT_ROOT / "frontend" / "dist"
_STATIC_INDEX = Path(__file__).resolve().parent / "static" / "index.html"


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    cfg = settings or load_settings_from_env()
    cfg.validate_config()

    app = FastAPI(
        title="TRADEIQ API",
        version="0.1.0",
        description="Deterministic AI-Assisted NSE Cash Equity Trading Intelligence Platform (Paper Trading Only)",
        # API schema/docs are not public by default (opt in with ENABLE_API_DOCS=true).
        docs_url="/docs" if cfg.enable_api_docs else None,
        redoc_url="/redoc" if cfg.enable_api_docs else None,
        openapi_url="/openapi.json" if cfg.enable_api_docs else None,
    )
    app.state.settings = cfg
    app.state.login_throttle = LoginThrottle()
    if not (cfg.local_auth_enabled or cfg.supabase_auth_enabled):
        logger.warning(
            "No owner authentication configured: every private API endpoint will answer 401. "
            "Set OWNER_PASSWORD_HASH + AUTH_JWT_SECRET (built-in login) and/or OWNER_USER_ID (Supabase)."
        )
    app.state.user_repo = UserRepository()
    legacy_supabase_key = cfg.supabase_service_role_key or cfg.supabase_anon_key
    app.state.supabase_bridge = SupabaseRestBridge(
        supabase_url=cfg.supabase_url,
        supabase_key=legacy_supabase_key,
    )
    app.state.legacy_paper_store = LegacyPaperTradeStore(cfg.supabase_url, legacy_supabase_key)
    app.state.instrument_service = InstrumentService(source_label="DEMO" if cfg.demo_mode else "ANGEL_ONE")
    app.state.market_provider = DemoMarketDataProvider(demo_mode_enabled=True) if cfg.demo_mode else AngelOneProvider(
        api_key=cfg.angel_api_key, client_code=cfg.angel_client_code,
        password=cfg.angel_password, totp_secret=cfg.angel_totp_secret,
    )
    app.state.provider_startup_error = None
    app.state.paper_simulator = PaperTradingSimulator()
    app.state.auto_trader = AutoPaperTrader()
    app.state.news_alerts_service = NewsAndAlertsService(
        finnhub_api_key=cfg.finnhub_api_key,
        marketaux_api_key=cfg.marketaux_api_key,
    )
    app.state.journal_entries = []
    app.state.job_runner = ProtectedJobRunner(expected_token=cfg.internal_job_token)
    app.state.telegram_notifier = TelegramNotifier(
        bot_token=cfg.telegram_bot_token,
        chat_id=cfg.telegram_chat_id,
        live_http=True,
    )
    app.state.llm_client = GeminiLLMClient(
        api_key=cfg.gemini_api_key,
        api_keys=cfg.gemini_api_keys,
        model_name=cfg.gemini_model,
    )

    @app.on_event("startup")
    async def connect_market_provider():
        # Restore the previous ApexQuant single-owner NSE paper ledger when its old table exists.
        if app.state.legacy_paper_store.is_configured():
            try:
                from decimal import Decimal
                from .domain.paper.simulator import PaperPosition
                rows = await asyncio.to_thread(app.state.legacy_paper_store.fetch_nse_positions)
                for row in rows:
                    try:
                        symbol = str(row.get("symbol", "")).upper()
                        buy = Decimal(str(row.get("buy_price_inr") or "0"))
                        budget = Decimal(str(row.get("budget_inr") or buy))
                        qty = max(1, int(budget / buy)) if buy > 0 else 1
                        status = "OPEN" if str(row.get("status", "")).upper() == "HOLDING" else "CLOSED"
                        created = datetime.datetime.fromtimestamp(int(row.get("created_at") or 0), tz=datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).isoformat()
                        exit_price = Decimal(str(row.get("exit_price_inr") or "0")) or None
                        raw_label = str(row.get("hold_label") or "SWING")
                        parts = [x.strip() for x in raw_label.split("|")]
                        t_mode = parts[0] if len(parts) >= 1 and parts[0] else "SWING"
                        eng_code = parts[1] if len(parts) >= 2 and parts[1] else ("ENGINE_1_ORB" if t_mode.upper() == "INTRADAY" else "ENGINE_2A_VCP")
                        direction = parts[2] if len(parts) >= 3 and parts[2] in ("LONG", "SHORT") else "LONG"
                        closed_ts = row.get("closed_at")
                        closed_ist = (
                            datetime.datetime.fromtimestamp(int(closed_ts), tz=datetime.timezone.utc)
                            .astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
                            .isoformat()
                            if closed_ts else None
                        )
                        gross_pnl = Decimal("0.00")
                        net_pnl = Decimal("0.00")
                        outcome = None
                        if status == "CLOSED" and exit_price and buy > 0:
                            gross_pnl = (
                                ((exit_price - buy) * Decimal(str(qty)))
                                if direction == "LONG"
                                else ((buy - exit_price) * Decimal(str(qty)))
                            ).quantize(Decimal("0.01"))
                            net_pnl = gross_pnl
                            outcome = "WIN" if net_pnl > 0 else ("LOSS" if net_pnl < 0 else "BREAKEVEN")
                        pos = PaperPosition(
                            position_id=str(row.get("id") or ""), user_id=cfg.owner_id, plan_id=None,
                            symbol=symbol, trading_mode=t_mode, direction=direction,
                            status=status, quantity=qty, planned_entry=buy, entry_fill_price=buy,
                            stop_loss=Decimal(str(row.get("stop_loss_inr") or "0")),
                            target_price=Decimal(str(row.get("target_inr") or "0")),
                            exit_fill_price=exit_price, exit_reason="SUPABASE_RESTORED" if status == "CLOSED" else None,
                            gross_pnl_inr=gross_pnl, net_pnl_inr=net_pnl,
                            opened_at_ist=created, closed_at_ist=closed_ist,
                            engine_code=eng_code, outcome=outcome,
                        )
                        if pos.position_id:
                            app.state.paper_simulator.positions[pos.position_id] = pos
                    except Exception:
                        continue
            except Exception as exc:
                app.state.provider_startup_error = f"Legacy Supabase restore failed: {type(exc).__name__}"
        if cfg.demo_mode:
            return
        provider = app.state.market_provider
        try:
            await asyncio.to_thread(provider.login)
            instruments = await asyncio.to_thread(provider.refresh_instruments, NSE_LIQUID_CASH_UNIVERSE)
            app.state.instrument_service.replace_with_provider_instruments(instruments)
            await asyncio.to_thread(provider.start_websocket, NSE_LIQUID_CASH_UNIVERSE)
        except Exception as exc:
            app.state.provider_startup_error = f"{type(exc).__name__}: {str(exc)[:180]}"

        # Start autonomous IST background scheduler in live non-test mode
        if cfg.app_env != "test":
            app.state._scheduler_task = asyncio.create_task(_ist_background_scheduler(app))

    @app.on_event("shutdown")
    async def disconnect_market_provider():
        task = getattr(app.state, "_scheduler_task", None)
        if task:
            task.cancel()
        provider = app.state.market_provider
        if isinstance(provider, AngelOneProvider):
            await asyncio.to_thread(provider.stop)

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.allowed_origins,
        allow_credentials=False,  # bearer-token auth only; no cookies are used or accepted
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Internal-Job-Token"],
    )

    register_exception_handlers(app)
    app.include_router(api_v1_router)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def serve_unified_dashboard():
        built_index = _FRONTEND_DIST / "index.html"
        if built_index.exists():
            return HTMLResponse(built_index.read_text(encoding="utf-8"))
        if _STATIC_INDEX.exists():
            return HTMLResponse(_STATIC_INDEX.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>TRADEIQ Backend Running</h1><p>Open /docs or /api/v1/health</p>")


    @app.websocket("/api/v1/ws/live")
    async def ws_live_ticker(websocket: WebSocket):
        """
        Owner-authenticated sub-second live market & P&L WebSocket stream (500ms push).
        Verifies owner JWT token via ?token=... before streaming; rejects invalid tokens with 4401.
        """
        from decimal import Decimal
        from .core.auth import verify_hs256_jwt
        token = (websocket.query_params.get("token") or "").strip()
        authed_user_id: Optional[str] = None
        if token:
            if cfg.local_auth_enabled:
                try:
                    claims = verify_hs256_jwt(token, cfg.auth_jwt_secret)
                    if claims.get("sub"):
                        authed_user_id = cfg.owner_id
                except Exception:
                    pass
            if not authed_user_id and cfg.supabase_auth_enabled:
                try:
                    claims = verify_hs256_jwt(token, cfg.supabase_jwt_secret)
                    if claims.get("sub") == cfg.owner_user_id:
                        authed_user_id = cfg.owner_id
                except Exception:
                    pass
        if not authed_user_id:
            await websocket.close(code=4401, reason="Unauthorized")
            return

        await websocket.accept()
        IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        provider = app.state.market_provider
        svc = app.state.instrument_service
        sim = app.state.paper_simulator
        at = app.state.auto_trader

        try:
            max_frames = int(websocket.query_params.get("max_frames") or "0")
            sent = 0
            while True:
                now_ist = datetime.datetime.now(IST)
                # Gather instant cached quotes (zero blocking REST calls in hot loop)
                insts, _ = select_top_active_stocks(svc, provider, 30)
                quotes_out = []
                ltp_map: dict = {}
                for inst in insts:
                    try:
                        q = provider.get_quote(inst.symbol)
                    except Exception:
                        continue
                    if not q or q.ltp <= 0:
                        continue
                    ltp = Decimal(str(q.ltp))
                    prev = Decimal(str(q.prev_close)) if q.prev_close > 0 else ltp
                    chg = (ltp - prev).quantize(Decimal("0.05"))
                    pct = ((chg / prev) * Decimal("100")).quantize(Decimal("0.01")) if prev > 0 else Decimal("0.00")
                    ltp_map[inst.symbol.upper()] = ltp
                    quotes_out.append({
                        "symbol": inst.symbol,
                        "sector": inst.sector,
                        "ltp": str(ltp),
                        "prev_close": str(prev),
                        "change_inr": str(chg),
                        "change_pct": str(pct),
                    })

                # Also include LTPs for any open paper positions not in top 12
                for p in sim.positions.values():
                    if p.user_id == authed_user_id and p.status == "OPEN" and p.symbol not in ltp_map:
                        q = provider.get_quote(p.symbol)
                        if q and q.ltp > 0:
                            ltp_map[p.symbol] = Decimal(str(q.ltp))

                # Instant indices from WebSocket cache (or demo in test mode)
                if cfg.demo_mode:
                    indices_out = provider.get_index_quotes() if hasattr(provider, "get_index_quotes") else []
                    freshness = "SIMULATED"
                else:
                    indices_out = []
                    for name, exch in (("NIFTY 50", "NSE"), ("BANKNIFTY", "NSE"), ("SENSEX", "BSE")):
                        q = provider.get_quote(name)
                        if q and q.ltp > 0:
                            prev = q.prev_close if q.prev_close > 0 else q.ltp
                            chg = (q.ltp - prev).quantize(Decimal("0.05"))
                            pct = ((chg / prev) * Decimal("100")).quantize(Decimal("0.02")) if prev > 0 else Decimal("0.00")
                            indices_out.append({"symbol": name, "exchange": exch, "ltp": str(q.ltp), "change_inr": str(chg), "change_pct": str(pct), "freshness": "LIVE"})
                    health = provider.status_summary() if hasattr(provider, "status_summary") else {}
                    freshness = "LIVE" if health.get("websocket_connected") else "UNAVAILABLE"

                ledger = sim.account_ledger(authed_user_id, at.config.starting_balance_inr, ltp_map)
                await websocket.send_json({
                    "type": "LIVE_TICK",
                    "ts_ist": now_ist.strftime("%H:%M:%S IST"),
                    "freshness": freshness,
                    "indices": indices_out,
                    "quotes": quotes_out,
                    "ledger": ledger,
                })
                sent += 1
                if max_frames > 0 and sent >= max_frames:
                    break
                await asyncio.sleep(0.5)
        except (WebSocketDisconnect, asyncio.CancelledError):
            return
        except Exception:
            try:
                await websocket.close()
            except Exception:
                pass

    @app.get("/health", tags=["health"])
    async def root_health():
        return {
            "status": "ok",
            "service": "tradeiq-backend",
            "version": "0.1.0",
            "demo_mode": cfg.demo_mode,
            "paper_trading_only": True,
            "ts_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    # Serve the Vite build and support React Router deep links on Render.
    if _FRONTEND_DIST.exists():
        assets_dir = _FRONTEND_DIST / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

        @app.get("/{requested_path:path}", include_in_schema=False)
        async def frontend_spa_fallback(requested_path: str):
            candidate = (_FRONTEND_DIST / requested_path).resolve()
            try:
                candidate.relative_to(_FRONTEND_DIST.resolve())
                if candidate.is_file():
                    return FileResponse(candidate)
            except ValueError:
                pass
            index_file = _FRONTEND_DIST / "index.html"
            return FileResponse(index_file) if index_file.exists() else HTMLResponse("TRADEIQ frontend build missing", status_code=404)

    return app



async def _ist_background_scheduler(app: FastAPI) -> None:
    """
    Autonomous IST Market-Hours Scheduler (Live Mode):
    - 09:00 IST: Sends Morning Brief (Indices + Top News + Gemini summary) to Telegram.
    - 09:30-11:30 IST (every 5 min): Runs 3-Engine Auto-Scan & opens qualifying paper trades.
    - 09:16-15:16 IST (every 60 sec): Monitors open paper positions on live LTP (SL/Target/Breakeven/Trail/15:15 Square-Off).
    - 15:30 IST: Sends Daily Accuracy & Net P&L Summary to Telegram.
    """
    from decimal import Decimal
    from .api.v1.endpoints.intelligence import _collect_live_ltps, _execute_three_engine_cycle

    IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    sent_morning_date: Optional[str] = None
    sent_eod_date: Optional[str] = None
    last_scan_minute: int = -1

    class _DummyReq:
        def __init__(self, app_ref: FastAPI) -> None:
            self.app = app_ref

    dummy_req = _DummyReq(app)
    await asyncio.sleep(10)  # Let startup & WebSocket settle

    while True:
        try:
            now_ist = datetime.datetime.now(IST)
            # Auto-refresh Angel One daily JWT + WebSocket on new trading day
            prov = getattr(app.state, "market_provider", None)
            if prov and hasattr(prov, "is_session_expired") and prov.is_session_expired() and hasattr(prov, "ensure_session"):
                await asyncio.to_thread(prov.ensure_session)
            # Mon=0 .. Fri=4
            if now_ist.weekday() < 5:
                today = now_ist.strftime("%Y-%m-%d")
                hhmm = now_ist.strftime("%H:%M")
                cfg = app.state.settings
                tg = app.state.telegram_notifier
                at = app.state.auto_trader
                sim = app.state.paper_simulator

                # 1. 09:00 IST Morning Brief -> Telegram
                if "09:00" <= hhmm <= "09:10" and sent_morning_date != today:
                    try:
                        await asyncio.to_thread(app.state.news_alerts_service.fetch_top_indian_market_news, None, True, app.state.llm_client, True)
                        indices = await asyncio.to_thread(app.state.market_provider.get_index_quotes) if hasattr(app.state.market_provider, "get_index_quotes") else []
                        idx_txt = " | ".join(f"{i['symbol']}: ₹{i.get('ltp') or 'N/A'} ({i.get('change_pct') or '0'}%)" for i in indices)
                        top_news = app.state.news_alerts_service.list_news()[:3]
                        news_txt = "\n".join(f"- [{n.sentiment}] {n.headline[:95]}" for n in top_news) or "- Live RSS synced."
                        msg = f"Good Morning! 09:00 AM IST Market Brief\n{idx_txt}\n\nTop Headlines:\n{news_txt}\n\nAuto-Trader: {'ACTIVE' if at.config.enabled else 'PAUSED'} (Balance ₹{at.config.starting_balance_inr}, Per-Trade ₹{at.config.per_trade_amount_inr})"
                        if tg and tg.enabled:
                            await asyncio.to_thread(tg.send_closed_candle_alert, "MORNING BRIEF", msg, now_ist.strftime("%Y-%m-%d %H:%M IST"))
                        sent_morning_date = today
                    except Exception as exc:
                        logger.warning(f"Morning brief scheduler error: {type(exc).__name__}: {exc}")

                # 2. 09:30 - 11:30 IST (every 5 mins): 3-Engine Auto-Scan & Entry
                if "09:30" <= hhmm <= "11:30" and at.config.enabled and (now_ist.minute % 5 == 0) and last_scan_minute != now_ist.minute:
                    try:
                        await asyncio.to_thread(_execute_three_engine_cycle, dummy_req, cfg.owner_id, 30, True)
                        last_scan_minute = now_ist.minute
                    except Exception as exc:
                        logger.warning(f"Auto-scan scheduler error: {type(exc).__name__}: {exc}")

                # 3. 09:16 - 15:17 IST (every 60s): Live Position Monitor (SL/Target/Breakeven/Trail/15:15 Square-Off)
                if "09:16" <= hhmm <= "15:17":
                    open_syms = [p.symbol for p in sim.positions.values() if p.user_id == cfg.owner_id and p.status == "OPEN"]
                    if open_syms:
                        try:
                            live_ltps = await asyncio.to_thread(_collect_live_ltps, dummy_req, open_syms)
                            await asyncio.to_thread(at.monitor_open_positions, cfg.owner_id, sim, live_ltps, now_ist, tg, getattr(app.state, "legacy_paper_store", None))
                        except Exception as exc:
                            logger.warning(f"Position monitor error: {type(exc).__name__}: {exc}")

                # 4. 15:30 IST Daily Summary -> Telegram
                if "15:30" <= hhmm <= "15:40" and sent_eod_date != today:
                    try:
                        open_syms = [p.symbol for p in sim.positions.values() if p.user_id == cfg.owner_id and p.status == "OPEN"]
                        live_ltps = await asyncio.to_thread(_collect_live_ltps, dummy_req, open_syms)
                        await asyncio.to_thread(at.build_and_send_daily_summary, cfg.owner_id, sim, live_ltps, tg, True, None)
                        sent_eod_date = today
                    except Exception as exc:
                        logger.warning(f"EOD summary scheduler error: {type(exc).__name__}: {exc}")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(f"Scheduler loop error: {type(exc).__name__}: {exc}")
        await asyncio.sleep(60)


app = create_app()
