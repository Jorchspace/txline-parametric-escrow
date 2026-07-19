"""
mock_stream.py — Simulador de Server-Sent Events (SSE) para la Copa del Mundo.

Emite payloads JSON cada 5 segundos simulando un partido Argentina vs Francia:
  - match_status: "LIVE" con marcador actualizado aleatoriamente
  - goles: goals_home, goals_away (distribución Poisson)
  - evento final: match_status = "FINISHED" + merkle_proof simulado

Payload JSON Schema (compatible TxLINE):
  {
    "event_id": "TXL-XXXXX-0001",
    "match_id": "TXL-WC-001",
    "match_status": "LIVE" | "FINISHED",
    "timestamp_utc": "2026-07-18T...",
    "home_team": "Argentina",
    "away_team": "Francia",
    "goals_home": 0,
    "goals_away": 1,
    "minute": 23,
    "phase": "first_half",
    "merkle_proof": null | "0xabc..."
  }
"""

from dataclasses import dataclass, asdict
from typing import Generator, Optional
import hashlib
import json
import random
import time
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Evento TxLINE
# ---------------------------------------------------------------------------

@dataclass
class TxLineEvent:
    event_id: str
    match_id: str
    match_status: str        # "LIVE" | "FINISHED"
    timestamp_utc: str
    home_team: str
    away_team: str
    goals_home: int
    goals_away: int
    minute: int
    phase: str               # first_half | second_half | finished
    merkle_proof: Optional[str] = None

    def to_sse(self) -> str:
        return f"data: {json.dumps(asdict(self))}\n\n"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Firma criptográfica simulada (merkle_proof)
# ---------------------------------------------------------------------------

def _sim_merkle_root(match_id: str, score_home: int, score_away: int) -> str:
    """Genera un merkle_proof simulado para el resultado."""
    leaf = f"{match_id}:{score_home}-{score_away}"
    h = hashlib.sha256(leaf.encode()).hexdigest()
    return f"0x{h}"


# ---------------------------------------------------------------------------
# Simulador de partido
# ---------------------------------------------------------------------------

class WorldCupSimulator:
    """
    Genera un stream sintético de un partido completo de Copa del Mundo.

    Uso:
        sim = WorldCupSimulator("Argentina", "Francia")
        for event in sim.run():
            print(event.to_sse())
    """

    PLAYERS_HOME = ["Messi", "Álvarez", "Di María", "De Paul", "Enzo",
                    "Mac Allister", "Otamendi", "Romero", "Acuña", "Molina"]
    PLAYERS_AWAY = ["Mbappé", "Griezmann", "Giroud", "Dembélé", "Tchouaméni",
                    "Rabiot", "Koundé", "Upamecano", "Hernandez", "Lloris"]

    def __init__(
        self,
        home: str = "Argentina",
        away: str = "Francia",
        match_id: str = "TXL-WC-001",
        duration_min: int = 90,
        interval_sec: float = 5.0,
        seed: int = 42,
    ):
        self.home = home
        self.away = away
        self.match_id = match_id
        self.duration = duration_min
        self.interval = interval_sec
        self._rng = random.Random(seed)

        # Estado
        self.score_home = 0
        self.score_away = 0
        self.minute = 0
        self.event_count = 0
        self.total_goals = 0
        self.goal_events: list = []
        self._plan_goals()

    # ------------------------------------------------------------------
    # Plan de goles (distribución Poisson λ=2.5)
    # ------------------------------------------------------------------

    def _plan_goals(self) -> None:
        total = self._rng.choices(
            [0, 1, 2, 3, 4, 5, 6, 7],
            weights=[5, 12, 20, 22, 18, 10, 2, 1],
            k=1,
        )[0]

        for _ in range(total):
            minute = self._rng.randint(1, self.duration + 6)
            team = self._rng.choice(["home", "away"])
            if team == "home":
                self.score_home += 1
            else:
                self.score_away += 1
            self.goal_events.append({
                "minute": minute,
                "team": team,
                "player": self._rng.choice(
                    self.PLAYERS_HOME if team == "home" else self.PLAYERS_AWAY
                ),
            })

        self.goal_events.sort(key=lambda g: g["minute"])
        self.total_goals = total

    # ------------------------------------------------------------------
    # Generador principal
    # ------------------------------------------------------------------

    def run(self) -> Generator[TxLineEvent, None, None]:
        """
        Yields eventos por ~10 minutos de partido cada tick.
        tick = 5 segundos reales → ~10 min de juego.
        """
        pending = list(self.goal_events)
        self.minute = 0

        # ── Primer evento: Lanzamiento ──
        yield self._emit("LIVE", "first_half")

        # ── Loop del partido ──
        while self.minute < self.duration:
            advance = self._rng.randint(6, 14)
            self.minute = min(self.minute + advance, self.duration)
            phase = "first_half" if self.minute <= 45 else "second_half"

            # Goles en esta ventana
            while pending and pending[0]["minute"] <= self.minute:
                g = pending.pop(0)
                if g["team"] == "home":
                    self.score_home += 1
                else:
                    self.score_away += 1
                # Emitir evento de gol como status update
                yield self._emit("LIVE", phase)

            # Snapshot normal
            yield self._emit("LIVE", phase)
            time.sleep(self.interval)

        # ── Goles en tiempo añadido ──
        while pending:
            g = pending.pop(0)
            if g["team"] == "home":
                self.score_home += 1
            else:
                self.score_away += 1
            self.minute += self._rng.randint(1, 3)
            yield self._emit("LIVE", "extra_time")
            time.sleep(self.interval)

        # ── Evento FINISHED ──
        merkle = _sim_merkle_root(self.match_id, self.score_home, self.score_away)
        yield self._emit("FINISHED", "finished", merkle_proof=merkle)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit(self, status: str, phase: str,
              merkle_proof: Optional[str] = None) -> TxLineEvent:
        self.event_count += 1
        return TxLineEvent(
            event_id=f"{self.match_id}-{self.event_count:04d}",
            match_id=self.match_id,
            match_status=status,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            home_team=self.home,
            away_team=self.away,
            goals_home=self.score_home,
            goals_away=self.score_away,
            minute=self.minute,
            phase=phase,
            merkle_proof=merkle_proof,
        )


# ---------------------------------------------------------------------------
# Demo directo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sim = WorldCupSimulator("Argentina", "Francia")
    print(f"\n{'='*60}")
    print(f"  ⚽  {sim.home} vs {sim.away}  |  ~{sim.total_goals} goles esperados")
    print(f"{'='*60}\n")
    for evt in sim.run():
        ts = evt.timestamp_utc[11:19]
        score = f"{evt.goals_home}-{evt.goals_away}"
        print(f"[{ts}] {evt.match_status:8s}  {score:5s}  min {evt.minute:3d}'  {evt.phase}")
        if evt.merkle_proof:
            print(f"       merkle_proof: {evt.merkle_proof}")
