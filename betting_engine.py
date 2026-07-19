"""
betting_engine.py — Motor financiero de apuestas paramétricas TxLINE.

Modelos implementados:

  Pool M1 — Bote de $1 USDC c/u
  ─────────────────────────────────────────────────────────
  - Cada ticket cuesta exactamente $1 USDC.
  - La apuesta es al total_goals exacto del partido.
  - Al resolverse (FINISHED), el engine busca coincidencias exactas.
  - Con ganadores:  dividen (total_tickets + rollover_balance) completo.
  - Sin ganadores:   platform_wallet += total_pool * 0.50
                     rollover_balance   = total_pool * 0.50  (próximo partido)

  Versus M2 — 1 vs N (Creador vs Oponentes)
  ─────────────────────────────────────────────────────────
  - Creador elige home/away/draw. Oponentes apuestan en contra.
  - Resultado DRAW (empate real):
      • Cada jugador recibe amount * 0.80 (reembolso 80%)
      • platform_wallet recibe el 20% restante
  - Creador GANA:
      • Creador recibe su depósito + 90% del pool de oponentes
      • platform_wallet recibe 10% del pool de oponentes
  - Creador PIERDE:
      • Pool de oponentes se divide proporcionalmente entre ellos
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import uuid
import logging

from config import (
    M1_TICKET_PRICE, M1_ROLLOVER_PCT, M1_HOUSE_CUT_PCT,
    M2_DRAW_REFUND_PCT, M2_DRAW_HOUSE_PCT,
    M2_CREATOR_WIN_PCT, M2_HOUSE_CUT_PCT,
    env,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ticket
# ---------------------------------------------------------------------------

@dataclass
class Ticket:
    player: str           # address base58
    match_id: str
    prediction: int        # total_goals apostado (M1)  o  1=home 2=away 0=draw (M2)
    amount_usdc: float
    model: str             # "M1" | "M2"
    ticket_id: str = field(default="")
    settled: bool = False
    payout_usdc: float = 0.0
    won: bool = False

    def __post_init__(self):
        if not self.ticket_id:
            self.ticket_id = str(uuid.uuid4())[:10]


# ---------------------------------------------------------------------------
# M1 — Pool de $1 USDC con Rollover 50%
# ---------------------------------------------------------------------------

@dataclass
class PoolM1:
    """
    Pool paramétrico M1.

    Reglas:
      - Tickets a $1 USDC fijo.
      - Apuesta: total_goals exacto (ej. prediction=3 → "3 goles totales").
      - Resolución:
          • Ganadores → dividen (total_pool + rollover) entre ellos.
          • Sin ganadores → 50% casa, 50% rollover.
    """

    match_id: str
    tickets: Dict[str, Ticket] = field(default_factory=dict)
    rollover_balance: float = 0.0
    resolved: bool = False
    winning_goals: Optional[int] = None

    # ── propiedades ──

    @property
    def total_tickets(self) -> int:
        return len(self.tickets)

    @property
    def total_pool(self) -> float:
        """Total apostado ($1 por ticket)."""
        return self.total_tickets * M1_TICKET_PRICE

    @property
    def total_at_stake(self) -> float:
        """Pool + rollover acumulado."""
        return self.total_pool + self.rollover_balance

    # ── operaciones ──

    def buy_ticket(self, player: str, prediction: int) -> Ticket:
        """
        Compra un ticket M1 por $1 USDC.

        Args:
            player: address base58 del jugador.
            prediction: total_goals exacto (0-9).
        """
        if self.resolved:
            raise ValueError("Pool M1 cerrado — partido finalizado.")
        ticket = Ticket(
            player=player,
            match_id=self.match_id,
            prediction=prediction,
            amount_usdc=M1_TICKET_PRICE,
            model="M1",
        )
        self.tickets[ticket.ticket_id] = ticket
        logger.info("🎫 M1  %s  apuesta %d goles  →  %s",
                     player[:8], prediction, ticket.ticket_id)
        return ticket

    def resolve(self, actual_goals: int) -> dict:
        """
        Resuelve el pool M1 contra el total_goals real.

        Returns:
            dict con winners, payouts, house_cut, rollover.
        """
        self.resolved = True
        self.winning_goals = actual_goals

        winners = [
            t for t in self.tickets.values()
            if t.prediction == actual_goals
        ]

        result = {
            "model": "M1",
            "total_pool": self.total_pool,
            "rollover_in": self.rollover_balance,
            "total_at_stake": self.total_at_stake,
            "tickets": self.total_tickets,
            "winners_count": len(winners),
            "house_cut": 0.0,
            "new_rollover": 0.0,
            "payouts": [],
        }

        if winners:
            # Dividen todo (pool + rollover) proporcionalmente ($1 = misma parte)
            payout_each = self.total_at_stake / len(winners)
            for w in winners:
                w.payout_usdc = payout_each
                w.settled = True
                w.won = True
                result["payouts"].append({
                    "player": w.player,
                    "ticket": w.ticket_id,
                    "payout": round(payout_each, 2),
                })
                logger.info("🏆 M1 WINNER  %s  gana $%.2f  [predijo %d goles]",
                            w.player[:8], payout_each, w.prediction)
            self.rollover_balance = 0.0
            result["new_rollover"] = 0.0
        else:
            # Sin ganadores → 50% casa, 50% rollover
            house = self.total_pool * M1_HOUSE_CUT_PCT
            roll = self.total_pool * M1_ROLLOVER_PCT
            self.rollover_balance = roll
            result["house_cut"] = round(house, 2)
            result["new_rollover"] = round(roll, 2)
            logger.info("😞 M1 SIN GANADORES  |  house=$%.2f  rollover=$%.2f",
                        house, roll)

        # Marcar perdedores
        for t in self.tickets.values():
            if not t.settled:
                t.settled = True
                t.won = False

        return result


# ---------------------------------------------------------------------------
# M2 — Versus 1 vs N
# ---------------------------------------------------------------------------

@dataclass
class DuelM2:
    """
    Duelo 1 vs N.

    Reglas:
      - Creador elige: 1 (home) | 2 (away) | 0 (draw).
      - Draw real → 80% reembolso a cada uno, 20% a la casa.
      - Creador gana → depósito + 90% pool oponentes, 10% casa.
      - Creador pierde → pool de oponentes dividido entre ellos.
    """

    duel_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    match_id: str = ""
    creator: Optional[Ticket] = None
    opponents: Dict[str, Ticket] = field(default_factory=dict)
    resolved: bool = False
    actual_result: Optional[int] = None   # 1=home 2=away 0=draw

    # ── propiedades ──

    @property
    def opponent_pool(self) -> float:
        return sum(t.amount_usdc for t in self.opponents.values())

    @property
    def total_pool(self) -> float:
        c = self.creator.amount_usdc if self.creator else 0.0
        return c + self.opponent_pool

    # ── operaciones ──

    def create(self, player: str, prediction: int,
               amount_usdc: float, match_id: str) -> Ticket:
        """Crea el duelo. prediction: 1=home, 2=away, 0=draw."""
        if self.creator is not None:
            raise ValueError("Duelo ya creado.")
        ticket = Ticket(
            player=player, match_id=match_id,
            prediction=prediction, amount_usdc=amount_usdc, model="M2",
        )
        self.creator = ticket
        self.match_id = match_id
        logger.info("⚔️  M2 DUELO CREADO  %s apuesta %s  $%.2f USDC  →  %s",
                     player[:8], ["draw", "home", "away"][prediction],
                     amount_usdc, self.duel_id)
        return ticket

    def join(self, player: str, prediction: int, amount_usdc: float) -> Ticket:
        """
        Un oponente se une al duelo. Debe apostar al resultado OPUESTO.
        Si el creador eligió home (1), oponentes apuestan a away (2) o draw (0).
        """
        ticket = Ticket(
            player=player, match_id=self.match_id,
            prediction=prediction, amount_usdc=amount_usdc, model="M2",
        )
        self.opponents[ticket.ticket_id] = ticket
        logger.info("👊 M2 JOIN  %s apuesta $%.2f contra %s  →  %s",
                     player[:8], amount_usdc, self.creator.player[:8], ticket.ticket_id)
        return ticket

    def resolve(self, actual: int) -> dict:
        """
        Resuelve el duelo.

        Args:
            actual: 1=home gana, 2=away gana, 0=draw.

        Returns:
            dict con resultado financiero.
        """
        self.resolved = True
        self.actual_result = actual
        result = {
            "model": "M2",
            "duel_id": self.duel_id,
            "creator": self.creator.player if self.creator else "none",
            "creator_prediction": self.creator.prediction if self.creator else -1,
            "actual": actual,
            "total_pool": self.total_pool,
            "house_cut": 0.0,
            "payouts": [],
        }

        creator_won = self.creator and self.creator.prediction == actual

        if actual == 0:
            # ── DRAW real (empate) → 80% reembolso, 20% casa ──
            self._resolve_draw(result)
        elif creator_won:
            # ── Creador GANA → depósito + 90% pool oponentes, 10% casa ──
            self._resolve_creator_wins(result)
        else:
            # ── Creador PIERDE → oponentes dividen el pool ──
            self._resolve_creator_loses(result)

        return result

    def _resolve_draw(self, result: dict) -> None:
        """Draw real: 80% reembolso a cada uno, 20% casa."""
        for ticket in [self.creator] + list(self.opponents.values()):
            if ticket is None:
                continue
            refund = round(ticket.amount_usdc * M2_DRAW_REFUND_PCT, 2)
            house  = round(ticket.amount_usdc * M2_DRAW_HOUSE_PCT, 2)
            ticket.payout_usdc = refund
            ticket.settled = True
            ticket.won = False
            result["payouts"].append({
                "player": ticket.player,
                "refund": refund,
                "house_kept": house,
            })
            result["house_cut"] += house
            logger.info("🤝 M2 DRAW  %s reembolso $%.2f (80%%)  house=$%.2f",
                        ticket.player[:8], refund, house)

    def _resolve_creator_wins(self, result: dict) -> None:
        """Creador gana: depósito + 90% pool oponentes, 10% casa."""
        opp_pool = self.opponent_pool
        creator_share = round(opp_pool * M2_CREATOR_WIN_PCT, 2)
        house_share   = round(opp_pool * M2_HOUSE_CUT_PCT, 2)

        self.creator.payout_usdc = self.creator.amount_usdc + creator_share
        self.creator.settled = True
        self.creator.won = True
        result["payouts"].append({
            "player": self.creator.player,
            "deposit_returned": self.creator.amount_usdc,
            "winnings": creator_share,
            "total": self.creator.payout_usdc,
        })
        result["house_cut"] = house_share
        logger.info("🥇 M2 CREATOR WINS  %s gana $%.2f (depósito + 90%% pool)  house=$%.2f",
                     self.creator.player[:8], self.creator.payout_usdc, house_share)

        for t in self.opponents.values():
            t.settled = True
            t.won = False

    def _resolve_creator_loses(self, result: dict) -> None:
        """Creador pierde → pool se divide entre oponentes."""
        self.creator.settled = True
        self.creator.won = False

        if not self.opponents:
            result["payouts"].append({
                "player": self.creator.player,
                "refund": self.creator.amount_usdc,
                "note": "sin oponentes, reembolso total",
            })
            self.creator.payout_usdc = self.creator.amount_usdc
            return

        for t in self.opponents.values():
            proportion = t.amount_usdc / self.opponent_pool
            payout = round(self.total_pool * proportion, 2)
            t.payout_usdc = payout
            t.settled = True
            t.won = True
            result["payouts"].append({
                "player": t.player,
                "proportion": round(proportion, 4),
                "payout": payout,
            })
            logger.info("🥈 M2 OPPONENT WINS  %s gana $%.2f (%.0f%% del pool)",
                        t.player[:8], payout, proportion * 100)


# ---------------------------------------------------------------------------
# Motor central
# ---------------------------------------------------------------------------

class BettingEngine:
    """
    Orquestador de pools M1 y duelos M2 para un partido.

    Uso:
        engine = BettingEngine("TXL-WC-001")
        engine.open_m1_pool(rollover_in=15.0)
        engine.buy_m1_ticket("alice", prediction=3)
        engine.create_m2_duel("bob", prediction=1, amount=50)
        engine.join_m2_duel(duel_id, "carol", prediction=2, amount=30)
        report = engine.resolve_all(actual_goals=4, actual_winner=1)
    """

    def __init__(self, match_id: str):
        self.match_id = match_id
        self.m1_pool: Optional[PoolM1] = None
        self.duels: Dict[str, DuelM2] = {}
        self.house_balance: float = 0.0
        self._resolved = False

    # ── M1 ───────────────────────────────────────────────────────

    def open_m1_pool(self, rollover_in: float = 0.0) -> PoolM1:
        if self.m1_pool is not None:
            return self.m1_pool
        self.m1_pool = PoolM1(match_id=self.match_id)
        self.m1_pool.rollover_balance = rollover_in
        logger.info("🏟️  M1 POOL ABIERTO  rollover=$%.2f", rollover_in)
        return self.m1_pool

    def buy_m1_ticket(self, player: str, prediction: int) -> Ticket:
        if self.m1_pool is None:
            self.open_m1_pool()
        return self.m1_pool.buy_ticket(player, prediction)

    # ── M2 ───────────────────────────────────────────────────────

    def create_m2_duel(self, player: str, prediction: int,
                       amount_usdc: float) -> DuelM2:
        duel = DuelM2()
        duel.create(player, prediction, amount_usdc, self.match_id)
        self.duels[duel.duel_id] = duel
        return duel

    def join_m2_duel(self, duel_id: str, player: str,
                     prediction: int, amount_usdc: float) -> Ticket:
        duel = self.duels[duel_id]
        return duel.join(player, prediction, amount_usdc)

    # ── Resolución ───────────────────────────────────────────────

    def resolve_all(self, actual_goals: int, actual_winner: int) -> dict:
        """
        Resuelve TODO al finalizar el partido.

        Args:
            actual_goals: total de goles del partido (0-9).
            actual_winner: 1=home, 2=away, 0=draw.
        """
        self._resolved = True
        report: dict = {
            "match_id": self.match_id,
            "actual_goals": actual_goals,
            "actual_winner": ["draw", "home", "away"][actual_winner],
            "m1": None,
            "m2_duels": [],
            "total_house_cut": 0.0,
            "next_rollover": 0.0,
        }

        # M1
        if self.m1_pool and self.m1_pool.total_tickets > 0:
            m1_result = self.m1_pool.resolve(actual_goals)
            report["m1"] = m1_result
            self.house_balance += m1_result["house_cut"]
            report["total_house_cut"] += m1_result["house_cut"]
            report["next_rollover"] = m1_result["new_rollover"]

        # M2
        for duel in self.duels.values():
            m2_result = duel.resolve(actual_winner)
            report["m2_duels"].append(m2_result)
            self.house_balance += m2_result["house_cut"]
            report["total_house_cut"] += m2_result["house_cut"]

        logger.info("💰 HOUSE BALANCE: $%.2f  |  rollover: $%.2f",
                     self.house_balance, report["next_rollover"])
        return report

    def summary(self) -> dict:
        return {
            "match_id": self.match_id,
            "m1_open": self.m1_pool is not None,
            "m1_tickets": self.m1_pool.total_tickets if self.m1_pool else 0,
            "m2_duels": len(self.duels),
            "house_balance": round(self.house_balance, 2),
        }
