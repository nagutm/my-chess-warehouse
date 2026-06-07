/* Frontend dashboard: fetch /stats/summary, /stats/openings, /stats/ratings
   - API base is provided via window.__API_BASE__ (replace at build time)
   - Parallel fetch on load and when date controls change
   - Uses Chart.js (loaded from CDN in index.html)
*/

const API_BASE = (window && window.__API_BASE__) || "";

const $ = (sel) => document.querySelector(sel);
const $all = (sel) => Array.from(document.querySelectorAll(sel));

const loadingEl = $("#loading");
const errorEl = $("#error");
const refreshBtn = $("#refresh-btn");
const fromDateInput = $("#from-date");
const toDateInput = $("#to-date");

const ratingsCtx = document.getElementById("ratings-chart").getContext("2d");
const timecontrolCtx = document.getElementById("timecontrol-chart").getContext("2d");

let ratingsChart = null;
let timecontrolChart = null;

function setLoading(text = "Loading...") {
  loadingEl.textContent = text;
}

function setError(message) {
  if (!message) {
    errorEl.hidden = true;
    errorEl.textContent = "";
  } else {
    errorEl.hidden = false;
    errorEl.textContent = message;
  }
}

function buildUrl(path, params = {}) {
  const base = API_BASE || "";
  const url = new URL(path, base || window.location.origin);
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  });
  return url.toString();
}

async function fetchAll(fromIso, toIso) {
  setError("");
  setLoading("Loading stats...");

  const paths = [
    buildUrl("/stats/summary", { from: fromIso, to: toIso }),
    buildUrl("/stats/openings", { from: fromIso, to: toIso }),
    buildUrl("/stats/ratings", { from: fromIso, to: toIso }),
  ];

  try {
    const responses = await Promise.all(paths.map((p) => fetch(p)));
    const ok = responses.every((r) => r.ok);
    if (!ok) {
      const msg = `One or more requests failed (${responses.map((r) => r.status).join(",")})`;
      throw new Error(msg);
    }

    const [summary, openings, ratings] = await Promise.all(responses.map((r) => r.json()));
    setLoading("Rendering...");

    renderSummary(summary);
    renderOpenings(openings);
    renderRatings(ratings);

    setLoading("Done");
    setTimeout(() => setLoading("Idle"), 600);
  } catch (err) {
    console.error(err);
    setError(err.message || String(err));
    setLoading("Error");
  }
}

function renderSummary(summary) {
  // byColor: { white: {games,wins,losses,draws,winRate}, black: {...} }
  const container = $("#color-breakdown");
  container.innerHTML = "";
  const colors = [
    { key: "white", label: "White", class: "card-white" },
    { key: "black", label: "Black", class: "card-black" },
  ];
  colors.forEach((c) => {
    const data = summary.byColor && summary.byColor[c.key];
    const html = `
      <div class="stat-card ${c.class}">
        <div class="stat-label">${c.label}</div>
        <div class="stat-value">${data ? data.games : 0} games</div>
        <div class="stat-sub">W:${data ? data.wins : 0} L:${data ? data.losses : 0} D:${data ? data.draws : 0}</div>
        <div class="stat-rate">${data ? Math.round((data.winRate || 0) * 100) : 0}% win</div>
      </div>`;
    container.insertAdjacentHTML("beforeend", html);
  });

  // byTimeControl -> bar chart of winRate%
  const byTC = summary.byTimeControl || {};
  const labels = Object.keys(byTC);
  const winRates = labels.map((l) => Math.round((byTC[l].winRate || 0) * 100));
  const gamesCounts = labels.map((l) => byTC[l].games || 0);

  if (timecontrolChart) {
    timecontrolChart.data.labels = labels;
    timecontrolChart.data.datasets[0].data = winRates;
    timecontrolChart.update();
  } else {
    timecontrolChart = new Chart(timecontrolCtx, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Win Rate (%)",
            data: winRates,
            backgroundColor: labels.map(() => "#6366f1"),
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          y: { beginAtZero: true, max: 100, ticks: { callback: (v) => v + "%" } },
        },
      },
    });
  }
}

function renderOpenings(openingsResp) {
  // openingsResp: { openings: [ {openingName, games, winRate} ] }
  const rows = (openingsResp.openings || []).slice(0, 10);
  const tbody = $("#openings-table tbody");
  tbody.innerHTML = "";
  rows.forEach((r) => {
    const name = r.openingName || r.eco || "(unknown)";
    const games = r.games || 0;
    const win = Math.round((r.winRate || 0) * 100);
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${escapeHtml(name)}</td><td>${games}</td><td>${win}%</td>`;
    tbody.appendChild(tr);
  });
}

function renderRatings(ratingsResp) {
  // ratingsResp: { ratings: { speed: [ { lastMoveAt, rating }, ... ] } }
  const ratings = ratingsResp.ratings || {};
  const datasets = Object.keys(ratings).map((speed, idx) => ({
    label: speed,
    data: (ratings[speed] || []).map((p) => ({ x: new Date(p.lastMoveAt), y: p.rating })),
    borderColor: palette(idx),
    backgroundColor: palette(idx, 0.15),
    tension: 0.2,
    pointRadius: 1,
  }));

  if (ratingsChart) {
    ratingsChart.data.datasets = datasets;
    ratingsChart.update();
  } else {
    ratingsChart = new Chart(ratingsCtx, {
      type: "line",
      data: { datasets },
      options: {
        parsing: false,
        normalized: true,
        plugins: { legend: { position: "bottom" } },
        scales: {
          x: { type: "time", time: { tooltipFormat: "PP", unit: "day" } },
          y: { title: { display: true, text: "Rating" } },
        },
      },
    });
  }
}

function palette(i, alpha = 1) {
  const colors = ["#6366f1", "#ef4444", "#10b981", "#f59e0b", "#3b82f6", "#a78bfa"];
  const hex = colors[i % colors.length];
  if (alpha === 1) return hex;
  // apply alpha
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function currentIsoFromInput(inputEl) {
  const v = inputEl && inputEl.value;
  if (!v) return undefined;
  // return ISO date (YYYY-MM-DD) — backend accepts naive ISO and treats as UTC
  return v;
}

async function doRefresh() {
  const fromIso = currentIsoFromInput(fromDateInput);
  const toIso = currentIsoFromInput(toDateInput);
  await fetchAll(fromIso, toIso);
}

refreshBtn.addEventListener("click", (e) => {
  e.preventDefault();
  doRefresh();
});

// initial load
setLoading("Idle");
setError("");
document.addEventListener("DOMContentLoaded", () => {
  // If desired, set default date range (last 90 days)
  const today = new Date();
  const prior = new Date(today.getTime() - 90 * 24 * 3600 * 1000);
  if (!fromDateInput.value) fromDateInput.value = prior.toISOString().slice(0, 10);
  if (!toDateInput.value) toDateInput.value = today.toISOString().slice(0, 10);
  doRefresh();
});
