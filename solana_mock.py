"""
solana_mock.py — Capa Blockchain Simetrizada (Trustless Mechanism).

Simula:
  - Llaves Públicas (Pubkeys base58) para cada wallet.
  - PDAs deterministas para el Escrow Program.
  - Bloqueo de saldos al abrir apuesta (PDA Escrow).
  - Transferencias finales con hashes de transacción falsos impresos en consola.
  - Ledger completo de transfers para auditoría.

No usa solana-sdk ni solana-py.  Implementación inline de base58 + PDA derivation.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import hashlib
import logging
import time
import uuid

from config import env, SimWallet

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base58 inline
# ---------------------------------------------------------------------------

_B58 = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58R = {b: i for i, b in enumerate(_B58)}


def b58encode(data: bytes) -> str:
    leading = len(data) - len(data.lstrip(b"\x00"))
    n = int.from_bytes(data, "big")
    chars = []
    while n:
        n, r = divmod(n, 58)
        chars.append(chr(_B58[r]))
    return "1" * leading + "".join(reversed(chars))


# ---------------------------------------------------------------------------
# PDA derivation (simulada)
# ---------------------------------------------------------------------------

PDA_MARKER = b"ProgramDerivedAddress"


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def find_pda(seeds: List[bytes], program_id: bytes) -> Tuple[str, int]:
    """Simula Pubkey.findProgramAddress. Retorna (pda_b58, bump)."""
    for bump in range(255, -1, -1):
        preimage = b"".join(seeds) + bytes([bump]) + program_id + PDA_MARKER
        h = sha256(preimage)
        try:
            h_int = int.from_bytes(h, "big")
            # Heurística: un punto NO está en la curva si el MSB del último byte
            # está claro (~50% del espacio).  Esto emula el comportamiento real.
            if h[31] & 0x80 == 0:
                return b58encode(h), bump
        except Exception:
            pass
    return b58encode(sha256(b"".join(seeds))), 255


def fake_tx_hash(data: str) -> str:
    """Genera un hash de transacción simulado estilo Solana."""
    h = sha256(f"{data}{time.time()}{uuid.uuid4()}".encode())
    return b58encode(h)[:44]


# ---------------------------------------------------------------------------
# Wallet simulado
# ---------------------------------------------------------------------------

@dataclass
class Wallet:
    address: str
    name: str
    balance_usdc: float
    locked_usdc: float = 0.0       # fondos bloqueados en escrow PDA

    @property
    def available(self) -> float:
        return self.balance_usdc - self.locked_usdc

    def lock(self, amount: float) -> bool:
        if self.available < amount:
            return False
        self.locked_usdc += amount
        return True

    def unlock(self, amount: float) -> None:
        self.locked_usdc = max(0.0, self.locked_usdc - amount)

    def debit(self, amount: float) -> bool:
        if self.available < amount:
            return False
        self.balance_usdc -= amount
        return True

    def credit(self, amount: float) -> None:
        self.balance_usdc += amount


# ---------------------------------------------------------------------------
# PDA Escrow
# ---------------------------------------------------------------------------

@dataclass
class EscrowVault:
    """Simula una cuenta PDA del programa de escrow paramétrico."""
    address: str           # PDA derivada
    authority: str         # program_id (dueño del vault)
    token_account: str     # ATA del vault (donde se guardan los USDC)
    locked_usdc: float = 0.0

    def deposit(self, amount: float) -> None:
        self.locked_usdc += amount
        logger.info("   🔒 ESCROW DEPOSIT  $%.2f USDC → vault %s", amount, self.address[:12])

    def withdraw(self, amount: float) -> bool:
        if self.locked_usdc < amount:
            return False
        self.locked_usdc -= amount
        return True


# ---------------------------------------------------------------------------
# Transfer record
# ---------------------------------------------------------------------------

@dataclass
class Transfer:
    tx_hash: str
    source: str
    destination: str
    amount_usdc: float
    memo: str
    slot: int
    confirmed: bool = True


# ---------------------------------------------------------------------------
# Solana Mock Client
# ---------------------------------------------------------------------------

PROGRAM_ID = b"EscrowParametric111111111111111111"


class SolanaMock:
    """
    Cliente simulado de Solana Devnet.

    Flujo:
      1. open_bet()      → lockea fondos del wallet → PDA Escrow
      2. resolve_bet()   → libera fondos de PDA → transfiere a ganador/casa
      3. Cada operación imprime un tx_hash falso en consola.
    """

    def __init__(self):
        self.slot: int = 300_000_000
        self.wallets: Dict[str, Wallet] = {}
        self.vaults: Dict[str, EscrowVault] = {}    # key = pool_key
        self.ledger: List[Transfer] = []

        # Cargar wallets del config
        for p in env.players:
            self.wallets[p.address] = Wallet(p.address, p.name, p.balance_usdc)
        self.wallets[env.house.address] = Wallet(
            env.house.address, env.house.name, env.house.balance_usdc)
        self.wallets[env.oracle.address] = Wallet(
            env.oracle.address, env.oracle.name, env.oracle.balance_usdc)

    # ------------------------------------------------------------------
    # PDA helpers
    # ------------------------------------------------------------------

    def _derive_vault(self, match_id: str, model: str) -> EscrowVault:
        seeds = [b"parametric", match_id.encode(), model.encode()]
        pda, _ = find_pda(seeds, PROGRAM_ID)
        ata_seeds = [b"vault", pda.encode()]
        ata, _ = find_pda(ata_seeds, PROGRAM_ID)
        return EscrowVault(address=pda, authority=b58encode(PROGRAM_ID),
                           token_account=ata)

    def get_vault(self, match_id: str, model: str) -> EscrowVault:
        key = f"{match_id}:{model}"
        if key not in self.vaults:
            self.vaults[key] = self._derive_vault(match_id, model)
            logger.info("🏦 VAULT DERIVADO  %s  →  PDA: %s",
                         key, self.vaults[key].address[:16])
        return self.vaults[key]

    # ------------------------------------------------------------------
    # Operaciones on-chain simuladas
    # ------------------------------------------------------------------

    def lock_funds(self, player_addr: str, match_id: str,
                   model: str, amount: float) -> Optional[str]:
        """
        Bloquea USDC del wallet del jugador → PDA Escrow.

        Steps (CPI simulado):
          1. create_account(escrow_token_account, ...)
          2. token::transfer(player → escrow)
          3. escrow::lock(amount)

        Returns: tx_hash falso
        """
        wallet = self.wallets.get(player_addr)
        if not wallet:
            logger.error("❌ Wallet no encontrado: %s", player_addr[:12])
            return None
        if not wallet.lock(amount):
            logger.error("❌ Fondos insuficientes: %s (disponible $%.2f)",
                         wallet.name, wallet.available)
            return None

        vault = self.get_vault(match_id, model)
        vault.deposit(amount)

        self.slot += 1
        tx = fake_tx_hash(f"lock:{player_addr}:{match_id}:{model}:{amount}")
        self.ledger.append(Transfer(
            tx_hash=tx, source=player_addr, destination=vault.address,
            amount_usdc=amount, memo=f"lock:{model}", slot=self.slot,
        ))

        print(f"   🔐 LOCK  {wallet.name:8s}  →  ${amount:>8.2f} USDC  |  tx: {tx}")
        return tx

    def payout(self, player_addr: str, match_id: str,
               model: str, amount: float) -> Optional[str]:
        """
        Libera fondos del PDA Escrow → wallet del ganador.

        Steps (CPI simulado):
          1. escrow::unlock(amount) — firma del oracle
          2. token::transfer(escrow → player)

        Returns: tx_hash falso
        """
        wallet = self.wallets.get(player_addr)
        if not wallet:
            logger.error("❌ Wallet no encontrado: %s", player_addr[:12])
            return None

        vault = self.get_vault(match_id, model)
        if not vault.withdraw(amount):
            logger.error("❌ Vault sin fondos: %s", vault.address[:12])
            return None

        wallet.unlock(amount)
        wallet.credit(amount)

        self.slot += 1
        tx = fake_tx_hash(f"payout:{player_addr}:{match_id}:{amount}")
        self.ledger.append(Transfer(
            tx_hash=tx, source=vault.address, destination=player_addr,
            amount_usdc=amount, memo=f"payout:{model}", slot=self.slot,
        ))

        print(f"   💸 PAYOUT  {wallet.name:8s}  ←  ${amount:>8.2f} USDC  |  tx: {tx}")
        return tx

    def collect_house_fee(self, match_id: str, model: str,
                          amount: float) -> Optional[str]:
        """
        Transfiere comisión de la casa desde el vault.
        """
        vault = self.get_vault(match_id, model)
        house = self.wallets[env.house.address]

        if not vault.withdraw(amount):
            return None
        house.credit(amount)

        self.slot += 1
        tx = fake_tx_hash(f"house:{match_id}:{model}:{amount}")
        self.ledger.append(Transfer(
            tx_hash=tx, source=vault.address, destination=house.address,
            amount_usdc=amount, memo=f"house_fee:{model}", slot=self.slot,
        ))

        print(f"   🏛️  HOUSE   platform_wallet  ←  ${amount:>8.2f} USDC  |  tx: {tx}")
        return tx

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    def get_balance(self, address: str) -> float:
        w = self.wallets.get(address)
        return w.balance_usdc if w else 0.0

    def get_available(self, address: str) -> float:
        w = self.wallets.get(address)
        return w.available if w else 0.0

    def print_balances(self) -> None:
        """Imprime todos los balances en formato tabla."""
        print(f"\n{'─'*60}")
        print(f"{'WALLET':12s} {'ADDRESS':20s} {'BALANCE':>10s} {'LOCKED':>10s}")
        print(f"{'─'*60}")
        for w in self.wallets.values():
            print(f"{w.name:12s} {w.address[:16]:16s}… {w.balance_usdc:>10.2f} {w.locked_usdc:>10.2f}")
        for v in self.vaults.values():
            print(f"{'[vault]':12s} {v.address[:16]:16s}… {v.locked_usdc:>10.2f} {'—':>10s}")
        print(f"{'─'*60}\n")

    def summary(self) -> dict:
        return {
            "slot": self.slot,
            "wallets": len(self.wallets),
            "vaults": len(self.vaults),
            "transfers": len(self.ledger),
            "total_balance": sum(w.balance_usdc for w in self.wallets.values()),
        }
