"""
stream_listener.py — Escucha el stream SSE (mock simulado o TxLINE real) y
dispara el motor de apuestas.

Modos:
  --mock (default):  WorldCupSimulator local con goles aleatorios.
  --live:            Conexión real al SSE de TxLINE (devnet o mainnet).

En ambos modos, el listener emite callbacks goal/finish que el betting_engine
consume de forma idéntica. La fuente de datos es intercambiable.
"""

from typing import Callable, Optional
import json
import logging
import os
import time

from mock_stream import WorldCupSimulator, TxLineEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tipos de callback
# ---------------------------------------------------------------------------

GoalCallback   = Callable[[TxLineEvent], None]
FinishCallback = Callable[[TxLineEvent], None]


# ---------------------------------------------------------------------------
# Cliente SSE real de TxLINE
# ---------------------------------------------------------------------------

class TxLineLiveClient:
    """
    Cliente HTTP para el stream SSE real de TxLINE.

    Usa requests con stream=True para consumir eventos Server-Sent Events
    del endpoint de scores de TxLINE.

    Uso:
        client = TxLineLiveClient(api_token="xxx", network="devnet")
        for event in client.stream():
            print(event)
    """

    def __init__(
        self,
        api_token: Optional[str] = None,
        jwt_token: Optional[str] = None,
        network: str = "devnet",
    ):
        self.api_token = api_token or os.environ.get("TXLINE_API_TOKEN", "")
        self.jwt_token = jwt_token or os.environ.get("TXLINE_JWT", "")
        self.network = network

        if network == "mainnet":
            self.api_origin = "https://txline.txodds.com"
        else:
            self.api_origin = "https://txline-dev.txodds.com"

        self.stream_url = f"{self.api_origin}/api/scores/stream"
        self.auth_url = f"{self.api_origin}/auth/guest/start"

    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------

    def ensure_jwt(self) -> str:
        """Obtiene un guest JWT si no tenemos uno."""
        if self.jwt_token:
            return self.jwt_token

        try:
            import requests
            resp = requests.post(self.auth_url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            self.jwt_token = data.get("token", "")
            logger.info("🔑 JWT obtenido: %s...", self.jwt_token[:20])
            return self.jwt_token
        except Exception as e:
            logger.error("❌ No se pudo obtener JWT de %s: %s", self.auth_url, e)
            return ""

    # ------------------------------------------------------------------
    # Stream SSE
    # ------------------------------------------------------------------

    def stream(self, match_id: Optional[str] = None):
        """
        Generador que yields eventos del stream SSE real.

        Args:
            match_id: opcional, filtra por partido específico.

        Yields:
            TxLineEvent por cada línea SSE parseada.
        """
        try:
            import requests
        except ImportError:
            raise ImportError(
                "requests no está instalado. Ejecutá: pip install requests"
            )

        jwt = self.ensure_jwt()
        if not jwt:
            logger.warning("⚠️  Sin JWT — usando stream mock como fallback")
            return

        headers = {
            "Authorization": f"Bearer {jwt}",
            "Accept": "text/event-stream",
        }
        if self.api_token:
            headers["X-Api-Token"] = self.api_token

        logger.info("📡 Conectando a %s ...", self.stream_url)

        try:
            response = requests.get(
                self.stream_url,
                headers=headers,
                stream=True,
                timeout=(10, 300),  # (connect, read) timeout
            )
            response.raise_for_status()

            event_count = 0
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if not line.startswith("data: "):
                    continue

                payload_str = line[6:]  # quitar "data: "
                try:
                    raw = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue

                # Mapear campos del JSON real de TxLINE a TxLineEvent
                event = self._map_to_event(raw)
                if event is None:
                    continue

                # Filtrar por match_id si se especificó
                if match_id and event.match_id != match_id:
                    continue

                event_count += 1
                yield event

        except requests.exceptions.ConnectionError as e:
            logger.error("❌ Error de conexión al stream TxLINE: %s", e)
            logger.info("💡 ¿Estás en devnet? Asegurate de tener un token activado.")
        except requests.exceptions.Timeout as e:
            logger.error("❌ Timeout en stream TxLINE: %s", e)
        except Exception as e:
            logger.error("❌ Error inesperado en stream: %s", e)

    # ------------------------------------------------------------------
    # Mapeo de campos
    # ------------------------------------------------------------------

    def _map_to_event(self, raw: dict) -> Optional[TxLineEvent]:
        """
        Convierte el JSON real de TxLINE a nuestro TxLineEvent.

        El esquema real de TxLINE puede variar. Este mapper acepta múltiples
        nombres de campo para ser robusto:

          matchStatus / match_status  →  "LIVE" | "FINISHED"
          goalsHome  / goals_home     →  int
          goalsAway  / goals_away     →  int
          minute     / matchMinute    →  int
          phase      / matchPhase     →  str
          homeTeam   / home_team      →  str
          awayTeam   / away_team      →  str
          merkleProof / merkle_proof  →  str | null
          matchId    / match_id       →  str
        """
        def _get(*keys):
            for k in keys:
                if k in raw:
                    return raw[k]
            return None

        status = _get("matchStatus", "match_status")
        if status is None:
            return None

        goals_home = int(_get("goalsHome", "goals_home") or 0)
        goals_away = int(_get("goalsAway", "goals_away") or 0)
        minute     = int(_get("minute", "matchMinute") or 0)
        phase      = _get("phase", "matchPhase") or "unknown"
        home_team  = _get("homeTeam", "home_team") or "Home"
        away_team  = _get("awayTeam", "away_team") or "Away"
        match_id   = _get("matchId", "match_id") or "TXL-UNKNOWN"
        merkle     = _get("merkleProof", "merkle_proof")
        event_id   = _get("eventId", "event_id") or f"{match_id}-live"
        timestamp  = _get("timestampUtc", "timestamp_utc") or ""

        return TxLineEvent(
            event_id=event_id,
            match_id=match_id,
            match_status=status.upper(),
            timestamp_utc=timestamp,
            home_team=home_team,
            away_team=away_team,
            goals_home=goals_home,
            goals_away=goals_away,
            minute=minute,
            phase=phase,
            merkle_proof=merkle,
        )


# ---------------------------------------------------------------------------
# Listener (modo dual: mock + live)
# ---------------------------------------------------------------------------

class TxLineListener:
    """
    Escucha el stream (mock o live) y dispara callbacks.

    Modo mock (default):
        listener = TxLineListener()
        listener.listen_mock("Argentina", "Francia")

    Modo live:
        listener = TxLineListener()
        listener.listen_live(api_token="xxx", network="devnet")
    """

    def __init__(self):
        self._goal_cb: list = []
        self._finish_cb: list = []
        self.last_event: Optional[TxLineEvent] = None
        self.final_goals_home: int = 0
        self.final_goals_away: int = 0
        self.final_winner: int = 0
        self.merkle_proof: Optional[str] = None
        self.home_team: str = ""
        self.away_team: str = ""

    def on_goal(self, cb: GoalCallback) -> None:
        self._goal_cb.append(cb)

    def on_finish(self, cb: FinishCallback) -> None:
        self._finish_cb.append(cb)

    # ── Modo Mock ─────────────────────────────────────────────────

    def listen_mock(self, home: str = "Argentina", away: str = "Francia",
                    speed: float = 5.0) -> "TxLineListener":
        """
        Corre el simulador local y despacha eventos.

        Args:
            home:  equipo local.
            away:  equipo visitante.
            speed: segundos entre eventos (default 5s).
        """
        self.home_team = home
        self.away_team = away

        sim = WorldCupSimulator(home=home, away=away, interval_sec=speed)
        prev_goals = 0

        print(f"\n{'━'*60}")
        print(f"  📡  TxLINE SSE STREAM (MOCK)")
        print(f"  ⚽  {home} vs {away}")
        print(f"{'━'*60}\n")

        for event in sim.run():
            self._display(event, prev_goals)
            if event.goals_home + event.goals_away > prev_goals:
                prev_goals = event.goals_home + event.goals_away
                for cb in self._goal_cb:
                    cb(event)

            if event.match_status == "FINISHED":
                self._finalize(event)
                break

        return self

    # ── Modo Live ─────────────────────────────────────────────────

    def listen_live(
        self,
        api_token: Optional[str] = None,
        jwt_token: Optional[str] = None,
        network: str = "devnet",
        match_id: Optional[str] = None,
    ) -> "TxLineListener":
        """
        Conecta al stream SSE real de TxLINE y despacha eventos.

        Args:
            api_token:  token de API activado (o TXLINE_API_TOKEN env var).
            jwt_token:  guest JWT (o TXLINE_JWT env var). Si no, se obtiene solo.
            network:    "devnet" | "mainnet".
            match_id:   opcional, filtrar por partido específico.
        """
        client = TxLineLiveClient(
            api_token=api_token,
            jwt_token=jwt_token,
            network=network,
        )

        print(f"\n{'━'*60}")
        print(f"  📡  TxLINE SSE STREAM (LIVE — {network})")
        print(f"  🔗 {client.stream_url}")
        print(f"{'━'*60}\n")

        prev_goals = 0
        event_count = 0

        for event in client.stream(match_id=match_id):
            event_count += 1

            # Detectar equipos del primer evento
            if not self.home_team and event.home_team:
                self.home_team = event.home_team
                self.away_team = event.away_team

            self._display(event, prev_goals)

            if event.goals_home + event.goals_away > prev_goals:
                prev_goals = event.goals_home + event.goals_away
                for cb in self._goal_cb:
                    cb(event)

            if event.match_status.upper() == "FINISHED":
                self._finalize(event)
                break

        if event_count == 0:
            logger.warning("⚠️  No se recibieron eventos del stream live.")
            logger.info("💡 Verificá que el API token esté activado y el JWT sea válido.")

        return self

    # ── Display común ─────────────────────────────────────────────

    def _display(self, event: TxLineEvent, prev_goals: int) -> None:
        self.last_event = event
        ts = event.timestamp_utc[11:19] if len(event.timestamp_utc) >= 19 else ""
        score = f"{event.goals_home}-{event.goals_away}"
        icon = {"LIVE": "🟢", "FINISHED": "🏁"}.get(event.match_status.upper(), "⚪")

        if event.goals_home + event.goals_away > prev_goals:
            print(f"  {icon} [{ts}]  🥅  GOL!   {score:5s}  min {event.minute:3d}'  ({event.match_status})")
        else:
            print(f"  {icon} [{ts}]       {score:5s}  min {event.minute:3d}'  {event.phase:12s}  ({event.match_status})")

    # ── Finalización común ────────────────────────────────────────

    def _finalize(self, event: TxLineEvent) -> None:
        self.final_goals_home = event.goals_home
        self.final_goals_away = event.goals_away
        self.home_team = event.home_team
        self.away_team = event.away_team

        if event.goals_home > event.goals_away:
            self.final_winner = 1
        elif event.goals_away > event.goals_home:
            self.final_winner = 2
        else:
            self.final_winner = 0

        self.merkle_proof = event.merkle_proof
        score = f"{event.goals_home}-{event.goals_away}"

        print(f"\n  🏁 PARTIDO FINALIZADO")
        print(f"  Resultado: {event.home_team} {score} {event.away_team}")
        print(f"  Total goles: {event.goals_home + event.goals_away}")
        if event.merkle_proof:
            print(f"  merkle_proof: {event.merkle_proof}\n")

        for cb in self._finish_cb:
            cb(event)
