let _token = localStorage.getItem('demo_token') || '';

async function ensureAuth() {
  if (_token) return true;
  return new Promise((resolve) => {
    const overlay = document.getElementById('login-overlay');
    overlay.style.display = 'flex';
    const form = document.getElementById('login-form');
    const errEl = document.getElementById('login-error');
    const btn = document.getElementById('login-submit-btn');
    const btnDefaultHtml = btn.innerHTML;
    form.onsubmit = async (e) => {
      e.preventDefault();
      errEl.textContent = '';
      const pw = document.getElementById('login-password').value;
      if (!pw) return;
      btn.disabled = true;
      btn.innerHTML = '<span class="ai-spinner"></span> Authenticating…';
      try {
        const body = new URLSearchParams({ username: 'admin', password: pw });
        const res = await fetch('/auth/login', { method: 'POST', body });
        if (!res.ok) {
          errEl.textContent = 'Wrong password';
          btn.disabled = false;
          btn.innerHTML = btnDefaultHtml;
          return;
        }
        const { access_token } = await res.json();
        _token = access_token;
        localStorage.setItem('demo_token', _token);
        overlay.style.display = 'none';
        resolve(true);
      } catch {
        errEl.textContent = 'Connection error';
        btn.disabled = false;
        btn.innerHTML = btnDefaultHtml;
      }
    };
  });
}

const MAX_POINTS = 120;
const taurusLatency = [];
const maasLatency = [];
const taurusQps = [];

let ws = null;
let wsDisconnectNotified = false;
let taurusChart, maasChart, latencyChart;
let chatHistory = [];
let chatChartInstance = null;

function initCharts() {
  const common = {
    chart: {
      type: 'area',
      height: 190,
      animations: { enabled: true, speed: 400, dynamicAnimation: { enabled: true, speed: 400 } },
      background: 'transparent',
      toolbar: { show: false },
      sparkline: { enabled: false },
    },
    xaxis: {
      type: 'datetime',
      range: MAX_POINTS * 1000,
      labels: { show: false },
      axisTicks: { show: false },
      axisBorder: { show: false },
    },
    yaxis: { labels: { style: { colors: '#6b7280', fontSize: '11px' } }, tickAmount: 3 },
    grid: { borderColor: 'rgba(255,255,255,0.04)', strokeDashArray: 4 },
    stroke: { width: 2, curve: 'smooth' },
    fill: {
      type: 'gradient',
      gradient: { shadeIntensity: 1, opacityFrom: 0.25, opacityTo: 0.02, stops: [0, 90, 100] },
    },
    dataLabels: { enabled: false },
    tooltip: { theme: 'dark' },
    colors: ['#818cf8'],
  };

  taurusChart = new ApexCharts(document.getElementById('taurus-chart'), {
    ...common,
    series: [{ name: 'QPS', data: [] }],
    yaxis: { ...common.yaxis, title: { text: 'QPS', style: { color: '#6b7280', fontSize: '11px' } } },
  });
  taurusChart.render();

  maasChart = new ApexCharts(document.getElementById('maas-chart'), {
    ...common,
    series: [{ name: 'Latency', data: [] }],
    colors: ['#22d3ee'],
    fill: { ...common.fill, gradient: { shadeIntensity: 1, opacityFrom: 0.2, opacityTo: 0.01, stops: [0, 90, 100] } },
    yaxis: { ...common.yaxis, title: { text: 'ms', style: { color: '#6b7280', fontSize: '11px' } } },
  });
  maasChart.render();

  latencyChart = new ApexCharts(document.getElementById('latency-chart'), {
    ...common,
    chart: { ...common.chart, height: 240 },
    series: [
      { name: 'TaurusDB', data: [] },
      { name: 'MaaS AI', data: [] },
    ],
    colors: ['#818cf8', '#22d3ee'],
    fill: {
      type: 'gradient',
      gradient: { shadeIntensity: 1, opacityFrom: 0.2, opacityTo: 0.01, stops: [0, 90, 100] },
    },
    yaxis: { ...common.yaxis, title: { text: 'Latency (ms)', style: { color: '#6b7280', fontSize: '11px' } } },
    legend: { show: true, position: 'top', labels: { colors: '#6b7280' } },
  });
  latencyChart.render();
}

function push(arr, x, val) {
  arr.push({ x, y: val });
  if (arr.length > MAX_POINTS) arr.shift();
}

function updateDashboard(data) {
  const t = data.taurus || {};
  const m = data.maas || {};
  const s = data.scenario || {};

  document.getElementById('db-dot').className = 'status-dot ' + (t.available ? 'ok' : 'err');
  document.getElementById('maas-dot').className = 'status-dot ' + (m.available ? 'ok' : 'err');

  document.getElementById('taurus-qps').textContent = t.qps || 0;
  document.getElementById('taurus-latency').textContent = (t.latency_ms || 0).toFixed(1);
  document.getElementById('taurus-conn').textContent = t.connected || 0;

  document.getElementById('maas-latency').textContent = (m.latency_ms || 0).toFixed(1);
  document.getElementById('maas-model').textContent = m.model || '—';
  document.getElementById('maas-err').textContent = t.errors || 0;

  document.getElementById('scenario-badge').textContent = s.state || 'idle';
  document.getElementById('scenario-msg').textContent = s.message || 'Ready';
  document.getElementById('progress-fill').style.width = (s.progress || 0) + '%';
  setActsBusy(!!s.state && s.state !== 'idle', s.state);

  if (data.commentary) {
    document.getElementById('ticker-text').textContent = data.commentary;
  }

  const now = Date.now();
  push(taurusLatency, now, t.latency_ms || 0);
  push(maasLatency, now, m.latency_ms || 0);
  push(taurusQps, now, t.qps || 0);

  taurusChart.updateSeries([{ data: [...taurusQps] }]);
  maasChart.updateSeries([{ data: [...maasLatency] }]);
  latencyChart.updateSeries([
    { data: [...taurusLatency] },
    { data: [...maasLatency] },
  ]);
}

const ACT_STATE_TILE = {
  loading: 'act-1',
  failing_over: 'act-2',
  ai_analyzing: 'act-3',
};

function setActsBusy(busy, activeState) {
  document.querySelectorAll('.scenario-btn').forEach((btn) => {
    btn.disabled = busy;
  });
  document.querySelectorAll('.act-tile').forEach((tile) => tile.classList.remove('active'));
  if (busy) {
    const activeClass = ACT_STATE_TILE[activeState];
    if (activeClass) {
      const tile = document.querySelector('.' + activeClass);
      if (tile) tile.classList.add('active');
    }
  }
}

function showError(msg) {
  const el = document.getElementById('error-banner');
  if (!el) return;
  el.textContent = msg;
  el.style.display = 'block';
  clearTimeout(el._hideTimer);
  el._hideTimer = setTimeout(() => { el.style.display = 'none'; }, 4000);
}

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = _token
    ? `${proto}://${location.host}/ws?token=${_token}`
    : `${proto}://${location.host}/ws`;
  ws = new WebSocket(url);
  ws.onopen = () => { wsDisconnectNotified = false; };
  ws.onmessage = (e) => {
    try {
      updateDashboard(JSON.parse(e.data));
    } catch {}
  };
  ws.onclose = () => {
    if (!wsDisconnectNotified) {
      showError('Live connection lost — reconnecting…');
      wsDisconnectNotified = true;
    }
    setTimeout(connectWS, 2000);
  };
  ws.onerror = () => ws.close();
}

async function api(path, method = 'POST', body = null) {
  if (!await ensureAuth()) return null;
  try {
    const opts = {
      method,
      headers: { 'Authorization': `Bearer ${_token}` },
    };
    if (body) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (res.status === 401) {
      _token = '';
      localStorage.removeItem('demo_token');
      if (!await ensureAuth()) return null;
      return api(path, method, body);
    }
    return await res.json();
  } catch (e) {
    console.error(e);
    return null;
  }
}

function startLoad() { api('/scenario/start-load'); }
function killPrimary() { api('/scenario/kill-primary'); }
async function aiAnalyze() {
  const res = await api('/scenario/ai-analyze');
  if (res && res.analysis) {
    document.getElementById('ai-result').innerHTML = renderMarkdown(res.analysis);
  } else if (res && res.error) {
    document.getElementById('ai-result').textContent = 'Error: ' + res.error;
  }
}
function resetScenario() { api('/scenario/reset'); setActsBusy(false); }

async function loadTransactions() {
  try {
    const res = await fetch('/transactions?limit=20');
    const rows = await res.json();
    const tbody = document.getElementById('tx-body');
    tbody.innerHTML = rows.map(r => `
      <tr>
        <td>${r.id}</td>
        <td>${r.account_id}</td>
        <td>${parseFloat(r.amount).toFixed(2)}</td>
        <td>${r.tx_type}</td>
        <td class="${r.is_flagged ? 'flagged' : ''}">${r.is_flagged ? 'FLAGGED' : 'ok'}</td>
      </tr>
    `).join('');
  } catch { showError('Failed to load transactions'); }
}

async function injectFraud(pattern) {
  const res = await api(`/fraud/inject/${pattern}`);
  if (res) {
    loadFraudAlerts();
  }
}

async function runFraudAnalysis() {
  const res = await api('/fraud/analyze');
  if (res && res.summary) {
    document.getElementById('ai-result').innerHTML = renderMarkdown(res.summary);
  }
  loadFraudAlerts();
}

async function loadFraudAlerts() {
  try {
    const res = await fetch('/fraud/alerts?limit=20');
    const alerts = await res.json();
    const feed = document.getElementById('alerts-feed');
    if (!alerts.length || alerts.error) {
      feed.innerHTML = '<div class="alerts-empty">No alerts yet. Inject fraud patterns to trigger detection.</div>';
      return;
    }
    feed.innerHTML = alerts.map(a => {
      const typeLabel = a.alert_type ? a.alert_type.toUpperCase().replace('_', ' ') : 'UNKNOWN';
      const conf = a.confidence ? Math.round(a.confidence * 100) : 0;
      const timeAgo = a.detected_at ? timeSince(new Date(a.detected_at)) : '';
      return `
        <div class="alert-card">
          <div class="alert-header">
            <span class="alert-type">${typeLabel}</span>
            <span class="alert-conf">${conf}% confidence</span>
          </div>
          <div class="alert-body">
            Account #${a.account_id || '?'} &middot; Txn #${a.transaction_id || '?'}
          </div>
          ${a.reasoning ? `<div class="alert-reason">${escapeHtml(a.reasoning)}</div>` : ''}
          <div class="alert-time">${timeAgo}</div>
        </div>
      `;
    }).join('');
  } catch { showError('Failed to load fraud alerts'); }
}

function timeSince(date) {
  const seconds = Math.floor((new Date() - date) / 1000);
  if (seconds < 60) return seconds + 's ago';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + ' min ago';
  const hours = Math.floor(minutes / 60);
  return hours + 'h ago';
}

function showAiLoading() {
  const historyEl = document.getElementById('chat-history');
  historyEl.innerHTML += '<div class="chat-msg ai ai-loading" id="ai-loading"><div class="ai-spinner"></div> Thinking...</div>';
  historyEl.scrollTop = historyEl.scrollHeight;
}

function hideAiLoading() {
  const el = document.getElementById('ai-loading');
  if (el) el.remove();
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';

  const historyEl = document.getElementById('chat-history');
  historyEl.innerHTML += `<div class="chat-msg user"><strong>You:</strong> ${escapeHtml(message)}</div>`;

  chatHistory.push({ role: 'user', content: message });

  showAiLoading();

  const res = await api('/ai/chat', 'POST', { message, history: chatHistory });

  hideAiLoading();

  if (!res) {
    historyEl.innerHTML += `<div class="chat-msg ai"><strong>AI:</strong> Error communicating with AI</div>`;
    return;
  }

  chatHistory.push({ role: 'assistant', content: res.text || '' });

  let aiHtml = `<div class="chat-msg ai"><strong>AI:</strong> ${renderMarkdown(res.text || '')}</div>`;
  if (res.sql) {
    aiHtml += `<div class="chat-sql"><details><summary>SQL Query</summary><code>${escapeHtml(res.sql)}</code></details></div>`;
  }
  historyEl.innerHTML += aiHtml;

  if (res.chart) {
    const chartId = 'chat-chart-' + Date.now();
    historyEl.innerHTML += `<div class="chat-chart" id="${chartId}"></div>`;
    renderChatChart(chartId, res.chart);
  }

  historyEl.scrollTop = historyEl.scrollHeight;
}

function renderMarkdown(text) {
  let html = escapeHtml(text);
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
  html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');
  html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
  html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
  html = html.replace(/^(\d+)\. (.+)$/gm, '<li>$2</li>');
  html = html.replace(/(?:<li>.*?<\/li>\s*)+/gs, (match) => '<ul>' + match + '</ul>');
  html = html.replace(/<\/ul>\s*<ul>/g, '');
  html = html.replace(/\n{2,}/g, '</p><p>');
  html = html.replace(/\n/g, '<br>');
  html = '<p>' + html + '</p>';
  html = html.replace(/<p>\s*<\/p>/g, '');
  return html;
}

function renderChatChart(containerId, chart) {
  const el = document.getElementById(containerId);
  if (!el) return;

  const type = chart.type || 'bar';
  const opts = {
    chart: { type, height: 250, background: 'transparent' },
    title: { text: chart.title || '', style: { color: '#8b8fa3', fontSize: '14px' } },
    xaxis: { categories: chart.categories || [] },
    series: chart.series || [],
    colors: type === 'line' ? ['#6c5ce7'] : ['#6c5ce7', '#00cec9', '#ff6b6b', '#fdcb6e', '#00b894'],
    grid: { borderColor: '#2a2d3a', strokeDashArray: 3 },
    tooltip: { theme: 'dark' },
    theme: { mode: 'dark' },
    plotOptions: {
      bar: { borderRadius: 4 },
      pie: { donut: { labels: { show: true } } },
    },
    legend: { labels: { colors: '#8b8fa3' } },
  };

  if (type === 'table') {
    el.innerHTML = renderTable(chart);
    return;
  }

  const instance = new ApexCharts(el, opts);
  instance.render();
}

function renderTable(chart) {
  if (!chart.series || !chart.series.length) return '';
  const headers = chart.categories || [];
  let html = '<div class="table-wrap"><table><thead><tr>';
  headers.forEach(h => html += `<th>${escapeHtml(String(h))}</th>`);
  html += '</tr></thead><tbody>';
  chart.series[0].data.forEach((row, i) => {
    html += '<tr>';
    if (Array.isArray(row)) {
      row.forEach(cell => html += `<td>${escapeHtml(String(cell))}</td>`);
    } else {
      html += `<td>${escapeHtml(String(headers[i] || ''))}</td><td>${escapeHtml(String(row))}</td>`;
    }
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  return html;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

async function generateReport() {
  const modal = document.getElementById('report-modal');
  const body = document.getElementById('report-body');
  modal.style.display = 'flex';
  body.innerHTML = '<div class="report-loading">Generating report...</div>';

  const res = await api('/ai/report', 'GET');
  if (!res || res.error) {
    body.innerHTML = '<div class="report-loading">Error generating report</div>';
    return;
  }

  let html = `<div class="report-ts">Generated: ${new Date(res.generated_at * 1000).toLocaleString()}</div>`;
  if (res.sections) {
    res.sections.forEach(s => {
      html += `<div class="report-section"><h3>${s.title}</h3><p>${renderMarkdown(s.content)}</p></div>`;
    });
  }
  body.innerHTML = html;
}

function closeReportModal() {
  document.getElementById('report-modal').style.display = 'none';
}

async function fetchCommentary() {
  try {
    const res = await fetch('/ai/commentary');
    const data = await res.json();
    if (data.text) {
      document.getElementById('ticker-text').textContent = data.text;
    }
  } catch {}
}

ensureAuth().then(() => {
  initCharts();
  connectWS();
  setInterval(loadTransactions, 5000);
  setInterval(loadFraudAlerts, 5000);
  setInterval(fetchCommentary, 30000);
  loadTransactions();
  loadFraudAlerts();
  setTimeout(fetchCommentary, 2000);
});
