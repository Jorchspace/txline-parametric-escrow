"""
config.py — Variables de entorno, llaves simuladas y fees del sistema TxLINE.

Reglas de negocio:
  - Pool M1:  tickets de $1 USDC c/u. 50% rollover si nadie gana.
  - Versus M2: 1vsN. Draw → 80% reembolso, 20% casa.
"""

from dataclasses import dataclass, field
from typing import List

# ---------------------------------------------------------------------------
# Fees (hardcodeados según reglas)
# ---------------------------------------------------------------------------

M1_TICKET_PRICE    = 1.0       # USDC — precio fijo por ticket Pool M1
M1_ROLLOVER_PCT    = 0.50      # 50% del pool va a rollover si nadie acierta
M1_HOUSE_CUT_PCT   = 0.50      # 50% del pool va a la casa si nadie acierta

M2_DRAW_REFUND_PCT = 0.80      # 80% reembolso a cada jugador en empate
M2_DRAW_HOUSE_PCT  = 0.20      # 20% a la casa en empate
M2_CREATOR_WIN_PCT = 0.90      # 90% del pool de oponentes va al ganador
M2_HOUSE_CUT_PCT   = 0.10      # 10% del pool de oponentes va a la casa


# ---------------------------------------------------------------------------
# Devnet simulated wallets
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SimWallet:
    name: str
    address: str
    balance_usdc: float = 0.0


HOUSE = SimWallet(
    name="platform_wallet",
    address="HouSe1111111111111111111111111111111111111",
    balance_usdc=0.0,
)

ORACLE = SimWallet(
    name="txodds_oracle",
    address="OracLe111111111111111111111111111111111111",
    balance_usdc=0.0,
)

PLAYERS: List[SimWallet] = [
    SimWallet("alice",   "ALice111111111111111111111111111111111111", 500.0),
    SimWallet("bob",     "Bobbb111111111111111111111111111111111111", 300.0),
    SimWallet("carol",   "CaroL1111111111111111111111111111111111111", 200.0),
    SimWallet("dave",    "DaveE1111111111111111111111111111111111111", 400.0),
    SimWallet("eve",     "EveeE1111111111111111111111111111111111111", 100.0),
]


@dataclass
class Env:
    rpc_url: str            = "https://api.devnet.solana.com"
    house: SimWallet        = HOUSE
    oracle: SimWallet       = ORACLE
    players: List[SimWallet] = field(default_factory=lambda: PLAYERS)


env = Env()
