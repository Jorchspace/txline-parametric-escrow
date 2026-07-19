"""
state_exporter.py — Atomic JSON state exporter for decoupled UI architecture.

Writes dashboard.json on every state change: goal detected, pool settlement,
or match status transition. The file is written atomically (write → temp →
rename) so external consumers never read a partial file.

Consumers: frontend dashboards, audit tools, Telegram bots, Grafana.
"""

from dataclasses import asdict, is_dataclass
from typing import Any, Optional
import json
import os
import tempfile
import time


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize(obj: Any) -> Any:
    """Recursively convert dataclasses/tuples to JSON-safe dicts."""
    if is_dataclass(obj):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _serialize(v) for k, v in obj.items()}
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------

class StateExporter:
    """
    Persiste el estado financiero completo a dashboard.json.

    Escritura atómica: escribe a un archivo temporal y luego hace rename.
    Esto garantiza que cualquier lector externo siempre vea un archivo completo.

    Uso:
        exporter = StateExporter("dashboard.json")
        exporter.export(engine, sol, match_state)
    """

    def __init__(self, path: str = "dashboard.json"):
        self.path = os.path.abspath(path)
        self.export_count = 0
        self.last_export_ts: float = 0.0

    def export(
        self,
        engine: Any,
        sol: Any,
        listener: Any = None,
        system_status: str = "UPCOMING",
    ) -> str:
        """
        Construye y escribe dashboard.json.

        Args:
            engine:    BettingEngine instance.
            sol:       SolanaMock instance.
            listener:  TxLineListener instance (for match state).
            system_status: "UPCOMING" | "LIVE" | "FINISHED".

        Returns:
            Absolute path of the written file.
        """
        self.export_count += 1
        self.last_export_ts = time.time()

        # ── Match state ───────────────────────────────────────
        match_stats = {
            "status": system_status,
            "home_team": "",
            "away_team": "",
            "goals_home": 0,
            "goals_away": 0,
            "minute": 0,
            "phase": "pre_match",
            "total_goals": 0,
        }
        if listener is not None:
            evt = listener.last_event
            if evt is not None:
                match_stats.update({
                    "status": evt.match_status,
                    "home_team": evt.home_team,
                    "away_team": evt.away_team,
                    "goals_home": evt.goals_home,
                    "goals_away": evt.goals_away,
                    "minute": evt.minute,
                    "phase": evt.phase,
                    "total_goals": evt.goals_home + evt.goals_away,
                })
            else:
                match_stats.update({
                    "home_team": getattr(listener, "home_team", ""),
                    "away_team": getattr(listener, "away_team", ""),
                })

        # ── M1 Pool ───────────────────────────────────────────
        m1_state = None
        if engine.m1_pool is not None:
            pool = engine.m1_pool
            m1_state = {
                "active": not pool.resolved,
                "match_id": pool.match_id,
                "ticket_price_usdc": 1.0,
                "total_tickets": pool.total_tickets,
                "total_pool": round(pool.total_pool, 2),
                "rollover_balance": round(pool.rollover_balance, 2),
                "total_at_stake": round(pool.total_at_stake, 2),
                "resolved": pool.resolved,
                "winning_goals": pool.winning_goals,
                "tickets": [
                    {
                        "id": t.ticket_id,
                        "player": t.player[:16],
                        "prediction": t.prediction,
                        "won": t.won,
                        "payout": round(t.payout_usdc, 2),
                    }
                    for t in pool.tickets.values()
                ],
                "sim_pda": sol.get_vault(pool.match_id, "M1").address if sol else "",
                "sim_vault": sol.get_vault(pool.match_id, "M1").address if sol else "",
                "sim_vault_balance": round(
                    sol.get_vault(pool.match_id, "M1").locked_usdc, 2
                ) if sol else 0.0,
            }

        # ── M2 Duels ──────────────────────────────────────────
        m2_duels = []
        for duel in engine.duels.values():
            duels_entry = {
                "duel_id": duel.duel_id,
                "match_id": duel.match_id,
                "resolved": duel.resolved,
                "creator": {
                    "player": duel.creator.player[:16] if duel.creator else "none",
                    "prediction": duel.creator.prediction if duel.creator else -1,
                    "stake": round(duel.creator.amount_usdc, 2) if duel.creator else 0,
                    "won": duel.creator.won if duel.creator else False,
                    "payout": round(duel.creator.payout_usdc, 2) if duel.creator else 0,
                },
                "opponents": [
                    {
                        "player": t.player[:16],
                        "prediction": t.prediction,
                        "stake": round(t.amount_usdc, 2),
                        "won": t.won,
                        "payout": round(t.payout_usdc, 2),
                    }
                    for t in duel.opponents.values()
                ],
                "opponent_pool": round(duel.opponent_pool, 2),
                "total_pool": round(duel.total_pool, 2),
                "actual_result": duel.actual_result,
                "sim_pda": sol.get_vault(duel.match_id or engine.match_id, "M2").address if sol else "",
                "sim_vault": sol.get_vault(duel.match_id or engine.match_id, "M2").address if sol else "",
            }
            m2_duels.append(duels_entry)

        # ── Platform Ledger ───────────────────────────────────
        house_address = "HouSe1111111111111111111111111111111111111"
        house_balance = 0.0

        if sol:
            for w in sol.wallets.values():
                if "house" in w.name.lower() or "platform" in w.name.lower():
                    house_address = w.address
                    house_balance = w.balance_usdc
                    break
            house_balance = max(house_balance, engine.house_balance)

        # Derive fee breakdown from ledger memos
        house_fees_m1 = sum(
            tx.amount_usdc for tx in (sol.ledger if sol else [])
            if "house_fee:M1" in tx.memo
        )
        house_fees_m2_all = sum(
            tx.amount_usdc for tx in (sol.ledger if sol else [])
            if "house_fee:M2" in tx.memo
        )

        platform_ledger = {
            "house_wallet": house_address,
            "total_balance_usdc": round(house_balance, 2),
            "fees_collected": {
                "m1_rollover_desert_50pct": round(house_fees_m1, 2),
                "m2_creator_win_10pct": round(house_fees_m2_all, 2),
                "m2_draw_protection_20pct": round(house_fees_m2_all, 2),
                "total": round(house_fees_m1 + house_fees_m2_all, 2),
            },
        }

        # ── Transaction History ───────────────────────────────
        tx_history = []
        for tx in (sol.ledger if sol else [])[-20:]:  # last 20 txs
            tx_history.append({
                "tx_hash": tx.tx_hash,
                "source": tx.source[:16],
                "destination": tx.destination[:16],
                "amount_usdc": round(tx.amount_usdc, 2),
                "memo": tx.memo,
                "slot": tx.slot,
                "confirmed": tx.confirmed,
            })

        # ── Wallet balances ───────────────────────────────────
        wallet_balances = {}
        if sol:
            for addr, w in sol.wallets.items():
                wallet_balances[w.name] = {
                    "address": addr[:16] + "…",
                    "balance": round(w.balance_usdc, 2),
                    "locked": round(w.locked_usdc, 2),
                    "available": round(w.available, 2),
                }

        # ── Assemble dashboard ────────────────────────────────
        dashboard = {
            "dashboard": "TxLINE Parametric Escrow",
            "version": "1.0.0",
            "export_count": self.export_count,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "system_status": system_status,
            "match_id": engine.match_id,
            "match_stats": match_stats,
            "pool_m1": m1_state,
            "versus_m2": {
                "active_duels": sum(1 for d in m2_duels if not d["resolved"]),
                "total_duels": len(m2_duels),
                "duels": m2_duels,
            },
            "platform_ledger": platform_ledger,
            "wallet_balances": wallet_balances,
            "transaction_history": tx_history,
            "solana_mock": {
                "slot": sol.slot if sol else 0,
                "total_transfers": len(sol.ledger) if sol else 0,
                "system_balance": round(
                    sum(w.balance_usdc for w in (sol.wallets.values() if sol else [])), 2
                ),
            },
        }

        # ── Atomic write ──────────────────────────────────────
        dashboard = _serialize(dashboard)  # type: ignore  (recursively clean)

        dirname = os.path.dirname(self.path) or "."
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=dirname,
            suffix=".json",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            json.dump(dashboard, tmp, indent=2, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name

        os.replace(tmp_path, self.path)  # atomic rename
        return os.path.abspath(self.path)
