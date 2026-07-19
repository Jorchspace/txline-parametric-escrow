"""
main.py — Orquestador Central de TxLINE Parametric Escrow.

Modos:
  --mock (default)   Stream simulado con WorldCupSimulator.
  --live             Stream real de TxLINE SSE (requiere TXLINE_API_TOKEN).

Uso:
    python main.py                          # modo mock (Argentina vs Francia)
    python main.py --mock --home Brasil --away Alemania
    python main.py --live --network devnet
    python main.py --live --network mainnet --match-id TXL-WC-001
"""

import argparse
import logging
import os
import sys

from config import env
from stream_listener import TxLineListener
from betting_engine import BettingEngine
from state_exporter import StateExporter
from solana_mock import SolanaMock

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
logger = logging.getLogger("txline")


# ---------------------------------------------------------------------------
# Helpers de display
# ---------------------------------------------------------------------------

def print_header(title: str) -> None:
    print(f"\n{'━'*60}")
    print(f"  {title}")
    print(f"{'━'*60}")


def print_subtitle(text: str) -> None:
    print(f"\n  {text}")
    print(f"  {'─'*50}")


# ---------------------------------------------------------------------------
# Setup: wallets + motor + solana mock
# ---------------------------------------------------------------------------

def build_phase_1_and_2(sol: SolanaMock, engine: BettingEngine, rollover_in: float):
    """Fases 1 (Pool M1) y 2 (Versus M2) — apuestas antes del partido."""

    # ─── Pool M1 ──────────────────────────────────────────────
    print_header("🎫 FASE 1: POOL M1 — $1 USDC por ticket (total_goals exacto)")

    engine.open_m1_pool(rollover_in=rollover_in)
    print(f"   Rollover acumulado del partido anterior: ${rollover_in:.2f}\n")

    bets_m1 = [
        ("alice", 3), ("bob", 2), ("carol", 4), ("dave", 2), ("eve", 1),
        ("alice", 3), ("bob", 4), ("carol", 2), ("dave", 5), ("eve", 3),
    ]

    for player_name, pred in bets_m1:
        addr = [p.address for p in env.players if p.name == player_name][0]
        ticket = engine.buy_m1_ticket(addr, pred)
        sol.lock_funds(addr, engine.match_id, "M1", ticket.amount_usdc)

    m1 = engine.m1_pool
    print(f"\n   Total M1: {m1.total_tickets} tickets × $1 = ${m1.total_pool:.2f}")
    print(f"   Rollover: ${m1.rollover_balance:.2f}")
    print(f"   At stake: ${m1.total_at_stake:.2f}")

    # ─── Versus M2 ────────────────────────────────────────────
    print_header("⚔️  FASE 2: VERSUS M2 — Creador vs Oponentes")

    alice = [p for p in env.players if p.name == "alice"][0]
    bob   = [p for p in env.players if p.name == "bob"][0]
    carol = [p for p in env.players if p.name == "carol"][0]
    dave  = [p for p in env.players if p.name == "dave"][0]
    eve   = [p for p in env.players if p.name == "eve"][0]

    # Duelo 1: Bob (HOME) vs Alice + Carol
    d1 = engine.create_m2_duel(bob.address, prediction=1, amount_usdc=50.0)
    sol.lock_funds(bob.address, engine.match_id, "M2", 50.0)
    engine.join_m2_duel(d1.duel_id, alice.address, prediction=2, amount_usdc=30.0)
    sol.lock_funds(alice.address, engine.match_id, "M2", 30.0)
    engine.join_m2_duel(d1.duel_id, carol.address, prediction=0, amount_usdc=20.0)
    sol.lock_funds(carol.address, engine.match_id, "M2", 20.0)
    print(f"   Duelo {d1.duel_id}: Bob (HOME) vs Alice+Carol  |  Pool: ${d1.total_pool:.2f}")

    # Duelo 2: Dave (DRAW) vs Eve (HOME)
    d2 = engine.create_m2_duel(dave.address, prediction=0, amount_usdc=25.0)
    sol.lock_funds(dave.address, engine.match_id, "M2", 25.0)
    engine.join_m2_duel(d2.duel_id, eve.address, prediction=1, amount_usdc=25.0)
    sol.lock_funds(eve.address, engine.match_id, "M2", 25.0)
    print(f"   Duelo {d2.duel_id}: Dave (DRAW) vs Eve (HOME)  |  Pool: ${d2.total_pool:.2f}")


def resolve_and_print(engine: BettingEngine, sol: SolanaMock, event):
    """Fase 4: resolución financiera post-partido."""
    total_goals = event.goals_home + event.goals_away
    if event.goals_home > event.goals_away:
        winner = 1
    elif event.goals_away > event.goals_home:
        winner = 2
    else:
        winner = 0

    print_header("💰 FASE 4: RESOLUCIÓN FINANCIERA")
    print(f"   Total goles: {total_goals}  |  Ganador: {['draw','home','away'][winner]}")
    if event.merkle_proof:
        print(f"   Merkle proof: {event.merkle_proof}\n")

    report = engine.resolve_all(actual_goals=total_goals, actual_winner=winner)

    # ── Pool M1 ──
    print_subtitle("RESULTADOS POOL M1")
    m1 = report["m1"]
    if m1:
        print(f"   Tickets totales:     {m1['tickets']}")
        print(f"   Total pool:          ${m1['total_pool']:.2f}")
        print(f"   Rollover entrante:   ${m1['rollover_in']:.2f}")
        print(f"   At stake total:      ${m1['total_at_stake']:.2f}")
        print(f"   Ganadores:           {m1['winners_count']}")
        if m1["winners_count"] > 0:
            for p in m1["payouts"]:
                sol.payout(p["player"], engine.match_id, "M1", p["payout"])
                print(f"     🏆 {p['player'][:8]}  ←  ${p['payout']:.2f}")
        else:
            print(f"     😞 Sin ganadores — 50% casa / 50% rollover")
            sol.collect_house_fee(engine.match_id, "M1", m1["house_cut"])
            print(f"     🏛️  House: ${m1['house_cut']:.2f}  |  Rollover next: ${m1['new_rollover']:.2f}")

    # ── Versus M2 ──
    print_subtitle("RESULTADOS VERSUS M2")
    for dr in report["m2_duels"]:
        print(f"   Duelo {dr['duel_id']}:")
        print(f"     Creador predijo {['draw','home','away'][dr['creator_prediction']]}, real={['draw','home','away'][dr['actual']]}")
        print(f"     House cut: ${dr['house_cut']:.2f}")
        for p in dr["payouts"]:
            player = p["player"][:8]
            if "total" in p:
                sol.payout(p["player"], engine.match_id, "M2", p["total"])
                print(f"     🥇 {player}  ←  ${p['total']:.2f}  (depósito + ganancia)")
            elif "payout" in p:
                sol.payout(p["player"], engine.match_id, "M2", p["payout"])
                print(f"     🥈 {player}  ←  ${p['payout']:.2f}")
            elif "refund" in p:
                sol.payout(p["player"], engine.match_id, "M2", p["refund"])
                print(f"     🤝 {player}  ←  ${p['refund']:.2f}  (reembolso 80%)")
        if dr["house_cut"] > 0:
            sol.collect_house_fee(engine.match_id, "M2", dr["house_cut"])

    print_subtitle("HOUSE BALANCE")
    print(f"   Total recolectado:   ${report['total_house_cut']:.2f}")
    print(f"   Rollover próximo:    ${report['next_rollover']:.2f}")

    print_header("📊 BALANCES FINALES")
    sol.print_balances()

    print_header("✅ DEMO COMPLETADA")
    print(f"   Transacciones on-chain:  {len(sol.ledger)}")
    print(f"   Slot final:              {sol.slot}")
    print(f"   Integridad del sistema:  ${sol.summary()['total_balance']:.2f} USDC\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_engine(mode="mock", home="Argentina", away="Francia", speed=0.5,
               rollover=15.0, network="devnet", match_id=None, api_token=None,
               match_num=1):
    """Core engine execution — callable from CLI or loop."""

    match_tag = f"TXL-WC-{match_num:03d}"
    print_header(f"🎯 TxLINE PARAMETRIC ESCROW — Match #{match_num}")

    sol = SolanaMock()
    engine = BettingEngine(match_id=match_tag)
    exporter = StateExporter("dashboard.json")

    print_subtitle("BALANCES INICIALES")
    sol.print_balances()

    # Fases 1+2: Apuestas
    build_phase_1_and_2(sol, engine, rollover_in=rollover)

    # Export UPCOMING
    exporter.export(engine, sol, listener=None, system_status="UPCOMING")
    print(f"\n  📊 Estado exportado → {exporter.path}  (UPCOMING)\n")

    # Fase 3: Stream
    listener = TxLineListener()

    def on_goal(event):
        exporter.export(engine, sol, listener=listener, system_status="LIVE")

    def on_finish(event):
        resolve_and_print(engine, sol, event)
        path = exporter.export(engine, sol, listener=listener, system_status="FINISHED")
        print(f"  📊 Estado final exportado → {path}  (FINISHED)\n")

    listener.on_goal(on_goal)
    listener.on_finish(on_finish)

    if mode == "live":
        print_header("📡 FASE 3: STREAM TxLINE — Partido en Vivo (LIVE)")
        listener.listen_live(
            api_token=api_token,
            network=network,
            match_id=match_id,
        )
    else:
        print_header("📡 FASE 3: STREAM TxLINE — Partido en Vivo (MOCK)")
        listener.listen_mock(home=home, away=away, speed=speed)

    # Return next rollover for chaining
    if engine.m1_pool and engine.m1_pool.resolved:
        return engine.m1_pool.rollover_balance
    return 0.0


def main():
    parser = argparse.ArgumentParser(
        description="TxLINE Parametric Escrow — Motor de apuestas sobre SSE de TxLINE",
    )
    parser.add_argument("--mode", choices=["mock", "live"], default="mock")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--home", default="Argentina")
    parser.add_argument("--away", default="Francia")
    parser.add_argument("--speed", type=float, default=3.0)
    parser.add_argument("--network", choices=["devnet", "mainnet"], default="devnet")
    parser.add_argument("--match-id")
    parser.add_argument("--api-token")
    parser.add_argument("--rollover", type=float, default=15.0)
    parser.add_argument("--loop", action="store_true",
                        help="Continuous mode: replay matches forever (for 24/7 dashboard)")
    args = parser.parse_args()

    if args.live:
        args.mode = "live"

    loop_mode = args.loop
    rollover = args.rollover
    match_num = 1

    TEAMS = [
        ("Argentina", "Francia"),
        ("Brasil", "Alemania"),
        ("Inglaterra", "España"),
        ("Uruguay", "Portugal"),
        ("Colombia", "Países Bajos"),
        ("México", "Croacia"),
    ]

    while True:
        h, a = args.home, args.away
        if loop_mode and match_num > 1:
            h, a = TEAMS[(match_num - 1) % len(TEAMS)]

        rollover = run_engine(
            mode=args.mode, home=h, away=a, speed=args.speed,
            rollover=rollover, network=args.network,
            match_id=args.match_id, api_token=args.api_token,
            match_num=match_num,
        )

        if not loop_mode:
            break

        match_num += 1
        print(f"\n  ⏳  Next match in 10s...  (rollover: ${rollover:.2f})\n")
        import time as _time
        _time.sleep(10)


if __name__ == "__main__":
    main()
