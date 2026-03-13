/* ── Config ────────────────────────────────────────────────────── */
const API_BASE    = '/api';
const WS_PATH     = '/ws';
const POLL_MS     = 30_000;   // fallback polling interval

/* ── State ─────────────────────────────────────────────────────── */
let ws            = null;
let pollTimer     = null;
let lastPayload   = null;

/* ── Entry ─────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  connectWebSocket();
});

/* ── WebSocket with polling fallback ───────────────────────────── */
function connectWebSocket() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  const url      = `${protocol}://${location.host}${WS_PATH}`;

  try {
    ws = new WebSocket(url);
  } catch {
    startPolling();
    return;
  }

  ws.onmessage = (evt) => {
    try {
      const data = JSON.parse(evt.data);
      render(data);
    } catch (e) {
      console.error('WS parse error', e);
    }
  };

  ws.onerror  = () => { ws = null; startPolling(); };
  ws.onclose  = () => { ws = null; startPolling(); };
}

function startPolling() {
  if (pollTimer) return;
  fetchAndRender();
  pollTimer = setInterval(fetchAndRender, POLL_MS);
}

async function fetchAndRender() {
  try {
    const resp = await fetch(`${API_BASE}/live-standings`);
    if (!resp.ok) throw new Error(resp.statusText);
    const data = await resp.json();
    render(data);
  } catch (err) {
    showError(err.message);
  }
}

/* ── Render ─────────────────────────────────────────────────────── */
function render(data) {
  lastPayload = data;

  const { standings = [], live_games = [], has_live = false, schedule = {}, logos = {}, updated_at } = data;

  // Header badges
  document.getElementById('live-badge').classList.toggle('hidden', !has_live);
  const updLabel = document.getElementById('updated-label');
  if (updated_at) {
    const d = new Date(updated_at * 1000);
    updLabel.textContent = `Uppdaterad ${d.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
  }

  // Live note under table title
  document.getElementById('live-note').classList.toggle('hidden', !has_live);

  // Live games panel
  renderLiveGames(live_games, has_live);

  // Upcoming round
  renderSchedule(schedule, logos);

  // Standings table
  renderStandings(standings, live_games, logos);
}

/* ── Live games panel ───────────────────────────────────────────── */
function renderLiveGames(liveGames, hasLive) {
  const section = document.getElementById('live-section');
  section.classList.toggle('hidden', !hasLive);
  if (!hasLive) return;

  const grid = document.getElementById('live-games');
  grid.innerHTML = '';

  for (const g of liveGames) {
    const homeLeads = g.home_score > g.away_score;
    const awayLeads = g.away_score > g.home_score;
    const tied      = g.home_score === g.away_score;

    const homeClass = homeLeads ? 'leading' : awayLeads ? 'trailing' : 'tied';
    const awayClass = awayLeads ? 'leading' : homeLeads ? 'trailing' : 'tied';

    const card = document.createElement('div');
    card.className = 'live-game-card';
    card.innerHTML = `
      <div class="teams">
        <div class="team-block">
          <span class="team-code">${esc(g.home_team_code)}</span>
          <span class="team-score ${homeClass}">${g.home_score}</span>
        </div>
        <span class="score-sep">–</span>
        <div class="team-block">
          <span class="team-score ${awayClass}">${g.away_score}</span>
          <span class="team-code">${esc(g.away_team_code)}</span>
        </div>
      </div>
      <div class="game-status">${esc(g.status)}</div>
      <div class="projected-pts">
        ${tied
          ? 'OT-prognos: +1p vardera'
          : `Prognos: ${esc(homeLeads ? g.home_team_code : g.away_team_code)} +3p`
        }
      </div>
    `;
    grid.appendChild(card);
  }
}

/* ── Upcoming round ─────────────────────────────────────────────── */
function renderSchedule(schedule, logos = {}) {
  const section = document.getElementById('schedule-section');
  const games   = schedule?.games ?? [];
  const roundDate = schedule?.round_date;

  if (!games.length) {
    section.classList.add('hidden');
    return;
  }
  section.classList.remove('hidden');

  // Format date as Swedish weekday + date
  const titleEl = document.getElementById('schedule-title');
  if (roundDate) {
    const d = new Date(roundDate + 'T12:00:00');
    const today = new Date();
    today.setHours(0,0,0,0);
    const roundDay = new Date(roundDate + 'T00:00:00');
    const isToday = roundDay.toDateString() === today.toDateString();
    const label = isToday
      ? 'Dagens matcher'
      : `Omgång ${d.toLocaleDateString('sv-SE', { weekday: 'long', month: 'short', day: 'numeric' })}`;
    titleEl.textContent = label;
  }

  const grid = document.getElementById('schedule-games');
  grid.innerHTML = '';

  for (const g of games) {
    const card = document.createElement('div');
    card.className = 'schedule-game-card' + (g.played ? ' played' : '');
    const homeLogo = logos[g.home_code] ? `<img class="sched-logo" src="${esc(logos[g.home_code])}" alt="${esc(g.home_code)}" onerror="this.style.display='none'">` : '';
    const awayLogo = logos[g.away_code] ? `<img class="sched-logo" src="${esc(logos[g.away_code])}" alt="${esc(g.away_code)}" onerror="this.style.display='none'">` : '';
    card.innerHTML = `
      <div class="sched-teams">
        <div class="sched-team-block">
          ${homeLogo}
          <span class="sched-code">${esc(g.home_code)}</span>
        </div>
        <div class="sched-middle">
          ${g.played
            ? `<span class="sched-result">${esc(g.result)}</span>`
            : `<span class="sched-time">${esc(g.time)}</span>`
          }
        </div>
        <div class="sched-team-block">
          <span class="sched-code">${esc(g.away_code)}</span>
          ${awayLogo}
        </div>
      </div>
    `;
    grid.appendChild(card);
  }
}

/* ── Standings table ────────────────────────────────────────────── */
function renderStandings(standings, liveGames, logos = {}) {
  const liveCodes = new Set();
  const livePts   = {};
  for (const g of liveGames) {
    liveCodes.add(g.home_team_code);
    liveCodes.add(g.away_team_code);
    livePts[g.home_team_code] = (livePts[g.home_team_code] || 0) + g.home_projected_pts;
    livePts[g.away_team_code] = (livePts[g.away_team_code] || 0) + g.away_projected_pts;
  }

  const tbody = document.getElementById('standings-body');
  tbody.innerHTML = '';

  if (!standings.length) {
    tbody.innerHTML = '<tr class="loading-row"><td colspan="11">Ingen data tillgänglig</td></tr>';
    return;
  }

  // Zone boundaries (after these ranks insert a separator)
  // 1-6: Direktkval  |  7-10: Play-in  |  11-12: Ingenmansland  |  13-14: Kvalspel
  const ZONE_SEPARATORS = {
    6:  { label: 'Play-in',         cls: 'zone-sep-playin' },
    10: { label: 'Ingenmansland',   cls: 'zone-sep-mid'    },
    12: { label: 'Kvalspel',        cls: 'zone-sep-kval'   },
  };
  const ZONE_CLASS = (rank) => {
    if (rank <= 6)  return 'zone-kval-direct';
    if (rank <= 10) return 'zone-playin';
    if (rank <= 12) return 'zone-mid';
    return 'zone-kval';
  };

  standings.forEach((entry, idx) => {
    const rank = entry.rank ?? (idx + 1);

    // Insert separator BEFORE this row if previous rank had a boundary
    const sep = ZONE_SEPARATORS[rank - 1];
    if (sep) {
      const sepTr = document.createElement('tr');
      sepTr.className = `zone-separator ${sep.cls}`;
      sepTr.innerHTML = `<td colspan="11"><span class="zone-sep-label">${sep.label}</span></td>`;
      tbody.appendChild(sepTr);
    }

    const team       = entry.team || {};
    const code       = team.code || team.teamCode || entry.teamCode || '';
    const name       = team.name || code;
    const isLive     = liveCodes.has(code);
    const deltaRaw   = livePts[code] || 0;
    const deltaStr   = deltaRaw > 0 ? `<span class="pts-live-delta">+${deltaRaw}</span>` : '';

    const gp   = entry.GP ?? entry.gamesPlayed ?? '–';
    const w    = entry.W ?? entry.wins ?? '–';
    const otw  = entry.OTW ?? entry.overtimeWins ?? '–';
    const otl  = entry.OTL ?? entry.overtimeLosses ?? '–';
    const l    = entry.L ?? entry.losses ?? '–';
    const gf   = entry.GF ?? entry.goalsFor ?? '–';
    const ga   = entry.GA ?? entry.goalsAgainst ?? '–';
    const diff = entry.Diff ?? (typeof gf === 'number' && typeof ga === 'number' ? gf - ga : '–');
    const pts  = entry.Points ?? entry.points ?? '–';

    const diffStr = typeof diff === 'number'
      ? `<span class="${diff > 0 ? 'diff-pos' : diff < 0 ? 'diff-neg' : ''}">${diff > 0 ? '+' : ''}${diff}</span>`
      : diff;

    const logoUrl = logos[code];
    const logoHtml = logoUrl
      ? `<img class="team-logo" src="${esc(logoUrl)}" alt="${esc(code)}" onerror="this.style.display='none'">`
      : `<span class="team-logo-fallback">${esc(code.slice(0,3))}</span>`;

    const tr = document.createElement('tr');
    tr.className = ZONE_CLASS(rank);
    if (isLive) tr.classList.add('is-live');

    tr.innerHTML = `
      <td class="col-rank">
        <span class="zone-dot ${ZONE_CLASS(rank)}-dot"></span>${rank}
      </td>
      <td class="col-team">
        ${isLive ? '<span class="live-indicator"></span>' : ''}
        ${logoHtml}
        <span class="team-full-name">${esc(name)}</span>
      </td>
      <td class="col-num">${gp}</td>
      <td class="col-num">${w}</td>
      <td class="col-num hide-sm">${otw}</td>
      <td class="col-num hide-sm">${otl}</td>
      <td class="col-num">${l}</td>
      <td class="col-num hide-md">${gf}</td>
      <td class="col-num hide-md">${ga}</td>
      <td class="col-num hide-sm">${diffStr}</td>
      <td class="col-pts">${pts}${deltaStr}</td>
    `;
    tbody.appendChild(tr);
  });
}

/* ── Error state ────────────────────────────────────────────────── */
function showError(msg) {
  document.getElementById('standings-body').innerHTML =
    `<tr class="error-row"><td colspan="11">Fel: ${esc(msg)}</td></tr>`;
}

/* ── Helper ─────────────────────────────────────────────────────── */
function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
