"""
User-scoped Repository enforcing Row-Level Security (`auth.uid() == user_id`) and Audit Logging.
Every query and mutation requires the authenticated `user_id` and is strictly isolated per user.
"""
import datetime
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..core.errors import TradeIQException


class UserRepository:
    def __init__(self) -> None:
        self._settings: Dict[str, Dict[str, Any]] = {}
        self._watchlists: Dict[str, Dict[str, Any]] = {}
        self._audit_logs: List[Dict[str, Any]] = []

    def _default_settings(self, user_id: str) -> Dict[str, Any]:
        return {
            "user_id": user_id,
            "capital_inr": Decimal("75000.00"),
            "risk_per_trade_pct": Decimal("0.50"),
            "daily_loss_limit_pct": Decimal("1.00"),
            "max_open_positions": 2,
            "max_single_position_exposure_pct": Decimal("25.00"),
            "minimum_net_rr": Decimal("2.00"),
            "slippage_bps": Decimal("10.00"),
            "theme": "dark",
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def get_settings(self, auth_user_id: str, target_user_id: Optional[str] = None) -> Dict[str, Any]:
        uid = target_user_id or auth_user_id
        if uid != auth_user_id:
            raise TradeIQException("FORBIDDEN", "RLS Violation: Cannot access another user's settings", status_code=403)
        if uid not in self._settings:
            self._settings[uid] = self._default_settings(uid)
        row = dict(self._settings[uid])
        return {
            **row,
            "capital_inr": str(row["capital_inr"]),
            "risk_per_trade_pct": str(row["risk_per_trade_pct"]),
            "daily_loss_limit_pct": str(row["daily_loss_limit_pct"]),
            "max_single_position_exposure_pct": str(row["max_single_position_exposure_pct"]),
            "minimum_net_rr": str(row["minimum_net_rr"]),
            "slippage_bps": str(row["slippage_bps"]),
        }

    def update_settings(self, auth_user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        cur = dict(self._settings.get(auth_user_id) or self._default_settings(auth_user_id))
        if "capital_inr" in updates and updates["capital_inr"] is not None:
            cap = Decimal(str(updates["capital_inr"]))
            if cap < Decimal("1000.00"):
                raise TradeIQException("VALIDATION_ERROR", "capital_inr must be at least ₹1,000.00", status_code=422)
            cur["capital_inr"] = cap.quantize(Decimal("0.01"))
        if "risk_per_trade_pct" in updates and updates["risk_per_trade_pct"] is not None:
            rp = Decimal(str(updates["risk_per_trade_pct"]))
            if rp <= Decimal("0") or rp > Decimal("5.00"):
                raise TradeIQException("VALIDATION_ERROR", "risk_per_trade_pct must be between 0.01 and 5.00", status_code=422)
            cur["risk_per_trade_pct"] = rp.quantize(Decimal("0.01"))
        if "daily_loss_limit_pct" in updates and updates["daily_loss_limit_pct"] is not None:
            dl = Decimal(str(updates["daily_loss_limit_pct"]))
            if dl <= Decimal("0") or dl > Decimal("10.00"):
                raise TradeIQException("VALIDATION_ERROR", "daily_loss_limit_pct must be between 0.01 and 10.00", status_code=422)
            cur["daily_loss_limit_pct"] = dl.quantize(Decimal("0.01"))
        if "max_open_positions" in updates and updates["max_open_positions"] is not None:
            mp = int(updates["max_open_positions"])
            if mp < 1 or mp > 10:
                raise TradeIQException("VALIDATION_ERROR", "max_open_positions must be between 1 and 10", status_code=422)
            cur["max_open_positions"] = mp
        if "max_single_position_exposure_pct" in updates and updates["max_single_position_exposure_pct"] is not None:
            ex = Decimal(str(updates["max_single_position_exposure_pct"]))
            if ex <= Decimal("0") or ex > Decimal("100.00"):
                raise TradeIQException("VALIDATION_ERROR", "max_single_position_exposure_pct must be 1..100", status_code=422)
            cur["max_single_position_exposure_pct"] = ex.quantize(Decimal("0.01"))
        if "minimum_net_rr" in updates and updates["minimum_net_rr"] is not None:
            rr = Decimal(str(updates["minimum_net_rr"]))
            if rr < Decimal("1.00"):
                raise TradeIQException("VALIDATION_ERROR", "minimum_net_rr must be at least 1.00", status_code=422)
            cur["minimum_net_rr"] = rr.quantize(Decimal("0.01"))
        if "theme" in updates and updates["theme"] in ("dark", "light"):
            cur["theme"] = updates["theme"]

        cur["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._settings[auth_user_id] = cur
        self._record_audit(auth_user_id, "UPDATE_USER_SETTINGS", "user_settings", auth_user_id, updates)
        return self.get_settings(auth_user_id)

    def create_watchlist(self, auth_user_id: str, name: str, symbols: List[str]) -> Dict[str, Any]:
        wid = str(uuid.uuid4())
        row = {
            "id": wid,
            "user_id": auth_user_id,
            "name": name.strip(),
            "symbols": [s.strip().upper() for s in symbols if s.strip()],
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self._watchlists[wid] = row
        self._record_audit(auth_user_id, "CREATE_WATCHLIST", "watchlists", wid, {"name": row["name"]})
        return dict(row)

    def list_watchlists(self, auth_user_id: str) -> List[Dict[str, Any]]:
        # RLS enforcement: only return rows where row["user_id"] == auth_user_id
        return [dict(w) for w in self._watchlists.values() if w["user_id"] == auth_user_id]

    def get_watchlist(self, auth_user_id: str, watchlist_id: str) -> Dict[str, Any]:
        w = self._watchlists.get(watchlist_id)
        if not w or w["user_id"] != auth_user_id:
            raise TradeIQException("NOT_FOUND", "Watchlist not found", status_code=404)
        return dict(w)

    def delete_watchlist(self, auth_user_id: str, watchlist_id: str) -> bool:
        w = self._watchlists.get(watchlist_id)
        if not w or w["user_id"] != auth_user_id:
            raise TradeIQException("NOT_FOUND", "Watchlist not found", status_code=404)
        del self._watchlists[watchlist_id]
        self._record_audit(auth_user_id, "DELETE_WATCHLIST", "watchlists", watchlist_id, {})
        return True

    def _record_audit(self, user_id: str, action: str, entity_type: str, entity_id: str, metadata: Dict[str, Any]) -> None:
        self._audit_logs.append({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "metadata_json": metadata,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })
