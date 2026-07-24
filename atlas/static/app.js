(() => {
  const state = { users: [], active: 'gokul', graph: null };
  const examples = [
    'average salary by department',
    "show me riders' phone numbers",
    'average riders from Airport to Downtown',
    'trips per day',
    'trips by status',
  ];
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  const api = async (path, options) => {
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'The service did not complete that request.');
    return data;
  };

  function activeUser() { return state.users.find((user) => user.user === state.active) || { user: state.active, team: 'unknown' }; }
  function renderUsers() {
    const current = activeUser();
    $('active-name').textContent = current.user;
    $('active-team').textContent = current.team;
    $('active-avatar').textContent = current.user[0] || '?';
    $('user-list').innerHTML = state.users.map((user) => `<button class="user-option ${user.user === state.active ? 'selected' : ''}" role="option" aria-selected="${user.user === state.active}" data-user="${escapeHtml(user.user)}"><span class="avatar">${escapeHtml(user.user[0])}</span><span><strong>${escapeHtml(user.user)}</strong><small>${escapeHtml(user.team)}</small></span></button>`).join('');
    document.querySelectorAll('.user-option').forEach((button) => button.addEventListener('click', () => switchUser(button.dataset.user)));
  }
  async function switchUser(user) {
    state.active = user;
    $('user-list').classList.remove('open'); $('active-user').setAttribute('aria-expanded', 'false');
    $('answers').replaceChildren(); $('welcome').classList.toggle('hidden', user === 'auditor');
    renderUsers(); await loadGraph();
    const auditMode = user === 'auditor';
    $('chat-view').classList.toggle('hidden', auditMode); $('audit-view').classList.toggle('hidden', !auditMode);
    $('workspace-title').textContent = auditMode ? 'Audit console' : 'Ask your governed data';
    if (auditMode) loadAudit(); else $('question').focus();
  }
  async function loadGraph() {
    const graph = $('graph'); graph.innerHTML = '<div class="loading">Loading scoped map…</div>';
    try { state.graph = await api(`/api/graph/${encodeURIComponent(state.active)}`); renderGraph(state.graph); }
    catch (error) { graph.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`; $('graph-caption').textContent = 'Map unavailable.'; }
  }
  function renderGraph(graph) {
    const nodes = graph.nodes || []; const edges = graph.edges || [];
    $('graph-caption').textContent = nodes.length ? `${nodes.length} permitted tables · ${edges.length} observed joins` : 'No warehouse tables are exposed to this role.';
    if (!nodes.length) { $('graph').innerHTML = '<div class="empty">Audit role<br>has no queryable tables.</div>'; return; }
    const width = 250, height = 250, cx = 125, cy = 125;
    const degree = Object.fromEntries(nodes.map((node) => [node.id, 0])); edges.forEach((edge) => { degree[edge.from] = (degree[edge.from] || 0) + 1; degree[edge.to] = (degree[edge.to] || 0) + 1; });
    const centre = [...nodes].sort((a, b) => degree[b.id] - degree[a.id] || a.id.localeCompare(b.id))[0];
    const others = nodes.filter((node) => node.id !== centre.id);
    const positions = { [centre.id]: { x: cx, y: cy } };
    others.forEach((node, index) => { const angle = -Math.PI / 2 + index * (Math.PI * 2 / others.length); positions[node.id] = { x: cx + Math.cos(angle) * 82, y: cy + Math.sin(angle) * 82 }; });
    const line = (edge) => { const a = positions[edge.from], b = positions[edge.to], mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2; return `<line class="graph-edge" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/><text class="graph-label" x="${mx}" y="${my - 3}">${escapeHtml(edge.label)}</text>`; };
    const box = (node) => { const p = positions[node.id]; return `<g><rect class="graph-node" x="${p.x - 42}" y="${p.y - 16}" rx="5" width="84" height="32"/><text class="graph-node-text" x="${p.x}" y="${p.y - 2}">${escapeHtml(node.label)}</text><text class="graph-col-text" x="${p.x}" y="${p.y + 9}">${node.columns} columns</text></g>`; };
    $('graph').innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Scoped data join graph">${edges.map(line).join('')}${nodes.map(box).join('')}</svg>`;
  }
  function formatValue(value) { if (value === '***MASKED***') return '<span class="masked">***MASKED***</span>'; if (value === null || value === undefined) return '—'; return escapeHtml(value); }
  function tableChart(answer) { const headers = answer.columns.map((column) => `<th>${escapeHtml(column)}</th>`).join(''); const rows = answer.rows.map((row) => `<tr>${row.map((value) => `<td>${formatValue(value)}</td>`).join('')}</tr>`).join(''); return `<div class="data-table-wrap"><table class="data-table"><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table></div>`; }
  function chart(answer) {
    const spec = answer.chart || { mark: 'table' }; if (!answer.rows.length || spec.mark === 'table') return tableChart(answer);
    if (spec.mark === 'value') return `<div class="value-chart"><div class="value-number">${formatValue(answer.rows[0][0])}</div><div class="chart-title">${escapeHtml(spec.y || answer.columns[0] || 'Result')}</div></div>`;
    const points = answer.rows.map((row) => ({ label: String(row[0]), value: Number(row[1]) })).filter((point) => Number.isFinite(point.value));
    if (!points.length) return tableChart(answer);
    if (spec.mark === 'bar') return barChart(points);
    if (spec.mark === 'line') return lineChart(points);
    return tableChart(answer);
  }
  function barChart(points) {
    const w = 700, h = Math.max(130, points.length * 31 + 30), max = Math.max(...points.map((point) => point.value), 1), left = 145;
    return `<div class="chart-wrap"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Bar chart">${points.map((point, index) => { const y = 12 + index * 31, width = (point.value / max) * (w - left - 70); return `<text class="chart-text" x="${left - 8}" y="${y + 15}" text-anchor="end">${escapeHtml(point.label)}</text><rect class="chart-bar" x="${left}" y="${y}" width="${width}" height="20" rx="3"/><text class="chart-text" x="${left + width + 7}" y="${y + 14}">${escapeHtml(point.value.toFixed(point.value % 1 ? 2 : 0))}</text>`; }).join('')}</svg></div>`;
  }
  function lineChart(points) {
    const w = 700, h = 230, pad = { l: 45, r: 18, t: 18, b: 40 }, max = Math.max(...points.map((point) => point.value), 1), min = Math.min(...points.map((point) => point.value), 0);
    const x = (i) => pad.l + (i * (w - pad.l - pad.r) / Math.max(points.length - 1, 1)); const y = (v) => pad.t + ((max - v) * (h - pad.t - pad.b) / Math.max(max - min, 1));
    const path = points.map((point, index) => `${index ? 'L' : 'M'}${x(index).toFixed(1)},${y(point.value).toFixed(1)}`).join(' '); const labels = [0, Math.floor((points.length - 1) / 2), points.length - 1].filter((v, i, a) => a.indexOf(v) === i);
    return `<div class="chart-wrap"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Line chart"><line class="chart-axis" x1="${pad.l}" y1="${h - pad.b}" x2="${w - pad.r}" y2="${h - pad.b}"/><line class="chart-axis" x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${h - pad.b}"/><path class="chart-line" d="${path}"/>${points.map((point, index) => `<circle class="chart-dot" cx="${x(index)}" cy="${y(point.value)}" r="2.5"/>`).join('')}${labels.map((i) => `<text class="chart-text" x="${x(i)}" y="${h - 16}" text-anchor="middle">${escapeHtml(points[i].label)}</text>`).join('')}<text class="chart-text" x="${pad.l - 7}" y="${pad.t + 4}" text-anchor="end">${escapeHtml(max)}</text></svg></div>`;
  }
  function renderAnswer(answer) {
    const decision = answer.decision === 'deny' ? 'BLOCKED' : answer.decision === 'mask' ? 'MASKED' : 'ALLOW';
    const latency = answer.latency_ms || {}; const breakdown = Object.entries(latency).filter(([key]) => key !== 'total').map(([key, value]) => `${key}: ${value}ms`).join(' · ');
    const sql = answer.sql || 'No SQL was executed because this request was blocked by policy.';
    const card = document.createElement('article'); card.className = `answer-card ${answer.decision === 'deny' ? 'blocked' : ''}`;
    card.innerHTML = `<div class="answer-head"><span class="badge ${answer.decision}"><span>●</span>${decision}</span><span class="audit-id">audit ${escapeHtml((answer.audit_id || '').slice(0, 8))}</span></div><p class="reason">${escapeHtml(answer.reason)}</p>${answer.rows.length ? `<div class="result"><div class="chart-title">${escapeHtml((answer.chart || {}).title || 'Approved result')}</div>${chart(answer)}</div>` : ''}<div class="answer-foot"><details class="sql-details"><summary>SQL that actually ran</summary><pre>${escapeHtml(sql)}</pre></details><span class="latency" data-detail="${escapeHtml(breakdown || 'Timing unavailable')}">${escapeHtml(latency.total ?? 0)} ms</span></div>`;
    $('answers').prepend(card); $('welcome').classList.add('hidden');
  }
  async function submitQuestion(question) {
    if (!question || state.active === 'auditor') return;
    const button = $('send-button'); button.disabled = true; $('question').value = '';
    const row = document.createElement('div'); row.className = 'question-row'; row.textContent = `You · ${question}`; $('answers').prepend(row);
    try { renderAnswer(await api('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user: state.active, question }) })); }
    catch (error) { renderAnswer({ decision: 'deny', reason: error.message, rows: [], latency_ms: { total: 0 }, audit_id: '' }); }
    finally { button.disabled = false; $('question').focus(); }
  }
  async function loadAudit() {
    const root = $('audit-view'); root.innerHTML = '<div class="loading">Verifying the hash chain…</div>';
    try {
      const audit = await api('/api/audit'); const entries = audit.entries || [];
      root.innerHTML = `<div class="audit-banner ${audit.chain_ok ? '' : 'bad'}"><span>${audit.chain_ok ? '✓' : '!'}</span><span><strong>${audit.chain_ok ? `Hash chain verified — ${entries.length} entries` : 'Tampering detected'}</strong><small>${escapeHtml(audit.chain_message)}</small></span></div><div class="audit-table-wrap"><table class="audit-table"><thead><tr><th>Time</th><th>User</th><th>Team</th><th>Question</th><th>Decision</th><th>Tables touched</th><th>Rows</th><th>Latency</th></tr></thead><tbody>${entries.map((entry) => `<tr><td>${escapeHtml(new Date(entry.ts).toLocaleString())}</td><td>${escapeHtml(entry.user)}</td><td>${escapeHtml(entry.team)}</td><td class="question" title="${escapeHtml(entry.question)}">${escapeHtml(entry.question)}</td><td><span class="badge ${escapeHtml(entry.decision)}">${escapeHtml(entry.decision)}</span></td><td>${escapeHtml((entry.tables_touched || []).join(', ') || '—')}</td><td>${escapeHtml(entry.row_count ?? '—')}</td><td>${escapeHtml((entry.latency_ms || {}).total ?? '—')} ms</td></tr>`).join('') || '<tr><td colspan="8" class="empty">No audit entries yet.</td></tr>'}</tbody></table></div>`;
    } catch (error) { root.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`; }
  }
  function renderExamples() { $('example-chips').innerHTML = examples.map((question) => `<button class="chip" data-question="${escapeHtml(question)}">${escapeHtml(question)}</button>`).join(''); document.querySelectorAll('.chip').forEach((chip) => chip.addEventListener('click', () => submitQuestion(chip.dataset.question))); }
  async function init() {
    renderExamples();
    $('active-user').addEventListener('click', () => { const open = $('user-list').classList.toggle('open'); $('active-user').setAttribute('aria-expanded', String(open)); });
    $('ask-form').addEventListener('submit', (event) => { event.preventDefault(); submitQuestion($('question').value.trim()); });
    try { state.users = await api('/api/users'); renderUsers(); await loadGraph(); } catch (error) { $('graph').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`; }
  }
  init();
})();
