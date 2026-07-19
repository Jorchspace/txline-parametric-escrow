# TxLINE Parametric Escrow

**On-chain sports betting engine with cryptographic settlement.**

Built for the TxODDS Hackathon — July 2026.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TxLINE PARAMETRIC ESCROW                      │
│                                                                      │
│  ┌──────────────────┐    ┌────────────────┐    ┌──────────────────┐ │
│  │  SSE INGESTOR    │───▶│  BETTING ENGINE │───▶│  SOLANA MOCK     │ │
│  │                  │    │                │    │                  │ │
│  │  • TxLINE live   │    │  • Pool M1     │    │  • PDA derivation│ │
│  │  • Mock simulator│    │  • Versus M2    │    │  • Fund locking  │ │
│  │  • Goal detection│    │  • Fee calc     │    │  • Payout CPI    │ │
│  │  • Merkle verify │    │  • Rollover     │    │  • Tx hashing    │ │
│  └──────────────────┘    └────────────────┘    └──────────────────┘ │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    DATA PIPELINE (async)                       │   │
│  │                                                               │   │
│  │  SSE event ──▶ Parse JSON ──▶ Detect goal? ──▶ Callback ──▶  │   │
│  │                                                               │   │
│  │  SSE event ──▶ Parse JSON ──▶ FINISHED? ──▶ Resolve pools    │   │
│  │                                         ──▶ Execute payouts   │   │
│  │                                         ──▶ Print report      │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Pipeline Stages

| Stage | Module | Responsibility |
|-------|--------|---------------|
| **1. SSE Ingestor** | `stream_listener.py` | Connects to TxLINE SSE stream (mock or live). Parses JSON events. Detects goals and match completion. Emits callbacks. |
| **2. Betting Engine** | `betting_engine.py` | Manages Pool M1 and Versus M2 markets. Accepts bets. Computes payouts on resolution. Enforces house fees and rollover rules. |
| **3. Settlement Layer** | `solana_mock.py` | Simulates on-chain escrow: PDA derivation, fund locking, CPI payouts. Produces deterministic transaction hashes for auditability. |
| **4. State Exporter** | `state_exporter.py` | Atomic JSON data feed. Writes `dashboard.json` on every state change — consumable by any frontend, bot, or auditor. |
| **5. Orchestrator** | `main.py` | Wires all stages together. Runs the full demo: wallet setup → betting phase → live stream → financial resolution → balance report. |

### Module Map

| File | Lines | Purpose |
|------|-------|---------|
| `config.py` | 66 | Wallet addresses, fee constants, environment |
| `mock_stream.py` | 229 | World Cup SSE simulator (Poisson goals, merkle proofs) |
| `stream_listener.py` | 280 | Dual-mode SSE listener (mock + TxLINE live) |
| `betting_engine.py` | 463 | Core financial logic: Pool M1 + Versus M2 |
| `solana_mock.py` | 323 | Simulated Solana layer: PDAs, locks, payouts, ledger |
| `main.py` | 190 | CLI orchestrator with `--mock` and `--live` modes |

---

## Mathematical Rules

### Pool M1 — Parametric Pool with 50% Rollover

**Market:** Exact total goals in the match.

**Mechanics:**

| Parameter | Value |
|-----------|-------|
| Ticket price | $1.00 USDC (fixed) |
| Payout (with winners) | `(total_pool + rollover_in) / winner_count` |
| Payout (no winners) | 50% house, 50% rollover to next match |

**Formulas:**

With winners:
```
payout_i = (total_tickets × $1 + rollover_balance) / n_winners
rollover_next = 0
```

Without winners:
```
house_cut      = total_tickets × $1 × 0.50
rollover_next  = total_tickets × $1 × 0.50
```

**Example:**
- 10 tickets bought ($10 pool) + $15 rollover from previous match = $25 at stake
- Actual total goals: 6
- Nobody predicted 6 goals → 0 winners
- House: $5.00 (50% of $10) | Rollover: $5.00 (carries to next match)

---

### Versus M2 — 1 vs N Creator Duel

**Market:** Match winner (home / away / draw).

**Mechanics:**

| Scenario | Creator receives | Opponents receive | House receives |
|----------|-----------------|-------------------|----------------|
| **Creator WINS** | deposit + 90% of opponent pool | 0 | 10% of opponent pool |
| **Creator LOSES** | 0 | proportional share of total pool | 0 |
| **DRAW (real match)** | 80% refund | 80% refund each | 20% of all stakes |

**Formulas:**

Creator wins (`actual == creator.prediction`):
```
creator_payout = creator_stake + opponent_pool × 0.90
house_cut      = opponent_pool × 0.10
```

Draw (`actual == 0`):
```
refund_per_player  = stake × 0.80
house_cut_per_player = stake × 0.20
total_house_cut    = Σ(stake × 0.20)
```

Creator loses (`actual ∈ {1,2} and actual ≠ creator.prediction`):
```
opponent_payout_i = total_pool × (stake_i / opponent_pool)
house_cut         = 0
```

**Example:**
- Bob (creator) bets $50 on HOME
- Alice bets $30 on AWAY, Carol bets $20 on DRAW → opponent pool = $50
- Match result: HOME wins → Bob wins
- Bob: $50 (deposit returned) + $45 (90% of $50) = **$95.00**
- House: $5.00 (10% of $50)

---

## How to Run

### Prerequisites

```bash
python3 --version   # ≥ 3.10
pip install -r requirements.txt
```

### Mode 1 — Mock Simulator (default)

No API keys required. Runs a simulated World Cup match with random goals.

```bash
# Argentina vs France (default)
python main.py

# Custom teams, faster playback
python main.py --mock --home Brasil --away Alemania --speed 1.0

# With initial rollover from previous match
python main.py --mock --rollover 25.0
```

**What you see:**
1. Initial wallet balances (7 simulated devnet wallets)
2. Pool M1: 10 tickets bought at $1 each
3. Versus M2: 2 duels created with opponents
4. Live SSE stream with goals appearing in real time
5. Final resolution: winners, payouts, house fees, rollover
6. Final balances with simulated on-chain transaction hashes

### Mode 2 — Live TxLINE Stream

Requires an activated API token from TxLINE (free World Cup tier available on devnet).

```bash
# Set credentials
export TXLINE_API_TOKEN="your-api-token"
export TXLINE_JWT="your-jwt"        # optional, auto-obtained if omitted

# Devnet live stream
python main.py --live --network devnet

# Mainnet live stream with match filter
python main.py --live --network mainnet --match-id TXL-WC-001

# Pass token directly
python main.py --live --api-token "tk_abc123..."
```

**How to get a free API token (devnet):**

1. Fund a devnet wallet with SOL (airdrop)
2. Run the [TxLINE devnet activation flow](https://txline-docs.txodds.com/documentation/worldcup)
3. Use the activated `apiToken` with `--api-token`

### Run Tests

```bash
python3 -m pytest test_engine.py -v
```

---

## Decoupled UI-Ready Architecture

The system writes a structured **`dashboard.json`** state file on every event: goal detected, pool settlement, or match status transition. External consumers — dashboards, audit tools, Telegram bots, Grafana — read this file without coupling to the engine internals.

### Atomic Write Guarantee

`state_exporter.py` writes to a temp file first, then performs an atomic `os.replace()` rename. Any external reader always sees a complete, valid JSON document — never a partial write.

### Data Feed Schema

```json
{
  "dashboard": "TxLINE Parametric Escrow",
  "version": "1.0.0",
  "export_count": 6,
  "timestamp_utc": "2026-07-18T23:50:27Z",
  "system_status": "FINISHED",
  "match_id": "TXL-WC-001",
  "match_stats": {
    "status": "FINISHED",
    "home_team": "Argentina",
    "away_team": "Francia",
    "goals_home": 4,
    "goals_away": 2,
    "minute": 90,
    "phase": "finished",
    "total_goals": 6
  },
  "pool_m1": {
    "active": false,
    "total_tickets": 10,
    "total_pool": 10.0,
    "rollover_balance": 5.0,
    "total_at_stake": 15.0,
    "resolved": true,
    "winning_goals": 6,
    "tickets": [...],
    "sim_pda": "2GghHBsTeUtHiPRd...",
    "sim_vault_balance": 5.0
  },
  "versus_m2": {
    "active_duels": 0,
    "total_duels": 2,
    "duels": [
      {
        "duel_id": "fd2b216c",
        "creator": {"player": "Bobbb1111...", "prediction": 1, "won": true, "payout": 95.0},
        "opponents": [...],
        "sim_pda": "39uvDxj8Q9y5gMnc..."
      }
    ]
  },
  "platform_ledger": {
    "house_wallet": "HouSe111111...",
    "total_balance_usdc": 10.0,
    "fees_collected": {
      "m1_rollover_desert_50pct": 5.0,
      "m2_creator_win_10pct": 5.0,
      "m2_draw_protection_20pct": 5.0,
      "total": 10.0
    }
  },
  "wallet_balances": {
    "alice": {"balance": 500.0, "locked": 32.0, "available": 468.0},
    ...
  },
  "transaction_history": [
    {"tx_hash": "2fY41o4R...", "source": "ALice1111...", "amount_usdc": 1.0, "memo": "lock:M1", "slot": 300000001},
    ...
  ],
  "solana_mock": {"slot": 300000019, "total_transfers": 19, "system_balance": 1655.0}
}
```

### Export Lifecycle

```
UPCOMING (1 export)     → after bet placement, before match start
LIVE     (N exports)    → on every goal detected from the stream
FINISHED (1 export)     → after pool resolution and all payouts
```

A minimal frontend can poll `dashboard.json` every 500ms and render a live betting UI without any server — just a static HTML file with `fetch("dashboard.json")`.

## Trustless Mechanism

Every bet is escrowed on-chain through a simulated Solana program:

```
1. PLACE BET
   Player wallet ──▶ lock_funds() ──▶ PDA Escrow Vault
   tx: 9YPybWvR9TJpqLVthtMeD1HkE4MBL7hirwccpyneaQUR

2. MATCH ENDS (oracle signature)
   TxLINE oracle ──▶ merkle_proof ──▶ on-chain verification

3. RESOLVE
   PDA Vault ──▶ payout() ──▶ Winner wallet
   PDA Vault ──▶ collect_house_fee() ──▶ Platform wallet
   tx: 4AuyjjQkhLNPdWZsjpRPS7zT93RQEZjDhSKMuNbFKS5E
```

**PDA derivation (deterministic):**
```
pool_pda  = find_pda(["parametric", match_id, market], program_id)
vault_pda = find_pda(["vault", pool_pda], program_id)
bet_pda   = find_pda(["bet", player, match_id, market], program_id)
```

All transaction hashes are deterministic SHA-256 derivations, auditable on a public ledger.

---

## Design Decisions

**Why Python?** Rapid prototyping for hackathon. The pure-core/thin-shim architecture keeps financial logic isolated from I/O, making it trivial to port the `betting_engine.py` to Rust/WASM for production deployment as a ZeroClaw plugin or Anchor program.

**Why mock mode?** Enables offline development and deterministic demo recordings without depending on live sports schedules.

**Why dual-mode listener?** The `TxLineListener` accepts both mock and live event sources through the same callback interface. The betting engine never knows — and shouldn't care — where the data comes from.

---

## What's Next

- [ ] **Anchor program**: Port `betting_engine.py` + `solana_mock.py` to a real Solana program with SPL Token-2022 integration
- [ ] **ZeroClaw plugin**: Package Pool M1 as a WASM tool for agent-driven betting
- [ ] **Frontend**: Telegram bot that accepts bets via chat messages
- [ ] **Multi-match**: Tournament mode with cross-match rollover tracking

---

## License

MIT — Jorch Lab, July 2026.
