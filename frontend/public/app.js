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

  // Upcoming round (with live scores merged in)
  renderSchedule(schedule, logos, live_games);

  // Standings table
  renderStandings(standings, live_games, logos);
}

/* ── Upcoming round (with live scores merged in) ────────────────── */
function renderSchedule(schedule, logos = {}, liveGames = []) {
  // Build lookup: "HOMECODE-AWAYCODE" → live game object
  const liveMap = {};
  for (const g of liveGames) {
    liveMap[`${g.home_team_code}-${g.away_team_code}`] = g;
  }
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
    const live = liveMap[`${g.home_code}-${g.away_code}`] || null;
    const homeLogo = logos[g.home_code] ? `<img class="sched-logo" src="${esc(logos[g.home_code])}" alt="${esc(g.home_code)}" onerror="this.style.display='none'">` : '';
    const awayLogo = logos[g.away_code] ? `<img class="sched-logo" src="${esc(logos[g.away_code])}" alt="${esc(g.away_code)}" onerror="this.style.display='none'">` : '';

    let middleHtml;
    if (live) {
      middleHtml = `
        <span class="sched-live-score">${live.home_score}–${live.away_score}</span>
        <span class="sched-live-status"><span class="sched-live-dot"></span>${esc(live.status)}</span>
      `;
    } else if (g.played) {
      middleHtml = `<span class="sched-result">${esc(g.result)}</span>`;
    } else {
      middleHtml = `<span class="sched-time">${esc(g.time)}</span>`;
    }

    const card = document.createElement('div');
    card.className = 'schedule-game-card' + (live ? ' live' : g.played ? ' played' : '');
    card.innerHTML = `
      <div class="sched-teams">
        <div class="sched-team-block">
          ${homeLogo}
          <span class="sched-code">${esc(g.home_code)}</span>
        </div>
        <div class="sched-middle">${middleHtml}</div>
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

  const ZONE_CLASS = (rank) => {
    if (rank <= 6)  return 'zone-kval-direct';
    if (rank <= 10) return 'zone-playin';
    if (rank <= 12) return 'zone-mid';
    return 'zone-kval';
  };

  standings.forEach((entry, idx) => {
    const rank = entry.rank ?? (idx + 1);

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
        <div class="rank-inner"><span class="zone-dot ${ZONE_CLASS(rank)}-dot"></span>${rank}</div>
      </td>
      <td class="col-team">
        <div class="team-inner">
          ${isLive ? '<span class="live-indicator"></span>' : ''}
          ${logoHtml}
          <span class="team-full-name">${esc(name)}</span>
        </div>
      </td>
      <td class="col-num">${gp}</td>
      <td class="col-num hide-sm">${w}</td>
      <td class="col-num hide-sm">${otw}</td>
      <td class="col-num hide-sm">${otl}</td>
      <td class="col-num hide-sm">${l}</td>
      <td class="col-num hide-md show-xs">${gf}</td>
      <td class="col-num hide-md show-xs">${ga}</td>
      <td class="col-num">${diffStr}</td>
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
