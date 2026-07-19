"""
server.py — Flask micro-server for TxLINE Parametric Escrow.

Endpoints:
  GET /              → renders the live betting dashboard (index.html)
  GET /api/status    → returns dashboard.json as JSON (decoupled data feed)

Run alongside main.py. Both share dashboard.json via filesystem.
"""

import json
import os
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

DASHBOARD_PATH = "/shared_data/dashboard.json"

# ---------------------------------------------------------------------------
# Inline HTML template (single-file, no external deps except Tailwind CDN)
# ---------------------------------------------------------------------------

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TxLINE Parametric Escrow — Live Dashboard</title>
<script src="https://cdn.tailwindcss.com"></script>
<script>tailwind.config={darkMode:'class',theme:{extend:{colors:{brand:{50:'#f0fdfa',500:'#14b8a6',600:'#0d9488',700:'#0f766e',900:'#134e4a'},panel:'#0f172a',card:'#1e293b',border:'#334155',muted:'#94a3b8'}}}}</script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
  body{font-family:'Inter',sans-serif;background:#020617;color:#e2e8f0}
  .mono{font-family:'JetBrains Mono',monospace}
  .glow-teal{box-shadow:0 0 20px rgba(20,184,166,0.15)}
  .glow-amber{box-shadow:0 0 20px rgba(245,158,11,0.15)}
  .glow-red{box-shadow:0 0 20px rgba(239,68,68,0.15)}
  .pulse{animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
  .gradient-text{background:linear-gradient(135deg,#14b8a6,#06b6d4);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
  .tx-row{transition:all .2s}
  .tx-row:hover{background:#1e293b}
  @keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
  .fade-in{animation:fadeIn .3s ease-out}
</style>
</head>
<body class="min-h-screen">
  <!-- Header -->
  <header class="border-b border-border bg-panel/80 backdrop-blur sticky top-0 z-50">
    <div class="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-500 to-cyan-500 flex items-center justify-center text-lg">⚡</div>
        <div>
          <h1 class="text-lg font-bold gradient-text">TxLINE Parametric Escrow</h1>
          <p class="text-xs text-muted">Decoupled UI · Live Data Feed</p>
        </div>
      </div>
      <div class="flex items-center gap-4">
        <div class="flex items-center gap-2"><span class="w-2 h-2 rounded-full bg-green-400 pulse" id="statusDot"></span><span class="text-xs text-muted font-mono" id="clock">--:--:--</span></div>
        <span class="text-xs text-muted">Dashboard v1.0</span>
      </div>
    </div>
  </header>

  <main class="max-w-7xl mx-auto px-4 py-6 space-y-4">
    <!-- Match Banner -->
    <div id="matchBanner" class="rounded-2xl bg-card border border-border p-6 glow-teal fade-in">
      <div class="flex items-center justify-between">
        <div class="text-center flex-1"><p class="text-3xl font-bold" id="homeTeam">---</p><p class="text-xs text-muted mt-1">HOME</p></div>
        <div class="text-center px-6">
          <div class="text-4xl font-black mono tracking-wider" id="score">0-0</div>
          <div class="mt-2"><span class="px-3 py-1 rounded-full text-xs font-semibold uppercase tracking-wider" id="statusBadge">UPCOMING</span></div>
        </div>
        <div class="text-center flex-1"><p class="text-3xl font-bold" id="awayTeam">---</p><p class="text-xs text-muted mt-1">AWAY</p></div>
      </div>
      <div class="flex justify-between mt-4 text-xs text-muted mono">
        <span id="matchId">---</span><span id="matchMinute">--'</span><span id="matchPhase">---</span><span id="totalGoals">⚽ 0</span>
      </div>
    </div>

    <!-- Pools Row -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <!-- M1 Pool -->
      <div class="rounded-2xl bg-card border border-border p-5 glow-teal fade-in">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-semibold uppercase tracking-wider text-brand-500">Pool M1 · $1 Tickets</h2>
          <span class="text-xs text-muted mono" id="m1Pda">---</span>
        </div>
        <div class="grid grid-cols-3 gap-3 mb-3">
          <div class="bg-panel rounded-xl p-3 text-center"><p class="text-xs text-muted">Tickets</p><p class="text-xl font-bold" id="m1Tickets">0</p></div>
          <div class="bg-panel rounded-xl p-3 text-center"><p class="text-xs text-muted">At Stake</p><p class="text-xl font-bold text-brand-500">$<span id="m1Stake">0</span></p></div>
          <div class="bg-panel rounded-xl p-3 text-center"><p class="text-xs text-muted">Rollover</p><p class="text-xl font-bold text-amber-400">$<span id="m1Rollover">0</span></p></div>
        </div>
        <div id="m1Winners" class="text-xs"></div>
        <div class="mt-2 text-xs text-muted"><span id="m1VaultBal">Vault: $0</span></div>
      </div>

      <!-- M2 Duels -->
      <div class="rounded-2xl bg-card border border-border p-5 glow-amber fade-in">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-semibold uppercase tracking-wider text-amber-400">Versus M2 · 1vsN Duels</h2>
          <span class="text-xs text-muted mono" id="m2Pda">---</span>
        </div>
        <div class="grid grid-cols-2 gap-3 mb-3">
          <div class="bg-panel rounded-xl p-3 text-center"><p class="text-xs text-muted">Active Duels</p><p class="text-xl font-bold" id="m2Active">0</p></div>
          <div class="bg-panel rounded-xl p-3 text-center"><p class="text-xs text-muted">Total Duels</p><p class="text-xl font-bold" id="m2Total">0</p></div>
        </div>
        <div id="m2Duels" class="space-y-2 max-h-48 overflow-y-auto"></div>
      </div>
    </div>

    <!-- Ledger + TX Row -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <!-- Platform Ledger -->
      <div class="rounded-2xl bg-card border border-border p-5 glow-red fade-in">
        <h2 class="text-sm font-semibold uppercase tracking-wider text-red-400 mb-3">🏛️ Platform Ledger</h2>
        <div class="space-y-2">
          <div class="flex justify-between text-sm"><span class="text-muted">House Wallet</span><span class="mono text-xs" id="houseAddr">---</span></div>
          <div class="flex justify-between text-sm"><span class="text-muted">Balance</span><span class="font-bold text-green-400">$<span id="houseBal">0</span></span></div>
          <div class="border-t border-border my-2"></div>
          <div class="flex justify-between text-xs"><span class="text-muted">M1 Desert 50%</span><span class="mono text-red-300">$<span id="feeM1">0</span></span></div>
          <div class="flex justify-between text-xs"><span class="text-muted">M2 Creator Win 10%</span><span class="mono text-red-300">$<span id="feeM2Creator">0</span></span></div>
          <div class="flex justify-between text-xs"><span class="text-muted">M2 Draw 20%</span><span class="mono text-red-300">$<span id="feeM2Draw">0</span></span></div>
          <div class="border-t border-border my-2"></div>
          <div class="flex justify-between text-sm font-bold"><span>Total Collected</span><span class="text-brand-500">$<span id="feeTotal">0</span></span></div>
        </div>
        <div class="mt-4">
          <h3 class="text-xs font-semibold uppercase tracking-wider text-muted mb-2">Wallet Balances</h3>
          <div id="walletList" class="space-y-1 text-xs"></div>
        </div>
      </div>

      <!-- Transaction History -->
      <div class="rounded-2xl bg-card border border-border p-5 fade-in">
        <h2 class="text-sm font-semibold uppercase tracking-wider text-muted mb-3">📜 Transaction History · Solana Sim</h2>
        <div class="overflow-x-auto">
          <table class="w-full text-xs">
            <thead><tr class="text-muted border-b border-border"><th class="text-left py-1 pr-2">TX Hash</th><th class="text-left py-1 pr-2">From</th><th class="text-left py-1 pr-2">To</th><th class="text-right py-1 pr-2">USDC</th><th class="text-left py-1">Memo</th></tr></thead>
            <tbody id="txBody"></tbody>
          </table>
        </div>
        <div class="mt-2 text-xs text-muted flex justify-between">
          <span>Slot: <span class="mono text-white" id="slotNum">0</span></span>
          <span>System: <span class="font-bold text-green-400">$<span id="sysBal">0</span></span></span>
        </div>
      </div>
    </div>
  </main>

  <footer class="text-center text-xs text-muted py-4 border-t border-border">
    TxLINE Parametric Escrow · Built for TxODDS Hackathon · July 2026
  </footer>

<script>
const API = '/api/status';
let lastExport = 0;

function fmtAddr(a){return a? a.slice(0,8)+'…'+a.slice(-4): '---'}
function fmtShort(a){return a? a.slice(0,6): '---'}
function statusColor(s){
  if(s==='LIVE')return 'bg-green-500/20 text-green-400 border border-green-500/30'
  if(s==='FINISHED')return 'bg-red-500/20 text-red-400 border border-red-500/30'
  return 'bg-slate-500/20 text-slate-400 border border-slate-500/30'
}

async function refresh(){
  try{
    const r=await fetch(API); if(!r.ok)return;
    const d=await r.json();
    if(d.export_count===lastExport)return; lastExport=d.export_count;

    // Match
    const ms=d.match_stats||{};
    document.getElementById('homeTeam').textContent=ms.home_team||'---';
    document.getElementById('awayTeam').textContent=ms.away_team||'---';
    document.getElementById('score').textContent=(ms.goals_home||0)+'-'+(ms.goals_away||0);
    const badge=document.getElementById('statusBadge');
    badge.textContent=ms.status||'UPCOMING';
    badge.className='px-3 py-1 rounded-full text-xs font-semibold uppercase tracking-wider '+statusColor(ms.status);
    document.getElementById('matchId').textContent=d.match_id||'---';
    document.getElementById('matchMinute').textContent=(ms.minute||0)+"'";
    document.getElementById('matchPhase').textContent=ms.phase||'---';
    document.getElementById('totalGoals').textContent='⚽ '+(ms.total_goals||0);
    const dot=document.getElementById('statusDot');
    dot.className='w-2 h-2 rounded-full '+(ms.status==='LIVE'?'bg-green-400 pulse':'bg-slate-600');

    // M1
    const m1=d.pool_m1||{};
    document.getElementById('m1Tickets').textContent=m1.total_tickets||0;
    document.getElementById('m1Stake').textContent=(m1.total_at_stake||0).toFixed(0);
    document.getElementById('m1Rollover').textContent=(m1.rollover_balance||0).toFixed(0);
    document.getElementById('m1Pda').textContent=fmtShort(m1.sim_pda||'');
    document.getElementById('m1VaultBal').textContent='Vault: $'+(m1.sim_vault_balance||0).toFixed(0);
    const wdiv=document.getElementById('m1Winners');
    if(m1.resolved){
      const w=m1.winning_goals; const wc=m1.tickets.filter(t=>t.won).length;
      wdiv.innerHTML='<span class='+(wc>0?'text-green-400':'text-red-400')+'>'+(wc>0?'🏆 '+wc+' winner(s) @ '+w+' goals':'😞 No winners — 50% house / 50% rollover')+'</span>';
    }else{wdiv.innerHTML='<span class="text-muted">⏳ Waiting for match resolution</span>'}

    // M2
    const m2=d.versus_m2||{};
    document.getElementById('m2Active').textContent=m2.active_duels||0;
    document.getElementById('m2Total').textContent=m2.total_duels||0;
    document.getElementById('m2Pda').textContent=fmtShort((m2.duels||[])[0]?.sim_pda||'');
    const dlist=document.getElementById('m2Duels');
    dlist.innerHTML=(m2.duels||[]).map(dd=>{
      const c=dd.creator||{}; const won=c.won?'🏆':'';
      const opps=(dd.opponents||[]).map(o=>fmtShort(o.player)+(o.won?' ✅':(dd.resolved?' ❌':''))).join(', ');
      return '<div class="bg-panel rounded-lg p-2 flex justify-between items-center text-xs"><div><span class="font-mono text-brand-500">'+dd.duel_id+'</span> <span class="text-muted">'+['draw','HOME','AWAY'][c.prediction||0]+'</span></div><div><span class="text-muted">$'+(dd.total_pool||0).toFixed(0)+'</span> '+won+'<span class="text-muted ml-1">vs</span> <span class="text-xs">'+opps+'</span></div></div>';
    }).join('');

    // Ledger
    const pl=d.platform_ledger||{};
    document.getElementById('houseAddr').textContent=fmtAddr(pl.house_wallet);
    document.getElementById('houseBal').textContent=(pl.total_balance_usdc||0).toFixed(0);
    const fc=pl.fees_collected||{};
    document.getElementById('feeM1').textContent=(fc.m1_rollover_desert_50pct||0).toFixed(2);
    document.getElementById('feeM2Creator').textContent=(fc.m2_creator_win_10pct||0).toFixed(2);
    document.getElementById('feeM2Draw').textContent=(fc.m2_draw_protection_20pct||0).toFixed(2);
    document.getElementById('feeTotal').textContent=(fc.total||0).toFixed(2);

    // Wallets
    const wb=d.wallet_balances||{};
    document.getElementById('walletList').innerHTML=Object.entries(wb).map(([name,w])=>{
      const cls=name.includes('platform')||name.includes('house')?'text-amber-300':name.includes('oracle')?'text-muted':'text-white';
      return '<div class="flex justify-between"><span class="'+cls+'">'+name+'</span><span class="mono">$'+w.balance.toFixed(0)+' <span class="text-muted">('+w.available.toFixed(0)+' avail)</span></span></div>';
    }).join('');

    // TX history
    const txs=d.transaction_history||[];
    document.getElementById('txBody').innerHTML=txs.slice(-8).reverse().map(tx=>
      '<tr class="tx-row border-b border-border/30"><td class="py-1 pr-2 mono text-brand-500">'+fmtShort(tx.tx_hash)+'…</td><td class="py-1 pr-2 mono">'+fmtShort(tx.source)+'</td><td class="py-1 pr-2 mono">'+fmtShort(tx.destination)+'</td><td class="py-1 pr-2 text-right mono">$'+tx.amount_usdc.toFixed(2)+'</td><td class="py-1 text-muted text-xs">'+tx.memo+'</td></tr>'
    ).join('');

    document.getElementById('slotNum').textContent=(d.solana_mock||{}).slot||0;
    document.getElementById('sysBal').textContent=((d.solana_mock||{}).system_balance||0).toFixed(0);

    // Clock
    document.getElementById('clock').textContent=new Date().toLocaleTimeString();
  }catch(e){console.error(e)}
}

setInterval(refresh,2000);
refresh();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template_string(TEMPLATE)


@app.route("/api/status")
def api_status():
    if not os.path.exists(DASHBOARD_PATH):
        return jsonify({"error": "dashboard.json not found — is main.py running?"}), 503
    with open(DASHBOARD_PATH) as f:
        return jsonify(json.load(f))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  🌐 TxLINE Dashboard → http://0.0.0.0:{port}")
    print(f"  📊 API endpoint    → http://0.0.0.0:{port}/api/status\n")
    app.run(host="0.0.0.0", port=port, debug=False)
