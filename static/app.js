/* ============================================================
   ATLAS UI · Client-side application
   ============================================================ */
(() => {
  // ---------- state ----------
  const state = {
    users: [],
    active: 'gokul',
    graph: null,
    providers: [],
    profiles: [],
    chosenProvider: 'deterministic',
    chosenModel: null,
    apiKey: localStorage.getItem('atlas.apiKey') || '',
    map: {
      scale: 1, tx: 0, ty: 0,
      dragging: false, lx: 0, ly: 0,
      showLabels: true, showArrows: true,
      layoutW: 0, layoutH: 0,
    },
    onboardingStep: 1,
    tab: 'ask',
    // board (user-built dashboard)
    board: {
      title: localStorage.getItem('atlas.boardTitle') || 'My Board',
      widgets: [],
    },
    lastAnswerById: new Map(),  // audit_id → full answer payload (for pinning)
  };

  const EXAMPLES = [
    'average salary by department',
    "show me riders' phone numbers",
    'average riders from Airport to Downtown',
    'trips per day',
    'trips by status',
    'top drivers',
  ];

  const PROVIDER_META = {
    deterministic:      { label: 'Deterministic', tag: 'local', desc: 'Offline mapping for demo questions. No API key needed.' },
    ollama:             { label: 'Ollama',        tag: 'local', desc: 'Local models via ollama.ai. Zero data egress.' },
    openai:             { label: 'OpenAI',        tag: 'cloud', desc: 'gpt-4o-mini, gpt-4o, o1, etc.' },
    anthropic:          { label: 'Anthropic',     tag: 'cloud', desc: 'Claude 3.5 Sonnet, Claude 3.5 Haiku.' },
    groq:               { label: 'Groq',          tag: 'cloud', desc: 'Fast inference on Llama, Mixtral.' },
    together:           { label: 'Together',      tag: 'cloud', desc: 'Open-source model hosting.' },
    'openai-compatible':{ label: 'OpenAI-compat', tag: 'cloud', desc: 'Any /v1/chat/completions endpoint.' },
  };

  const MODEL_SUGGESTIONS = {
    ollama:    ['qwen2.5-coder:1.5b', 'qwen2.5-coder:7b', 'sqlcoder:7b', 'llama3.2:3b', 'llama3.1:8b'],
    openai:    ['gpt-4o-mini', 'gpt-4o', 'o1-mini'],
    anthropic: ['claude-3-5-sonnet-latest', 'claude-3-5-haiku-latest'],
    groq:      ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant', 'mixtral-8x7b-32768'],
    together:  ['meta-llama/Llama-3.3-70B-Instruct-Turbo', 'Qwen/Qwen2.5-72B-Instruct-Turbo'],
    'openai-compatible': ['gpt-4o-mini'],
  };

  const $ = (id) => document.getElementById(id);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const escapeHtml = (v) => String(v ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

  // ---------- api helper ----------
  const api = async (path, options = {}) => {
    const headers = { ...(options.headers || {}) };
    if (state.apiKey) headers['X-Atlas-Key'] = state.apiKey;
    const response = await fetch(path, { ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'The service did not complete that request.');
    return data;
  };

  // ---------- toast ----------
  function toast(msg, kind = '') {
    const el = $('toast');
    el.textContent = msg;
    el.className = `toast ${kind} show`;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.className = `toast ${kind}`; }, 2400);
  }

  // ---------- tab switching ----------
  function switchTab(tabName) {
    state.tab = tabName;
    document.body.dataset.tab = tabName;
    $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === tabName));
    $$('.tab').forEach((t) => t.setAttribute('aria-selected', t.dataset.tab === tabName));
    $$('.pane').forEach((p) => p.classList.toggle('active', p.dataset.pane === tabName));
    if (tabName === 'map') requestAnimationFrame(renderMap);
    if (tabName === 'audit') loadAudit();
    if (tabName === 'governance') loadGovernance();
    if (tabName === 'models') renderProviders();
    if (tabName === 'connections') renderSavedConnections();
    if (tabName === 'board') renderBoard();
  }

  // ---------- identity ----------
  function activeUser() {
    return state.users.find((u) => u.user === state.active) || { user: state.active, team: 'unknown' };
  }
  function renderUsers() {
    const current = activeUser();
    $('active-name').textContent = current.user;
    $('active-team').textContent = current.team;
    $('active-avatar').textContent = (current.user[0] || '?').toUpperCase();
    $('current-user-hint').textContent = current.user;
    $('map-user-hint').textContent = current.user;

    $('user-list').innerHTML = state.users.map((u) => `
      <button class="user-option ${u.user === state.active ? 'selected' : ''}"
              role="option" aria-selected="${u.user === state.active}"
              data-user="${escapeHtml(u.user)}">
        <span class="avatar">${escapeHtml(u.user[0].toUpperCase())}</span>
        <span class="identity-text">
          <strong>${escapeHtml(u.user)}</strong>
          <small>${escapeHtml(u.team)}</small>
        </span>
      </button>
    `).join('');
    $$('.user-option').forEach((b) => b.addEventListener('click', () => switchUser(b.dataset.user)));
  }
  // ============================================================
  //   CHAT SESSIONS — ChatGPT-style, per identity
  // ============================================================
  // Storage shape:
  //   atlas.chatSessions = {
  //     <userId>: {
  //       sessions: [{ id, title, createdAt, updatedAt, entries: [...] }],
  //       activeSessionId: <string>
  //     }
  //   }
  //
  // Entries inside a session mirror the old format:
  //   { kind: 'question', text: '...' }  |  { kind: 'answer', payload: {...} }
  //
  // Backward-compat: the previous shape `atlas.chatHistory[user] = [entry...]`
  // is migrated on first load into a single legacy session per user.

  const CHAT_STORE_KEY = 'atlas.chatSessions';
  const LEGACY_STORE_KEY = 'atlas.chatHistory';

  function _uid() {
    return 's_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
  }

  function loadAllSessions() {
    let all = {};
    try { all = JSON.parse(localStorage.getItem(CHAT_STORE_KEY) || '{}'); }
    catch { all = {}; }
    // One-time migration from the legacy flat-thread format.
    try {
      const legacy = JSON.parse(localStorage.getItem(LEGACY_STORE_KEY) || 'null');
      if (legacy && typeof legacy === 'object') {
        for (const [u, entries] of Object.entries(legacy)) {
          if (!all[u] && Array.isArray(entries) && entries.length) {
            const now = new Date().toISOString();
            all[u] = {
              sessions: [{
                id: _uid(),
                title: _titleFromEntries(entries) || 'Previous chat',
                createdAt: now,
                updatedAt: now,
                entries,
              }],
              activeSessionId: null,
            };
            all[u].activeSessionId = all[u].sessions[0].id;
          }
        }
        localStorage.removeItem(LEGACY_STORE_KEY);
        _persistAll(all);
      }
    } catch { /* ignore migration errors */ }
    return all;
  }

  function _titleFromEntries(entries) {
    const q = (entries || []).find((e) => e.kind === 'question');
    if (!q) return '';
    return (q.text || '').slice(0, 60);
  }

  function _persistAll(all) {
    try {
      // Trim: at most 50 sessions per user, at most 200 entries per session.
      Object.keys(all).forEach((u) => {
        const bucket = all[u] || { sessions: [] };
        if (Array.isArray(bucket.sessions)) {
          bucket.sessions.forEach((s) => {
            if (Array.isArray(s.entries) && s.entries.length > 200) {
              s.entries = s.entries.slice(-200);
            }
          });
          if (bucket.sessions.length > 50) {
            // Drop the oldest by updatedAt, keeping the active one.
            const active = bucket.activeSessionId;
            bucket.sessions.sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));
            const kept = bucket.sessions.slice(0, 50);
            if (active && !kept.find((s) => s.id === active)) {
              const rec = bucket.sessions.find((s) => s.id === active);
              if (rec) kept.push(rec);
            }
            bucket.sessions = kept;
          }
        }
      });
      localStorage.setItem(CHAT_STORE_KEY, JSON.stringify(all));
    } catch { /* quota — silently skip */ }
  }

  function getUserBucket(user) {
    const all = loadAllSessions();
    if (!all[user]) all[user] = { sessions: [], activeSessionId: null };
    return { all, bucket: all[user] };
  }

  function _activeSessionOf(bucket) {
    if (!bucket.activeSessionId) return null;
    return bucket.sessions.find((s) => s.id === bucket.activeSessionId) || null;
  }

  function activeSessionFor(user) {
    const { bucket } = getUserBucket(user);
    return _activeSessionOf(bucket);
  }

  function ensureActiveSession(user) {
    const { all, bucket } = getUserBucket(user);
    let s = _activeSessionOf(bucket);
    if (!s) {
      s = _newSessionInBucket(all, bucket);
    }
    return s;
  }

  function _newSessionInBucket(all, bucket) {
    const now = new Date().toISOString();
    const s = { id: _uid(), title: 'New chat', createdAt: now, updatedAt: now, entries: [] };
    bucket.sessions.unshift(s);
    bucket.activeSessionId = s.id;
    _persistAll(all);
    return s;
  }

  function startNewSession(user) {
    const { all, bucket } = getUserBucket(user);
    // If the current active session is empty, reuse it rather than creating another empty one.
    const current = _activeSessionOf(bucket);
    if (current && (current.entries || []).length === 0) {
      return current;
    }
    return _newSessionInBucket(all, bucket);
  }

  function setActiveSession(user, sessionId) {
    const { all, bucket } = getUserBucket(user);
    if (bucket.sessions.find((s) => s.id === sessionId)) {
      bucket.activeSessionId = sessionId;
      _persistAll(all);
    }
  }

  function deleteSession(user, sessionId) {
    const { all, bucket } = getUserBucket(user);
    bucket.sessions = bucket.sessions.filter((s) => s.id !== sessionId);
    if (bucket.activeSessionId === sessionId) {
      bucket.activeSessionId = bucket.sessions[0] ? bucket.sessions[0].id : null;
    }
    _persistAll(all);
  }

  function renameSession(user, sessionId, title) {
    const { all, bucket } = getUserBucket(user);
    const s = bucket.sessions.find((x) => x.id === sessionId);
    if (s) {
      s.title = (title || '').trim().slice(0, 80) || 'New chat';
      s.updatedAt = new Date().toISOString();
      _persistAll(all);
    }
  }

  function appendToChat(user, entry) {
    const { all, bucket } = getUserBucket(user);
    let s = _activeSessionOf(bucket);
    if (!s) s = _newSessionInBucket(all, bucket);
    s.entries.push(entry);
    s.updatedAt = new Date().toISOString();
    // Auto-title the session from the first question.
    if (s.title === 'New chat' && entry.kind === 'question') {
      s.title = (entry.text || '').slice(0, 60) || 'New chat';
    }
    _persistAll(all);
  }

  // ============================================================
  //   IDENTITY SWITCH — restore that identity's active session
  // ============================================================
  async function switchUser(user) {
    state.active = user;
    closeIdentityMenu();
    renderUsers();
    renderChatForUser(user);
    renderChatDrawer();
    await loadGraph();
    if (state.tab === 'map') renderMap();
    if (state.tab === 'ask') $('question').focus();
  }

  function renderChatForUser(user) {
    const session = activeSessionFor(user);
    $('answers').replaceChildren();
    $('chat-title').textContent = session && session.entries.length
      ? (session.title || 'Chat')
      : 'Ask your governed data';
    if (!session || !session.entries.length) {
      $('welcome').classList.remove('hidden');
      return;
    }
    $('welcome').classList.add('hidden');
    _replaying = true;
    try {
      for (const entry of session.entries) {
        if (entry.kind === 'question') {
          const row = document.createElement('div');
          row.className = 'question-row';
          row.textContent = entry.text;
          $('answers').appendChild(row);
        } else if (entry.kind === 'answer') {
          renderAnswer(entry.payload);
        }
      }
    } finally {
      _replaying = false;
    }
    requestAnimationFrame(() => {
      const kids = $('answers').children;
      if (kids.length) kids[kids.length - 1].scrollIntoView({ behavior: 'auto', block: 'end' });
    });
  }

  let _replaying = false;

  // ============================================================
  //   CHAT DRAWER RENDERING
  // ============================================================
  function _dateBucket(iso) {
    const d = new Date(iso).getTime();
    const now = Date.now();
    const ONE_DAY = 24 * 3600 * 1000;
    const startOfToday = new Date(); startOfToday.setHours(0, 0, 0, 0);
    if (d >= startOfToday.getTime()) return 'Today';
    if (d >= startOfToday.getTime() - ONE_DAY) return 'Yesterday';
    if (d >= now - 7 * ONE_DAY) return 'Previous 7 days';
    if (d >= now - 30 * ONE_DAY) return 'Previous 30 days';
    return 'Older';
  }

  function _relDateLabel(iso) {
    const d = new Date(iso);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function renderChatDrawer() {
    const container = $('chat-sessions');
    if (!container) return;
    const user = state.active;
    $('chat-drawer-user-name').textContent = user || '—';

    const { bucket } = getUserBucket(user);
    const q = ($('chat-search') && $('chat-search').value || '').trim().toLowerCase();

    let sessions = [...(bucket.sessions || [])];
    // Filter by search across title + entries text
    if (q) {
      sessions = sessions.filter((s) => {
        if ((s.title || '').toLowerCase().includes(q)) return true;
        return (s.entries || []).some((e) => {
          if (e.kind === 'question' && (e.text || '').toLowerCase().includes(q)) return true;
          if (e.kind === 'answer' && ((e.payload && e.payload.question) || '').toLowerCase().includes(q)) return true;
          return false;
        });
      });
    }
    // Sort by updatedAt desc
    sessions.sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1));

    if (!sessions.length) {
      container.innerHTML = `<div class="chat-empty">
        ${q ? 'No chats match your search.' : 'No chats yet. Click <b>New chat</b> or ask a question below.'}
      </div>`;
      return;
    }

    // Group into date buckets, keeping sort order
    const groups = {};
    for (const s of sessions) {
      const b = _dateBucket(s.updatedAt || s.createdAt);
      if (!groups[b]) groups[b] = [];
      groups[b].push(s);
    }
    const BUCKET_ORDER = ['Today', 'Yesterday', 'Previous 7 days', 'Previous 30 days', 'Older'];

    let html = '';
    for (const b of BUCKET_ORDER) {
      if (!groups[b]) continue;
      html += `<div class="chat-section-label">${escapeHtml(b)}</div>`;
      for (const s of groups[b]) {
        const isActive = s.id === bucket.activeSessionId;
        html += `
          <div class="chat-session ${isActive ? 'active' : ''}" data-session-id="${escapeHtml(s.id)}" role="listitem">
            <div>
              <div class="chat-session-title" data-session-title>${escapeHtml(s.title || 'New chat')}</div>
              <div class="chat-session-meta">${escapeHtml(_relDateLabel(s.updatedAt || s.createdAt))} · ${(s.entries || []).filter(e => e.kind === 'question').length} turn${((s.entries || []).filter(e => e.kind === 'question').length === 1) ? '' : 's'}</div>
            </div>
            <div class="chat-session-actions">
              <button class="session-action-btn" data-action="rename" title="Rename">✎</button>
              <button class="session-action-btn delete" data-action="delete" title="Delete">🗑</button>
            </div>
          </div>`;
      }
    }
    container.innerHTML = html;

    // Wire click → activate; action buttons; rename inline
    container.querySelectorAll('.chat-session').forEach((el) => {
      const sid = el.dataset.sessionId;
      el.addEventListener('click', (ev) => {
        if (ev.target.closest('[data-action]') || ev.target.matches('input')) return;
        setActiveSession(state.active, sid);
        renderChatDrawer();
        renderChatForUser(state.active);
      });
      const renameBtn = el.querySelector('[data-action="rename"]');
      const deleteBtn = el.querySelector('[data-action="delete"]');
      const titleEl = el.querySelector('[data-session-title]');
      renameBtn && renameBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        _startInlineRename(titleEl, sid);
      });
      deleteBtn && deleteBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        if (!confirm('Delete this chat?')) return;
        deleteSession(state.active, sid);
        renderChatDrawer();
        renderChatForUser(state.active);
      });
    });
  }

  function _startInlineRename(titleEl, sessionId) {
    const oldTitle = titleEl.textContent;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'chat-rename-input';
    input.value = oldTitle;
    input.addEventListener('click', (e) => e.stopPropagation());
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        commit();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        cancel();
      }
    });
    input.addEventListener('blur', commit);
    titleEl.replaceWith(input);
    input.focus();
    input.select();

    function commit() {
      const v = (input.value || '').trim();
      renameSession(state.active, sessionId, v || oldTitle);
      renderChatDrawer();
    }
    function cancel() {
      renderChatDrawer();
    }
  }

  function handleNewChat() {
    startNewSession(state.active);
    renderChatDrawer();
    renderChatForUser(state.active);
    // Animate the freshly created session into view
    const first = $('chat-sessions').querySelector('.chat-session');
    if (first) first.classList.add('just-created');
    // Focus the composer, ready for the first question.
    if (state.tab === 'ask') $('question').focus();
  }

  function toggleDrawer() {
    const layout = document.querySelector('.ask-layout');
    const toggleBtn = $('chat-drawer-toggle');
    if (!layout) return;
    const isMobile = window.matchMedia('(max-width: 900px)').matches;
    if (isMobile) {
      const drawer = $('chat-drawer');
      drawer.classList.toggle('open');
    } else {
      const collapsed = layout.classList.toggle('drawer-collapsed');
      // Reveal the floating "show drawer" button only once the drawer is
      // actually collapsed — otherwise it stays invisible forever (previous
      // bug: visibility relied on a DOM-order-dependent CSS sibling selector
      // that never matched, so there was no way to bring the drawer back).
      if (toggleBtn) toggleBtn.classList.toggle('show', collapsed);
    }
  }

  function updateChatTitle(user) {
    const session = activeSessionFor(user || state.active);
    if (session && session.entries.length) {
      $('chat-title').textContent = session.title || 'Chat';
    } else {
      $('chat-title').textContent = 'Ask your governed data';
    }
  }
  function openIdentityMenu() {
    $('user-list').classList.add('open');
    $('active-user').setAttribute('aria-expanded', 'true');
  }
  function closeIdentityMenu() {
    $('user-list').classList.remove('open');
    $('active-user').setAttribute('aria-expanded', 'false');
  }
  function toggleIdentityMenu(e) {
    e.stopPropagation();
    const isOpen = $('user-list').classList.contains('open');
    if (isOpen) closeIdentityMenu(); else openIdentityMenu();
  }

  // ---------- graph loading ----------
  async function loadGraph() {
    try {
      state.graph = await api(`/api/graph/${encodeURIComponent(state.active)}`);
    } catch (e) {
      state.graph = { nodes: [], edges: [], error: e.message };
    }
  }

  // ============================================================
  //   DATA MAP — cleaner routing, no arrow clash, no overlap
  // ============================================================
  function renderMap() {
    const svg = $('map-svg');
    const emptyEl = $('map-empty');
    const nodes = (state.graph && state.graph.nodes) || [];
    const edges = (state.graph && state.graph.edges) || [];

    $('map-stats').textContent = `${nodes.length} tables · ${edges.length} joins`;

    if (!nodes.length) {
      emptyEl.classList.remove('hidden');
      svg.innerHTML = '';
      return;
    }
    emptyEl.classList.add('hidden');

    const canvas = $('map-canvas');
    const W = Math.max(canvas.clientWidth, 800);
    const H = Math.max(canvas.clientHeight, 500);
    state.map.layoutW = W;
    state.map.layoutH = H;

    const cx = W / 2, cy = H / 2;

    // ---------- layout: hub-and-spoke by degree centrality ----------
    const degree = Object.fromEntries(nodes.map((n) => [n.id, 0]));
    edges.forEach((e) => { degree[e.from] = (degree[e.from] || 0) + 1; degree[e.to] = (degree[e.to] || 0) + 1; });
    const sorted = [...nodes].sort((a, b) => degree[b.id] - degree[a.id] || a.id.localeCompare(b.id));
    const centre = sorted[0];
    const others = sorted.slice(1);

    // Node size: a bit taller so title + qualified name + col count all fit
    const NODE_W = 190, NODE_H = 74;

    const positions = { [centre.id]: { x: cx, y: cy } };
    // Radius scales with node count so cards don't cluster
    const baseRadius = Math.min(W, H) * 0.34;
    const radius = Math.max(baseRadius, others.length * 26);
    others.forEach((n, i) => {
      const angle = -Math.PI / 2 + (i * 2 * Math.PI) / Math.max(others.length, 1);
      positions[n.id] = { x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius };
    });

    // ---------- edge routing: attach to node borders, curve gently ----------
    // For each edge, compute the intersection of the straight line with each
    // node's rounded rectangle border. Then use a quadratic Bezier with a
    // small offset for the control point, so parallel edges don't overlap.
    const edgesByPair = new Map();
    edges.forEach((e) => {
      const key = [e.from, e.to].sort().join('|');
      if (!edgesByPair.has(key)) edgesByPair.set(key, []);
      edgesByPair.get(key).push(e);
    });

    // ---------- SVG defs ----------
    const defs = `
      <defs>
        <marker id="edgeArrow" viewBox="0 0 12 12" refX="10" refY="6"
                markerWidth="7" markerHeight="7" orient="auto-start-reverse"
                markerUnits="userSpaceOnUse">
          <path d="M 0 1 L 10 6 L 0 11 z" fill="rgba(232, 207, 139, 0.75)"/>
        </marker>
        <filter id="nodeGlow" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur in="SourceAlpha" stdDeviation="5"/>
          <feOffset dx="0" dy="4"/>
          <feComponentTransfer><feFuncA type="linear" slope="0.45"/></feComponentTransfer>
          <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        <linearGradient id="edgeGrad" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stop-color="rgba(232, 207, 139, 0.65)"/>
          <stop offset="100%" stop-color="rgba(184, 152, 88, 0.4)"/>
        </linearGradient>
      </defs>
    `;

    // Compute the point on a rounded rectangle border along the direction
    // from centre to target. We use the simpler ellipse approximation which
    // is close enough for a clean look and never falls inside the node.
    function borderPoint(cx, cy, tx, ty, rx, ry) {
      const dx = tx - cx, dy = ty - cy;
      const angle = Math.atan2(dy, dx);
      // Ellipse parametric equations; add a small pad so arrow head sits nicely.
      const pad = 4;
      return {
        x: cx + Math.cos(angle) * (rx + pad),
        y: cy + Math.sin(angle) * (ry + pad),
      };
    }

    // First pass: compute geometry for every edge (curve control point + label
    // anchor). We compute the label anchor as the actual midpoint of the quadratic
    // Bezier (t=0.5 evaluates to (S + 2C + E) / 4), not a linear approximation
    // — this places the label ON the curve, not between them.
    const edgeGeoms = edges.map((e) => {
      const a = positions[e.from], b = positions[e.to];
      if (!a || !b) return null;
      const rx = NODE_W / 2, ry = NODE_H / 2;
      const start = borderPoint(a.x, a.y, b.x, b.y, rx, ry);
      const end   = borderPoint(b.x, b.y, a.x, a.y, rx, ry);

      // Alternating sides for siblings: 0 → -1, 1 → +1, 2 → -2, 3 → +2, ...
      // Combined with a larger base offset, this pushes parallel edges cleanly
      // apart instead of stacking on the same side.
      const key = [e.from, e.to].sort().join('|');
      const siblings = edgesByPair.get(key) || [e];
      const idx = siblings.indexOf(e);
      const rank = Math.ceil((idx + 1) / 2) * (idx % 2 === 0 ? -1 : 1);
      const baseCurve = 46;                // wider than before → more visible fanning
      const spacing = 42;                  // gap between parallel curves
      const offsetMagnitude = rank * spacing + (rank === 0 ? baseCurve : 0);

      const midx = (start.x + end.x) / 2, midy = (start.y + end.y) / 2;
      const dx = end.x - start.x, dy = end.y - start.y;
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;
      const cx2 = midx + nx * offsetMagnitude;
      const cy2 = midy + ny * offsetMagnitude;

      // Actual point ON the Bezier at t = 0.5
      const lx = 0.25 * start.x + 0.5 * cx2 + 0.25 * end.x;
      const ly = 0.25 * start.y + 0.5 * cy2 + 0.25 * end.y;

      return { edge: e, start, end, cx2, cy2, lx, ly, rank, siblings: siblings.length };
    }).filter(Boolean);

    // Second pass: for sibling edges, shift labels along the curve so they don't
    // sit on top of each other. Alternate slight t-offsets (0.5 ± 0.12 per sibling).
    edgeGeoms.forEach((g) => {
      if (g.siblings <= 1) return;
      const shift = g.rank * 0.10;   // ±0.10, ±0.20 …
      const t = Math.max(0.28, Math.min(0.72, 0.5 + shift));
      const omt = 1 - t;
      g.lx = omt * omt * g.start.x + 2 * omt * t * g.cx2 + t * t * g.end.x;
      g.ly = omt * omt * g.start.y + 2 * omt * t * g.cy2 + t * t * g.end.y;
    });

    // Emit paths first (behind), then all labels (in front) so labels sit on top
    // regardless of which edge they belong to.
    const arrow = state.map.showArrows ? 'marker-end="url(#edgeArrow)"' : '';
    const pathMarkup = edgeGeoms.map((g) =>
      `<path class="graph-edge" d="M ${g.start.x} ${g.start.y} Q ${g.cx2} ${g.cy2} ${g.end.x} ${g.end.y}" ${arrow}/>`
    ).join('');

    const labelMarkup = state.map.showLabels
      ? edgeGeoms.map((g) => {
          const labelText = escapeHtml(g.edge.label || '');
          if (!labelText) return '';
          const approxWidth = Math.max(labelText.length * 6.2 + 14, 40);
          return `
            <g class="edge-label-group">
              <rect class="graph-edge-label-bg" x="${g.lx - approxWidth / 2}" y="${g.ly - 10}"
                    width="${approxWidth}" height="20" rx="10"/>
              <text class="graph-edge-label" x="${g.lx}" y="${g.ly + 4}" text-anchor="middle">${labelText}</text>
            </g>`;
        }).join('')
      : '';

    const edgeMarkup = pathMarkup + labelMarkup;

    // ---------- nodes ----------
    const nodeMarkup = nodes.map((n) => {
      const p = positions[n.id];
      const isCentral = n.id === centre.id;
      const rectClass = isCentral ? 'graph-node-rect central' : 'graph-node-rect';
      const parts = String(n.label || n.id).split('.');
      const schema = parts.length > 1 ? parts[0] : '';
      const tableName = parts.length > 1 ? parts.slice(1).join('.') : parts[0];

      return `
        <g class="graph-node" filter="url(#nodeGlow)">
          <rect class="${rectClass}" x="${p.x - NODE_W/2}" y="${p.y - NODE_H/2}"
                width="${NODE_W}" height="${NODE_H}" rx="14" ry="14"/>
          <text class="graph-node-schema" x="${p.x}" y="${p.y - 18}" text-anchor="middle">${escapeHtml(schema)}</text>
          <text class="graph-node-title"  x="${p.x}" y="${p.y - 2}" text-anchor="middle">${escapeHtml(tableName)}</text>
          <text class="graph-node-columns" x="${p.x}" y="${p.y + 18}" text-anchor="middle">${n.columns} columns</text>
        </g>`;
    }).join('');

    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = defs + `<g id="map-viewport" transform="translate(${state.map.tx},${state.map.ty}) scale(${state.map.scale})">${edgeMarkup}${nodeMarkup}</g>`;
  }

  function mapZoom(delta, cx, cy) {
    const newScale = Math.max(0.35, Math.min(3, state.map.scale + delta));
    if (cx !== undefined && cy !== undefined) {
      const rect = $('map-canvas').getBoundingClientRect();
      const px = cx - rect.left, py = cy - rect.top;
      const factor = newScale / state.map.scale;
      state.map.tx = px - (px - state.map.tx) * factor;
      state.map.ty = py - (py - state.map.ty) * factor;
    }
    state.map.scale = newScale;
    applyMapTransform();
  }
  function applyMapTransform() {
    const g = document.getElementById('map-viewport');
    if (g) g.setAttribute('transform', `translate(${state.map.tx},${state.map.ty}) scale(${state.map.scale})`);
  }
  function mapResetView() { state.map.scale = 1; state.map.tx = 0; state.map.ty = 0; applyMapTransform(); }

  function bindMapControls() {
    const canvas = $('map-canvas');
    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      mapZoom(-e.deltaY * 0.0012 * state.map.scale, e.clientX, e.clientY);
    }, { passive: false });
    canvas.addEventListener('mousedown', (e) => {
      state.map.dragging = true;
      state.map.lx = e.clientX; state.map.ly = e.clientY;
      canvas.style.cursor = 'grabbing';
    });
    window.addEventListener('mouseup', () => {
      state.map.dragging = false;
      canvas.style.cursor = 'grab';
    });
    window.addEventListener('mousemove', (e) => {
      if (!state.map.dragging) return;
      state.map.tx += e.clientX - state.map.lx;
      state.map.ty += e.clientY - state.map.ly;
      state.map.lx = e.clientX; state.map.ly = e.clientY;
      applyMapTransform();
    });
    $('map-zoom-in').addEventListener('click', () => mapZoom(0.2));
    $('map-zoom-out').addEventListener('click', () => mapZoom(-0.2));
    $('map-reset').addEventListener('click', () => mapResetView());
    $('map-fit').addEventListener('click', () => { mapResetView(); renderMap(); });
    $('map-show-labels').addEventListener('change', (e) => { state.map.showLabels = e.target.checked; renderMap(); });
    $('map-show-arrows').addEventListener('change', (e) => { state.map.showArrows = e.target.checked; renderMap(); });
    let resizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => { if (state.tab === 'map') renderMap(); }, 150);
    });
  }

  // ============================================================
  //   ASK FLOW & CHART RENDERING
  // ============================================================
  function renderExamples() {
    $('example-chips').innerHTML = EXAMPLES.map((q) =>
      `<button class="chip" data-question="${escapeHtml(q)}">${escapeHtml(q)}</button>`
    ).join('');
    $$('.chip').forEach((c) => c.addEventListener('click', () => submitQuestion(c.dataset.question)));
  }

  function formatValue(v) {
    if (v === '***MASKED***') return '<span class="masked">***MASKED***</span>';
    if (v === null || v === undefined) return '—';
    if (typeof v === 'number') return escapeHtml(Number.isInteger(v) ? v : v.toFixed(2));
    return escapeHtml(v);
  }

  function tableChart(answer) {
    const headers = answer.columns.map((c) => `<th>${escapeHtml(c)}</th>`).join('');
    const rows = answer.rows.map((row) =>
      `<tr>${row.map((v) => `<td>${formatValue(v)}</td>`).join('')}</tr>`
    ).join('');
    return `<div class="data-table-wrap"><table class="data-table"><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  function chart(answer) {
    const spec = answer.chart || { mark: 'table' };
    if (!answer.rows.length || spec.mark === 'table') return tableChart(answer);
    if (spec.mark === 'value')
      return `<div class="value-chart"><div class="value-number">${formatValue(answer.rows[0][0])}</div><div class="chart-title">${escapeHtml(spec.y || answer.columns[0] || 'Result')}</div></div>`;
    const points = answer.rows.map((r) => ({ label: String(r[0]), value: Number(r[1]) })).filter((p) => Number.isFinite(p.value));
    if (!points.length) return tableChart(answer);
    if (spec.mark === 'bar') return barChart(points);
    if (spec.mark === 'line') return lineChart(points);
    return tableChart(answer);
  }
  function barChart(points) {
    const w = 720, h = Math.max(160, points.length * 34 + 30);
    const max = Math.max(...points.map((p) => p.value), 1);
    const left = 160;
    return `<div class="chart-wrap"><svg viewBox="0 0 ${w} ${h}" role="img">
      <defs><linearGradient id="gradBar" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0%" stop-color="#fbe9b0"/><stop offset="100%" stop-color="#b89858"/>
      </linearGradient></defs>
      ${points.map((p, i) => {
        const y = 14 + i * 34;
        const width = (p.value / max) * (w - left - 80);
        return `
          <text class="chart-text" x="${left - 8}" y="${y + 16}" text-anchor="end">${escapeHtml(p.label)}</text>
          <rect class="chart-bar" x="${left}" y="${y}" width="${width}" height="22" rx="4"/>
          <text class="chart-text" x="${left + width + 8}" y="${y + 15}">${escapeHtml(p.value.toFixed(p.value % 1 ? 2 : 0))}</text>`;
      }).join('')}
    </svg></div>`;
  }
  function lineChart(points) {
    const w = 720, h = 260, pad = { l: 50, r: 20, t: 20, b: 50 };
    const max = Math.max(...points.map((p) => p.value), 1);
    const min = Math.min(...points.map((p) => p.value), 0);
    const x = (i) => pad.l + (i * (w - pad.l - pad.r) / Math.max(points.length - 1, 1));
    const y = (v) => pad.t + ((max - v) * (h - pad.t - pad.b) / Math.max(max - min, 1));
    const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
    const labels = [0, Math.floor((points.length - 1) / 2), points.length - 1].filter((v, i, a) => a.indexOf(v) === i);
    return `<div class="chart-wrap"><svg viewBox="0 0 ${w} ${h}" role="img">
      <line class="chart-axis" x1="${pad.l}" y1="${h - pad.b}" x2="${w - pad.r}" y2="${h - pad.b}"/>
      <line class="chart-axis" x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${h - pad.b}"/>
      <path class="chart-line" d="${path}"/>
      ${points.map((p, i) => `<circle class="chart-dot" cx="${x(i)}" cy="${y(p.value)}" r="3.5"/>`).join('')}
      ${labels.map((i) => `<text class="chart-text" x="${x(i)}" y="${h - 20}" text-anchor="middle">${escapeHtml(points[i].label)}</text>`).join('')}
      <text class="chart-text" x="${pad.l - 8}" y="${pad.t + 5}" text-anchor="end">${escapeHtml(max)}</text>
    </svg></div>`;
  }

  function renderAnswer(answer) {
    const decision = answer.decision === 'deny' ? 'BLOCKED' : answer.decision === 'mask' ? 'MASKED' : 'ALLOW';
    const latency = answer.latency_ms || {};
    const breakdown = Object.entries(latency).filter(([k]) => k !== 'total').map(([k, v]) => `${k}: ${v}ms`).join(' · ');
    const sql = answer.sql || '';
    const auditId = answer.audit_id || '';
    if (auditId) state.lastAnswerById.set(auditId, answer);

    const pinnable = answer.decision !== 'deny' && (answer.rows || []).length > 0;
    const alreadyPinned = state.board.widgets.some((w) => w.auditId === auditId);

    // If the block came from the deterministic generator or an unreachable
    // model, show a specific nudge so the user isn't stuck guessing.
    const reason = answer.reason || '';
    const isDeterministicBlock =
      answer.decision === 'deny' &&
      state.chosenProvider === 'deterministic' &&
      /deterministic|documented|recognize/i.test(reason);
    const isOllamaUnreachable =
      answer.decision === 'deny' &&
      /can't reach ollama|ollama at .* rejected|ollama returned|host\.containers\.internal/i.test(reason);
    const nudgeHtml = isOllamaUnreachable ? `
      <div class="upgrade-nudge">
        <div class="upgrade-nudge-body">
          <strong>Ollama is not reachable</strong>
          <small>Atlas can't talk to Ollama. Make sure <code>ollama serve</code> is running, then set the base URL on the Models tab. From a container, use <code>http://host.containers.internal:11434</code>.</small>
        </div>
        <button class="btn-primary" data-goto-models>Fix in Models →</button>
      </div>
    ` : (isDeterministicBlock ? `
      <div class="upgrade-nudge">
        <div class="upgrade-nudge-body">
          <strong>Need free-form questions?</strong>
          <small>You're on the offline deterministic generator. Switch to Ollama (local) or a cloud model to answer questions in your own words.</small>
        </div>
        <button class="btn-primary" data-goto-models>Open Models →</button>
      </div>
    ` : '');

    // The SQL panel: always visible, editable, runnable. If the request was
    // denied and produced no SQL, we still show the panel so the user has a
    // starting point to write SQL manually.
    const owner = answer._owner || state.active;
    const label = answer.question || 'Query';
    const prettySql = formatSql(sql);
    const sqlPanelHtml = `
      <div class="sql-panel" data-owner="${escapeHtml(owner)}" data-label="${escapeHtml(label)}">
        <div class="sql-panel-header">
          <span class="sql-label">SQL THAT ACTUALLY RAN</span>
          <div class="sql-actions">
            <span class="sql-hint">Edit and click Run to re-execute as <b>${escapeHtml(owner)}</b></span>
            <button class="sql-copy-btn" data-sql-copy>Copy</button>
            <button class="sql-reset-btn hidden" data-sql-reset>Reset</button>
            <button class="sql-run-btn" data-sql-run>▶ Run</button>
          </div>
        </div>
        ${sql ? `<textarea class="sql-edit" spellcheck="false"
                          data-original-sql="${escapeHtml(sql)}"
                          rows="${Math.min(20, Math.max(4, prettySql.split('\n').length))}"
                          placeholder="-- your SQL here">${escapeHtml(prettySql)}</textarea>`
              : `<div class="sql-no-sql">No SQL was produced — the request was blocked before any query was generated. Type SQL above to compose your own.</div>
                 <textarea class="sql-edit" spellcheck="false" data-original-sql="" rows="4" placeholder="-- try: SELECT COUNT(*) FROM rides.trips"></textarea>`}
      </div>`;

    const card = document.createElement('article');
    card.className = `answer-card ${answer.decision === 'deny' ? 'blocked' : ''}`;
    card.dataset.auditId = auditId;
    card.innerHTML = `
      <div class="answer-head">
        <span class="badge ${answer.decision}"><span>●</span>${decision}</span>
        <span class="audit-id">audit ${escapeHtml(auditId.slice(0, 8))}</span>
      </div>
      <p class="reason">${escapeHtml(answer.reason)}</p>
      ${nudgeHtml}
      ${(answer.rows || []).length ? `<div class="result">
        <div class="chart-title">${escapeHtml((answer.chart || {}).title || answer.question || 'Result')}</div>
        ${chart(answer)}
      </div>` : ''}
      ${sqlPanelHtml}
      <div class="answer-foot">
        <div class="foot-actions">
          ${pinnable ? `<button class="pin-btn ${alreadyPinned ? 'pinned' : ''}" data-pin="${escapeHtml(auditId)}">${alreadyPinned ? '◆ Pinned' : '◇ Pin to Board'}</button>` : ''}
        </div>
        <span class="latency" title="${escapeHtml(breakdown || 'timing unavailable')}">${escapeHtml(latency.total ?? 0)} ms</span>
      </div>`;
    $('answers').appendChild(card);
    $('welcome').classList.add('hidden');
    // Persist to per-identity history (unless we're replaying it right now).
    if (!_replaying && answer.decision) {
      appendToChat(owner, { kind: 'answer', payload: answer });
    }
    // Scroll into view so the newest answer is visible.
    requestAnimationFrame(() => card.scrollIntoView({ behavior: 'smooth', block: 'end' }));

    // wire pin button
    const pinBtn = card.querySelector('[data-pin]');
    if (pinBtn) pinBtn.addEventListener('click', () => togglePin(auditId, pinBtn));
    // wire upgrade nudge
    const goto = card.querySelector('[data-goto-models]');
    if (goto) goto.addEventListener('click', () => switchTab('models'));
    // wire SQL panel
    wireSqlPanel(card);
  }

  // ---------- SQL formatting ----------
  // Small, dependency-free formatter. Not a full parser: uppercases common
  // keywords and adds newlines before major clauses. Good enough for readable
  // display of typical generated SQL. The user can still edit freely.
  function formatSql(sql) {
    if (!sql || typeof sql !== 'string') return '';
    let s = sql.trim();
    // Collapse runs of whitespace to a single space (preserves quoted strings well enough for demo).
    s = s.replace(/\s+/g, ' ');
    // Break before major clauses.
    const breakBefore = [
      'SELECT','FROM','WHERE','GROUP BY','ORDER BY','HAVING','LIMIT',
      'INNER JOIN','LEFT JOIN','RIGHT JOIN','FULL JOIN','JOIN',
      'UNION ALL','UNION','WITH','ON'
    ];
    breakBefore.forEach((kw) => {
      const re = new RegExp('\\b' + kw.replace(/ /g, '\\s+') + '\\b', 'gi');
      s = s.replace(re, '\n' + kw);
    });
    // Uppercase the keywords we broke on (they may have been lowercase before).
    breakBefore.forEach((kw) => {
      const re = new RegExp('\\b' + kw.replace(/ /g, '\\s+') + '\\b', 'gi');
      s = s.replace(re, kw);
    });
    // Add indentation for lines that aren't top-level clauses.
    return s.split('\n').map((line, i) => i === 0 ? line.trim() : '  ' + line.trim()).join('\n').trim();
  }

  // ---------- SQL panel wiring ----------
  function wireSqlPanel(card) {
    const panel = card.querySelector('.sql-panel');
    if (!panel) return;
    const ta = panel.querySelector('.sql-edit');
    const runBtn = panel.querySelector('[data-sql-run]');
    const copyBtn = panel.querySelector('[data-sql-copy]');
    const resetBtn = panel.querySelector('[data-sql-reset]');
    const hint = panel.querySelector('.sql-hint');
    const owner = panel.dataset.owner;
    const label = panel.dataset.label;
    const original = ta ? (ta.dataset.originalSql || '') : '';

    // Track dirty state — enables the Reset button, shows a "modified" hint.
    if (ta) {
      const check = () => {
        const dirty = ta.value.trim() !== formatSql(original).trim() && original !== '';
        resetBtn.classList.toggle('hidden', !dirty);
        hint.classList.toggle('dirty', dirty);
        hint.innerHTML = dirty
          ? `<b>Modified</b> — click Run to execute as <b>${escapeHtml(owner)}</b>`
          : `Edit and click Run to re-execute as <b>${escapeHtml(owner)}</b>`;
      };
      ta.addEventListener('input', check);
      // Cmd/Ctrl + Enter runs the SQL from the editor.
      ta.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
          e.preventDefault();
          runBtn.click();
        }
      });
    }

    copyBtn.addEventListener('click', () => {
      const text = ta ? ta.value : '';
      if (!text) { toast('Nothing to copy', 'error'); return; }
      navigator.clipboard.writeText(text).then(
        () => toast('SQL copied', 'success'),
        () => toast('Copy failed', 'error')
      );
    });

    resetBtn.addEventListener('click', () => {
      ta.value = formatSql(original);
      resetBtn.classList.add('hidden');
      hint.classList.remove('dirty');
      hint.innerHTML = `Edit and click Run to re-execute as <b>${escapeHtml(owner)}</b>`;
    });

    runBtn.addEventListener('click', async () => {
      const sql = (ta && ta.value || '').trim();
      if (!sql) { toast('Type some SQL first', 'error'); return; }
      runBtn.disabled = true;
      runBtn.classList.add('running');
      runBtn.textContent = '⏳ Running…';
      try {
        const response = await api('/api/run-sql', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user: owner, sql, label }),
        });
        response.question = response.question || label;
        response._owner = owner;
        response._fromEditor = true;
        renderAnswer(response);
      } catch (e) {
        renderAnswer({
          decision: 'deny', reason: e.message, rows: [], columns: [],
          latency_ms: { total: 0 }, audit_id: '', chart: null,
          question: label, sql, _owner: owner, _fromEditor: true,
        });
      } finally {
        runBtn.disabled = false;
        runBtn.classList.remove('running');
        runBtn.textContent = '▶ Run';
      }
    });
  }

  async function submitQuestion(question) {
    if (!question) return;
    const btn = $('send-button');
    btn.disabled = true;
    $('question').value = '';
    autoResize();

    // Snapshot the identity that asked — used both to route the answer to the
    // correct thread and to persist to the correct history bucket in case the
    // user switches identity while the request is in flight.
    const askingUser = state.active;

    const row = document.createElement('div');
    row.className = 'question-row';
    row.textContent = question;
    $('answers').appendChild(row);
    row.scrollIntoView({ behavior: 'smooth', block: 'end' });
    // Persist question to this identity's history.
    ensureActiveSession(askingUser);
    appendToChat(askingUser, { kind: 'question', text: question });
    renderChatDrawer();
    updateChatTitle(askingUser);

    const body = { user: state.active, question };
    if (state.chosenProvider && state.chosenProvider !== 'deterministic') {
      if (state.chosenProvider === 'ollama') {
        body.provider = 'ollama';
        body.model = state.chosenModel || 'qwen2.5-coder:1.5b';
        const url = localStorage.getItem('atlas.ollamaBaseUrl');
        if (url) body.base_url = url;
      }
    }

    try {
      const response = await api('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      // Persist the question on the response for later pinning + persistence
      response.question = response.question || question;
      response._owner = askingUser;
      // Only render if the user hasn't switched away in the meantime.
      // Persistence still happens through _owner so the answer lands in the
      // right thread when they switch back.
      if (state.active === askingUser) {
        renderAnswer(response);
      } else {
        // Save silently to the asking user's history without visible render.
        appendToChat(askingUser, { kind: 'answer', payload: response });
      }
      renderChatDrawer();
      updateChatTitle(askingUser);
    } catch (e) {
      const failed = {
        decision: 'deny', reason: e.message, rows: [], columns: [],
        latency_ms: { total: 0 }, audit_id: '', chart: null, question,
        _owner: askingUser,
      };
      if (state.active === askingUser) {
        renderAnswer(failed);
      } else {
        appendToChat(askingUser, { kind: 'answer', payload: failed });
      }
    } finally {
      btn.disabled = false;
      $('question').focus();
    }
  }

  function autoResize() {
    const ta = $('question');
    ta.style.height = 'auto';
    ta.style.height = Math.min(200, ta.scrollHeight) + 'px';
  }

  // ============================================================
  //   BOARD — user-built dashboard
  // ============================================================
  function loadBoard() {
    try {
      state.board.widgets = JSON.parse(localStorage.getItem('atlas.boardWidgets') || '[]');
    } catch { state.board.widgets = []; }
  }
  function saveBoard() {
    localStorage.setItem('atlas.boardWidgets', JSON.stringify(state.board.widgets));
    localStorage.setItem('atlas.boardTitle', state.board.title);
  }
  function togglePin(auditId, btn) {
    const idx = state.board.widgets.findIndex((w) => w.auditId === auditId);
    if (idx >= 0) {
      state.board.widgets.splice(idx, 1);
      btn.classList.remove('pinned');
      btn.textContent = '◇ Pin to Board';
      toast('Removed from board');
    } else {
      const answer = state.lastAnswerById.get(auditId);
      if (!answer) { toast('Could not find the response to pin', 'error'); return; }
      state.board.widgets.push({
        auditId,
        question: answer.question || '',
        user: state.active,
        columns: answer.columns || [],
        rows: answer.rows || [],
        chart: answer.chart || null,
        decision: answer.decision,
        pinnedAt: new Date().toISOString(),
      });
      btn.classList.add('pinned');
      btn.textContent = '◆ Pinned';
      toast('Pinned to Board', 'success');
    }
    saveBoard();
    updateBoardCount();
  }
  function updateBoardCount() {
    const n = state.board.widgets.length;
    $('board-widget-count').textContent = `${n} widget${n === 1 ? '' : 's'}`;
  }
  function renderBoard() {
    $('board-title').value = state.board.title;
    updateBoardCount();
    const empty = $('board-empty');
    const grid = $('board-grid');
    if (!state.board.widgets.length) {
      empty.classList.remove('hidden');
      grid.innerHTML = '';
      return;
    }
    empty.classList.add('hidden');
    grid.innerHTML = state.board.widgets.map((w, i) => `
      <article class="widget" style="animation-delay:${i * 60}ms">
        <div class="widget-head">
          <div class="widget-head-text">
            <strong>${escapeHtml(w.question || 'Query')}</strong>
            <small>${escapeHtml(w.user)} · ${escapeHtml(new Date(w.pinnedAt).toLocaleString())}</small>
          </div>
          <button class="widget-remove" data-unpin="${escapeHtml(w.auditId)}" title="Remove">✕</button>
        </div>
        <div class="widget-body">${chart({ columns: w.columns, rows: w.rows, chart: w.chart })}</div>
      </article>
    `).join('');
    $$('[data-unpin]').forEach((b) => b.addEventListener('click', () => {
      state.board.widgets = state.board.widgets.filter((w) => w.auditId !== b.dataset.unpin);
      saveBoard(); renderBoard(); toast('Removed');
    }));
  }

  // ---------- EXPORTS ----------
  function exportPNG() {
    const grid = $('board-grid');
    if (!grid.childElementCount) { toast('Board is empty', 'error'); return; }
    // Compose a stitched image using html-to-canvas via SVG foreignObject
    const rect = grid.getBoundingClientRect();
    const w = Math.max(rect.width, 720);
    const h = grid.scrollHeight;
    const clone = grid.cloneNode(true);
    // Use serializer to embed HTML into an SVG foreignObject then draw to canvas
    const svgNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('xmlns', svgNS);
    svg.setAttribute('width', w);
    svg.setAttribute('height', h);
    const fo = document.createElementNS(svgNS, 'foreignObject');
    fo.setAttribute('x', 0); fo.setAttribute('y', 0);
    fo.setAttribute('width', w); fo.setAttribute('height', h);
    const wrapper = document.createElement('div');
    wrapper.setAttribute('xmlns', 'http://www.w3.org/1999/xhtml');
    // Inline critical styles by copying the head style content
    const cssText = Array.from(document.styleSheets).map((s) => {
      try { return Array.from(s.cssRules).map((r) => r.cssText).join('\n'); }
      catch { return ''; }
    }).join('\n');
    wrapper.innerHTML = `<style>${cssText}</style>
      <div style="background:#050503;padding:24px;color:#faf6ec;font-family:Inter,sans-serif">
        <h2 style="margin:0 0 6px;font-size:24px;background:linear-gradient(180deg,#fefaea,#d5c493);-webkit-background-clip:text;-webkit-text-fill-color:transparent">${escapeHtml(state.board.title)}</h2>
        <p style="margin:0 0 20px;color:#a89f83;font-size:12px">${escapeHtml(new Date().toLocaleString())}</p>
        ${clone.outerHTML}
      </div>`;
    fo.appendChild(wrapper);
    svg.appendChild(fo);
    const svgData = new XMLSerializer().serializeToString(svg);
    const img = new Image();
    const svgBlob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(svgBlob);
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = w * 2; canvas.height = h * 2;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#050503';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.scale(2, 2);
      ctx.drawImage(img, 0, 0);
      canvas.toBlob((blob) => {
        downloadBlob(blob, `${slug(state.board.title)}.png`);
        URL.revokeObjectURL(url);
      }, 'image/png');
    };
    img.onerror = () => { URL.revokeObjectURL(url); toast('PNG export failed (browser blocked SVG rendering)', 'error'); };
    img.src = url;
    toast('Rendering PNG…', 'success');
  }

  function exportPDF() {
    // Print-only view: open a new window with the board rendered, then invoke print.
    const w = window.open('', '_blank');
    if (!w) { toast('Popup blocked — allow popups to export PDF', 'error'); return; }
    const gridHTML = $('board-grid').outerHTML;
    const cssText = Array.from(document.styleSheets).map((s) => {
      try { return Array.from(s.cssRules).map((r) => r.cssText).join('\n'); }
      catch { return ''; }
    }).join('\n');
    w.document.write(`<!doctype html><html><head><meta charset="utf-8">
      <title>${escapeHtml(state.board.title)}</title>
      <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono&display=swap" rel="stylesheet">
      <style>
        ${cssText}
        body { background: #050503; padding: 40px; }
        .board-grid { grid-template-columns: 1fr 1fr !important; }
        .composer, .topnav, .ambience { display: none !important; }
        @media print {
          body { background: #fff !important; color: #111 !important; padding: 20px; }
          .widget { break-inside: avoid; background: #fff !important; border: 1px solid #ddd !important; color: #111 !important; box-shadow: none !important; backdrop-filter: none !important; }
          .widget-head-text strong { color: #111 !important; }
          .widget-head-text small { color: #666 !important; }
          .chart-title { color: #666 !important; }
          .data-table th { background: #f6f6f6 !important; color: #333 !important; }
          .data-table td { color: #222 !important; }
          .masked { background: #fff2cc !important; color: #7a5900 !important; }
          .chart-bar { fill: #b89858 !important; }
          .chart-line { stroke: #b89858 !important; }
          .chart-dot { fill: #b89858 !important; }
          .chart-text { fill: #555 !important; }
          .chart-axis { stroke: #ccc !important; }
          h1, h2 { color: #111 !important; -webkit-text-fill-color: #111 !important; background: none !important; }
        }
      </style></head>
      <body>
        <h1 style="margin:0 0 4px;font-size:26px;background:linear-gradient(180deg,#fefaea,#d5c493);-webkit-background-clip:text;-webkit-text-fill-color:transparent">${escapeHtml(state.board.title)}</h1>
        <p style="color:#a89f83;font-size:12px;margin:0 0 24px">Exported ${escapeHtml(new Date().toLocaleString())} · ${state.board.widgets.length} widgets</p>
        ${gridHTML}
        <script>window.onload = () => setTimeout(() => window.print(), 400);</script>
      </body></html>`);
    w.document.close();
  }

  function exportExcel() {
    // Emit CSV-per-widget wrapped in a single .xls (HTML table) file that Excel opens.
    if (!state.board.widgets.length) { toast('Board is empty', 'error'); return; }
    let html = `<html xmlns:x="urn:schemas-microsoft-com:office:excel"><head><meta charset="utf-8"></head><body>`;
    state.board.widgets.forEach((w, i) => {
      html += `<h3>${escapeHtml(w.question || 'Widget ' + (i + 1))}</h3>`;
      html += `<p>${escapeHtml(w.user)} · ${escapeHtml(new Date(w.pinnedAt).toLocaleString())}</p>`;
      html += '<table border="1"><thead><tr>';
      html += (w.columns || []).map((c) => `<th>${escapeHtml(c)}</th>`).join('');
      html += '</tr></thead><tbody>';
      html += (w.rows || []).map((r) =>
        '<tr>' + r.map((v) => `<td>${escapeHtml(v)}</td>`).join('') + '</tr>'
      ).join('');
      html += '</tbody></table><br/><br/>';
    });
    html += '</body></html>';
    const blob = new Blob(['\ufeff', html], { type: 'application/vnd.ms-excel' });
    downloadBlob(blob, `${slug(state.board.title)}.xls`);
    toast('Excel file saved', 'success');
  }

  function shareLink() {
    // Compact the board into a URL fragment. Base64(JSON(gzip-lite)).
    // Simple deflate is skipped; the payload is small and gzips at the browser.
    if (!state.board.widgets.length) { toast('Board is empty — nothing to share', 'error'); return; }
    const payload = {
      title: state.board.title,
      widgets: state.board.widgets,
      exportedAt: new Date().toISOString(),
    };
    const b64 = btoa(unescape(encodeURIComponent(JSON.stringify(payload))));
    const shareUrl = `${window.location.origin}${window.location.pathname}#board=${b64}`;
    // Also open a mailto option
    $('share-url').textContent = shareUrl;
    $('share-banner').classList.remove('hidden');
  }
  function loadBoardFromHash() {
    if (!location.hash.startsWith('#board=')) return false;
    try {
      const b64 = location.hash.slice(7);
      const payload = JSON.parse(decodeURIComponent(escape(atob(b64))));
      if (payload && Array.isArray(payload.widgets)) {
        state.board.title = payload.title || 'Shared Board';
        state.board.widgets = payload.widgets;
        saveBoard();
        toast('Loaded shared board', 'success');
        return true;
      }
    } catch (e) {
      toast('Could not decode shared board', 'error');
    }
    return false;
  }
  function copyShareUrl() {
    const url = $('share-url').textContent;
    navigator.clipboard.writeText(url).then(
      () => toast('Link copied', 'success'),
      () => toast('Copy failed', 'error')
    );
  }
  function emailBoard() {
    if (!state.board.widgets.length) { toast('Board is empty', 'error'); return; }
    // Generate share link on the fly for the email
    const payload = { title: state.board.title, widgets: state.board.widgets, exportedAt: new Date().toISOString() };
    const b64 = btoa(unescape(encodeURIComponent(JSON.stringify(payload))));
    const shareUrl = `${window.location.origin}${window.location.pathname}#board=${b64}`;
    const subject = `Atlas dashboard — ${state.board.title}`;
    const body = `You've been shared an Atlas dashboard:\n\n${state.board.title}\n\nOpen it in your browser:\n${shareUrl}\n\n— Sent from Atlas`;
    window.location.href = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function slug(s) {
    return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'board';
  }

  // ============================================================
  //   AUDIT & GOVERNANCE
  // ============================================================
  async function loadAudit() {
    const root = $('audit-view');
    root.innerHTML = '<div class="loading">Verifying the hash chain</div>';
    try {
      const audit = await api('/api/audit');
      const entries = audit.entries || [];
      root.innerHTML = `
        <div class="audit-banner ${audit.chain_ok ? '' : 'bad'}">
          <span>${audit.chain_ok ? '✓' : '!'}</span>
          <div>
            <strong>${audit.chain_ok ? `Hash chain verified — ${entries.length} entries` : 'Tampering detected'}</strong>
            <small>${escapeHtml(audit.chain_message)}</small>
          </div>
        </div>
        <div class="audit-table-wrap">
          <table class="audit-table">
            <thead><tr>
              <th>Time</th><th>User</th><th>Team</th><th>Question</th>
              <th>Decision</th><th>Tables touched</th><th>Rows</th><th>Latency</th>
            </tr></thead>
            <tbody>${entries.map((e) => `
              <tr>
                <td>${escapeHtml(new Date(e.ts).toLocaleString())}</td>
                <td>${escapeHtml(e.user)}</td>
                <td>${escapeHtml(e.team)}</td>
                <td class="question" title="${escapeHtml(e.question)}">${escapeHtml(e.question)}</td>
                <td><span class="badge ${escapeHtml(e.decision)}">${escapeHtml(e.decision)}</span></td>
                <td>${escapeHtml((e.tables_touched || []).join(', ') || '—')}</td>
                <td>${escapeHtml(e.row_count ?? '—')}</td>
                <td>${escapeHtml((e.latency_ms || {}).total ?? '—')} ms</td>
              </tr>`).join('') || '<tr><td colspan="8" class="empty">No audit entries yet — ask a question first.</td></tr>'}
            </tbody>
          </table>
        </div>`;
    } catch (e) {
      root.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
    }
  }

  async function loadGovernance() {
    const root = $('governance-view');
    root.innerHTML = '<div class="loading">Loading governance overview</div>';
    try {
      const d = await api('/api/dashboard');
      const c = d.decision_counts || { allow: 0, mask: 0, deny: 0 };
      root.innerHTML = `
        <div class="gov-row">
          <div class="gov-stat"><div class="label">Total decisions</div><div class="value">${d.total_decisions}</div><div class="sub">since first ask</div></div>
          <div class="gov-stat allow"><div class="label">Allowed</div><div class="value">${c.allow || 0}</div><div class="sub">${((d.decision_rates || {}).allow * 100 || 0).toFixed(0)}%</div></div>
          <div class="gov-stat mask"><div class="label">Masked</div><div class="value">${c.mask || 0}</div><div class="sub">${((d.decision_rates || {}).mask * 100 || 0).toFixed(0)}%</div></div>
          <div class="gov-stat deny"><div class="label">Denied</div><div class="value">${c.deny || 0}</div><div class="sub">${((d.decision_rates || {}).deny * 100 || 0).toFixed(0)}%</div></div>
        </div>
        <div class="gov-row">
          <div class="gov-card">
            <h3>PII mask events</h3>
            <div class="value" style="font-size:32px;font-weight:700;letter-spacing:-.03em;background:linear-gradient(180deg,#fbe9b0,#b89858);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent">${d.pii_mask_events}</div>
            <div class="sub" style="color:var(--muted);font-size:12.5px;margin-top:4px;">queries where PII columns were auto-masked</div>
          </div>
          <div class="gov-card">
            <h3>Top askers</h3>
            <div class="gov-list">
              ${(d.top_users || []).map((u) =>
                `<div class="gov-list-row"><span class="name">${escapeHtml(u.user)}</span><span class="count">${u.count}</span></div>`
              ).join('') || '<div class="empty">No activity yet.</div>'}
            </div>
          </div>
        </div>
        <div class="gov-card">
          <h3>Recent denials</h3>
          ${(d.recent_denials || []).map((r) => `
            <div class="denial-row">
              <div class="top"><span>${escapeHtml(r.user)}</span><span>${escapeHtml(new Date(r.ts).toLocaleString())}</span></div>
              <div class="question">${escapeHtml(r.question)}</div>
              <div class="reason">${escapeHtml(r.reason)}</div>
            </div>`).join('') || '<div class="empty">No denials yet.</div>'}
        </div>`;
    } catch (e) {
      root.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
    }
  }

  // ============================================================
  //   PROVIDERS / MODELS
  // ============================================================
  async function loadProviders() {
    try { const r = await api('/api/providers'); state.providers = r.providers || []; }
    catch { state.providers = []; }
  }
  async function loadProfiles() {
    try { const r = await api('/api/providers/profiles'); state.profiles = r.profiles || []; }
    catch { state.profiles = []; }
  }
  function updateActiveModelBadge() {
    const meta = PROVIDER_META[state.chosenProvider] || { label: state.chosenProvider };
    const model = state.chosenModel ? ` · ${state.chosenModel}` : '';
    $('active-model-pill').textContent = `✵ ${meta.label}${model}`;
    $('composer-provider-label').textContent = `${meta.label}${model}`;
    $('active-model-name').textContent = `${meta.label}${model}`;
    $('active-model-desc').textContent = meta.desc || '';
    // Set a contextual hint on the welcome panel: deterministic → warn about
    // demo-only phrasing; anything else → free-form OK.
    const hint = $('welcome-hint');
    if (hint) {
      if (state.chosenProvider === 'deterministic') {
        hint.innerHTML = '⚠ You\'re on the offline <b>Deterministic</b> generator — only the documented demo phrasings below are recognized. <a href="#" data-goto="models">Switch to Ollama or a cloud model</a> to ask in your own words.';
        hint.classList.add('warn');
      } else if (state.chosenProvider === 'ollama') {
        hint.innerHTML = `Using <b>Ollama · ${escapeHtml(state.chosenModel || '')}</b>. Checking connection… <a href="#" data-goto="models">Configure</a>`;
        hint.classList.remove('warn');
        // Fire a health check in the background.
        pingOllamaAndUpdateHint();
      } else {
        hint.innerHTML = `You're on <b>${escapeHtml(meta.label)}</b>${model ? ` (${escapeHtml(state.chosenModel)})` : ''} — ask anything in plain English. Suggestions to get started:`;
        hint.classList.remove('warn');
      }
      const link = hint.querySelector('[data-goto="models"]');
      if (link) link.addEventListener('click', (e) => { e.preventDefault(); switchTab('models'); });
    }
  }

  async function pingOllamaAndUpdateHint() {
    const hint = $('welcome-hint');
    if (!hint) return;
    const url = localStorage.getItem('atlas.ollamaBaseUrl');
    const q = url ? `?base_url=${encodeURIComponent(url)}` : '';
    try {
      const r = await api(`/api/providers/ollama/health${q}`);
      if (r.ok) {
        hint.innerHTML = `✓ Connected to <b>Ollama</b> (${escapeHtml(state.chosenModel || '')}) — ask anything in plain English. Suggestions:`;
        hint.classList.remove('warn');
      } else {
        hint.innerHTML = `⚠ ${escapeHtml(r.message)} <a href="#" data-goto="models">Fix in Models →</a>`;
        hint.classList.add('warn');
        const link = hint.querySelector('[data-goto="models"]');
        if (link) link.addEventListener('click', (e) => { e.preventDefault(); switchTab('models'); });
      }
    } catch (e) { /* silent */ }
  }
  function renderProviders() {
    const grid = $('providers-grid');
    grid.innerHTML = state.providers.map((p) => {
      const meta = PROVIDER_META[p] || { label: p, tag: 'cloud', desc: '' };
      return `
        <div class="provider-card ${meta.tag}" data-provider="${escapeHtml(p)}">
          <div class="provider-name">${escapeHtml(meta.label)}</div>
          <div class="provider-desc">${escapeHtml(meta.desc)}</div>
          <span class="provider-tag ${meta.tag}">${meta.tag.toUpperCase()}</span>
        </div>`;
    }).join('');
    $$('.provider-card').forEach((c) => c.addEventListener('click', () => {
      const provider = c.dataset.provider;
      if (provider === 'deterministic' || provider === 'ollama') {
        state.chosenProvider = provider;
        state.chosenModel = provider === 'ollama' ? MODEL_SUGGESTIONS.ollama[0] : null;
        updateActiveModelBadge();
        toast(`Model set to ${PROVIDER_META[provider].label}`, 'success');
      } else {
        openProfileModal(provider);
      }
    }));

    // Restore Ollama base URL into the input (if the user set one).
    const savedOllamaUrl = localStorage.getItem('atlas.ollamaBaseUrl') || '';
    const input = $('ollama-base-url');
    if (input) input.value = savedOllamaUrl;

    const list = $('profiles-list');
    if (!state.profiles.length) {
      list.innerHTML = '<div class="profile-empty">No profiles saved. Add one to use a cloud model.</div>';
    } else {
      list.innerHTML = state.profiles.map((p) => `
        <div class="profile-row">
          <span class="profile-provider-tag">${escapeHtml(p.provider)}</span>
          <div class="profile-name"><strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.model || '')}</small></div>
          <button class="btn-secondary" data-use="${escapeHtml(p.name)}">Use</button>
          <button class="btn-ghost" data-delete="${escapeHtml(p.name)}" title="Delete">✕</button>
        </div>
      `).join('');
      $$('[data-use]').forEach((b) => b.addEventListener('click', () => {
        const profile = state.profiles.find((p) => p.name === b.dataset.use);
        state.chosenProvider = profile.provider;
        state.chosenModel = profile.model;
        updateActiveModelBadge();
        toast(`Using profile: ${profile.name}`, 'success');
      }));
      $$('[data-delete]').forEach((b) => b.addEventListener('click', async () => {
        if (!confirm(`Delete profile "${b.dataset.delete}"?`)) return;
        try {
          await api(`/api/providers/profiles/${encodeURIComponent(b.dataset.delete)}`, { method: 'DELETE' });
          await loadProfiles();
          renderProviders();
          toast('Profile deleted', 'success');
        } catch (e) { toast(e.message, 'error'); }
      }));
    }
  }

  function openProfileModal(preselectProvider = 'openai') {
    const provider = preselectProvider;
    const suggestions = MODEL_SUGGESTIONS[provider] || [''];
    $('modal-card').innerHTML = `
      <h3>New provider profile</h3>
      <p class="desc">The API key is encrypted before it's written to disk. Deleting a profile removes the key.</p>
      <div class="form-field">
        <label>Profile name</label>
        <input id="pf-name" placeholder="my-openai" autocomplete="off">
        <small>A short handle you'll pick from later.</small>
      </div>
      <div class="form-field">
        <label>Provider</label>
        <select id="pf-provider">
          ${state.providers.filter((p) => p !== 'deterministic' && p !== 'ollama')
            .map((p) => `<option value="${escapeHtml(p)}" ${p === provider ? 'selected' : ''}>${escapeHtml(PROVIDER_META[p]?.label || p)}</option>`).join('')}
        </select>
      </div>
      <div class="form-field">
        <label>Model</label>
        <input id="pf-model" list="pf-model-list" value="${escapeHtml(suggestions[0])}" autocomplete="off">
        <datalist id="pf-model-list">${suggestions.map((m) => `<option value="${escapeHtml(m)}">`).join('')}</datalist>
      </div>
      <div class="form-field">
        <label>API key</label>
        <input id="pf-key" type="password" placeholder="sk-...">
        <small>Stored encrypted at rest. Never appears in the audit log.</small>
      </div>
      <div class="form-field">
        <label>Base URL <small>(optional)</small></label>
        <input id="pf-base" placeholder="https://api.openai.com/v1">
      </div>
      <div class="form-actions">
        <button class="btn-secondary" id="pf-cancel">Cancel</button>
        <button class="btn-primary" id="pf-save">Save profile</button>
      </div>`;
    $('modal').classList.remove('hidden');
    $('pf-provider').addEventListener('change', (e) => {
      const s = MODEL_SUGGESTIONS[e.target.value] || [''];
      $('pf-model').value = s[0];
      $('pf-model-list').innerHTML = s.map((m) => `<option value="${escapeHtml(m)}">`).join('');
    });
    $('pf-cancel').addEventListener('click', closeModal);
    $('pf-save').addEventListener('click', async () => {
      const payload = {
        name: $('pf-name').value.trim(),
        provider: $('pf-provider').value,
        model: $('pf-model').value.trim(),
        api_key: $('pf-key').value,
        base_url: $('pf-base').value.trim() || null,
      };
      if (!payload.name || !payload.model || !payload.api_key) {
        toast('Name, model, and API key are required', 'error'); return;
      }
      try {
        await api('/api/providers/profiles', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        toast(`Profile "${payload.name}" saved`, 'success');
        closeModal();
        await loadProfiles();
        renderProviders();
      } catch (e) { toast(e.message, 'error'); }
    });
  }

  // ============================================================
  //   CONNECTIONS
  // ============================================================
  function loadSavedConnections() {
    try { return JSON.parse(localStorage.getItem('atlas.connections') || '[]'); }
    catch { return []; }
  }
  function saveConnection(c) {
    const list = loadSavedConnections();
    list.push({ ...c, savedAt: new Date().toISOString() });
    localStorage.setItem('atlas.connections', JSON.stringify(list));
  }
  function deleteConnection(name) {
    localStorage.setItem('atlas.connections', JSON.stringify(loadSavedConnections().filter((c) => c.name !== name)));
  }
  function renderSavedConnections() {
    const el = $('conn-saved');
    const list = loadSavedConnections();
    if (!list.length) { el.innerHTML = ''; return; }
    el.innerHTML = `<h3 style="margin:24px 0 12px;font-size:15px;letter-spacing:-.02em;">Saved connections</h3>` +
      list.map((c) => `
        <article class="conn-card glass">
          <div class="conn-head">
            <span class="conn-badge ${escapeHtml(c.kind)}">${escapeHtml(c.kind)}</span>
            <button class="btn-ghost" data-del-conn="${escapeHtml(c.name)}">✕ Remove</button>
          </div>
          <h3>${escapeHtml(c.name)}</h3>
          <p class="conn-meta">Saved ${escapeHtml(new Date(c.savedAt).toLocaleString())}</p>
          <p class="conn-note">Restart Atlas with the corresponding env vars to activate this connection.</p>
        </article>
      `).join('');
    $$('[data-del-conn]').forEach((b) => b.addEventListener('click', () => {
      deleteConnection(b.dataset.delConn);
      renderSavedConnections();
      toast('Connection removed', 'success');
    }));
  }
  function openConnectionModal(kind) {
    const FIELDS = {
      postgres: [
        { key: 'dsn', label: 'DSN', placeholder: 'postgresql://user:pass@host:5432/db', help: 'Recommended: point at a role that only has SELECT.' },
      ],
      snowflake: [
        { key: 'account', label: 'Account', placeholder: 'xy12345.us-east-1' },
        { key: 'user', label: 'User', placeholder: 'atlas_reader' },
        { key: 'password', label: 'Password', type: 'password' },
        { key: 'warehouse', label: 'Warehouse', placeholder: 'COMPUTE_WH' },
        { key: 'database', label: 'Database', placeholder: 'ANALYTICS' },
        { key: 'schema', label: 'Schema', placeholder: 'PUBLIC' },
      ],
      bigquery: [
        { key: 'project', label: 'Project ID', placeholder: 'my-gcp-project' },
        { key: 'credentials', label: 'Service account JSON', type: 'textarea', help: 'Paste the full service-account key JSON.' },
      ],
    }[kind] || [];

    const label = { postgres: 'Postgres', snowflake: 'Snowflake', bigquery: 'BigQuery' }[kind] || kind;

    $('modal-card').innerHTML = `
      <h3>Add ${escapeHtml(label)} connection</h3>
      <p class="desc">Atlas saves this locally for reference. To activate, restart with the matching env vars — a hot-swap is on the roadmap.</p>
      <div class="form-field">
        <label>Name for this connection</label>
        <input id="cn-name" placeholder="prod-${escapeHtml(kind)}">
      </div>
      ${FIELDS.map((f) => `
        <div class="form-field">
          <label>${escapeHtml(f.label)}</label>
          ${f.type === 'textarea'
            ? `<textarea id="cn-${escapeHtml(f.key)}" rows="4" placeholder="${escapeHtml(f.placeholder || '')}"></textarea>`
            : `<input id="cn-${escapeHtml(f.key)}" type="${escapeHtml(f.type || 'text')}" placeholder="${escapeHtml(f.placeholder || '')}">`}
          ${f.help ? `<small>${escapeHtml(f.help)}</small>` : ''}
        </div>
      `).join('')}
      <div class="form-actions">
        <button class="btn-secondary" id="cn-cancel">Cancel</button>
        <button class="btn-primary" id="cn-save">Save</button>
      </div>`;
    $('modal').classList.remove('hidden');
    $('cn-cancel').addEventListener('click', closeModal);
    $('cn-save').addEventListener('click', () => {
      const name = $('cn-name').value.trim();
      if (!name) { toast('Name is required', 'error'); return; }
      const conn = { name, kind };
      FIELDS.forEach((f) => { conn[f.key] = $(`cn-${f.key}`).value; });
      saveConnection(conn);
      closeModal();
      renderSavedConnections();
      toast(`Connection "${name}" saved locally`, 'success');
    });
  }

  function closeModal() { $('modal').classList.add('hidden'); }

  // ============================================================
  //   COMPOSER PROVIDER DROPDOWN
  // ============================================================
  function toggleProviderDropdown(e) {
    e.stopPropagation();
    const menu = $('composer-provider-menu');
    if (menu.classList.contains('hidden')) {
      menu.innerHTML = ['deterministic', 'ollama', ...state.profiles.map((p) => `profile:${p.name}`)].map((key) => {
        if (key.startsWith('profile:')) {
          const p = state.profiles.find((x) => x.name === key.slice(8));
          return `<button class="composer-provider-option" data-choice="profile:${escapeHtml(p.name)}">
            <strong>${escapeHtml(p.name)}</strong><small>${escapeHtml(p.provider)} · ${escapeHtml(p.model || '')}</small></button>`;
        }
        const meta = PROVIDER_META[key];
        return `<button class="composer-provider-option" data-choice="${key}">
          <strong>${escapeHtml(meta.label)}</strong><small>${escapeHtml(meta.desc)}</small></button>`;
      }).join('');
      menu.classList.remove('hidden');
      $$('.composer-provider-option').forEach((b) => b.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const choice = b.dataset.choice;
        if (choice === 'deterministic') {
          state.chosenProvider = 'deterministic'; state.chosenModel = null;
        } else if (choice === 'ollama') {
          state.chosenProvider = 'ollama'; state.chosenModel = MODEL_SUGGESTIONS.ollama[0];
        } else if (choice.startsWith('profile:')) {
          const p = state.profiles.find((x) => x.name === choice.slice(8));
          state.chosenProvider = p.provider; state.chosenModel = p.model;
        }
        updateActiveModelBadge();
        menu.classList.add('hidden');
      }));
    } else {
      menu.classList.add('hidden');
    }
  }

  // ============================================================
  //   ONBOARDING (5 steps)
  // ============================================================
  function showOnboarding() {
    state.onboardingStep = 1;
    $('onboarding').classList.remove('hidden');
    renderOnboarding();
  }
  function renderOnboarding() {
    $$('.onb-step').forEach((s) => s.classList.toggle('hidden', +s.dataset.step !== state.onboardingStep));
    $$('.onboarding-progress .dot').forEach((d) => {
      d.classList.toggle('active', +d.dataset.step === state.onboardingStep);
      d.classList.toggle('completed', +d.dataset.step < state.onboardingStep);
    });
    $('onb-prev').classList.toggle('hidden', state.onboardingStep === 1);
    $('onb-next').textContent = state.onboardingStep === 5 ? 'Start using Atlas' : 'Continue';

    if (state.onboardingStep === 5) {
      $('onb-users').innerHTML = state.users.map((u) => `
        <div class="onb-user ${u.user === state.active ? 'selected' : ''}" data-user="${escapeHtml(u.user)}">
          <span class="avatar">${escapeHtml(u.user[0].toUpperCase())}</span>
          <div><strong>${escapeHtml(u.user)}</strong><small>${escapeHtml(u.team)}</small></div>
        </div>`).join('');
      $$('.onb-user').forEach((el) => el.addEventListener('click', () => {
        state.active = el.dataset.user;
        $$('.onb-user').forEach((x) => x.classList.toggle('selected', x.dataset.user === state.active));
        renderUsers();
      }));
    }
  }
  function nextOnboarding() {
    if (state.onboardingStep < 5) { state.onboardingStep++; renderOnboarding(); }
    else { closeOnboarding(); }
  }
  function prevOnboarding() {
    if (state.onboardingStep > 1) { state.onboardingStep--; renderOnboarding(); }
  }
  function closeOnboarding() {
    $('onboarding').classList.add('hidden');
    localStorage.setItem('atlas.onboarded', '1');
    loadGraph().then(() => renderMap());
  }

  // ============================================================
  //   EXPORT MENU
  // ============================================================
  function toggleExportMenu(e) {
    e.stopPropagation();
    const menu = $('export-menu');
    if (menu.classList.contains('hidden')) {
      menu.innerHTML = `
        <button class="export-option" data-export="pdf"><span class="icon">📄</span>Export as PDF</button>
        <button class="export-option" data-export="png"><span class="icon">🖼</span>Export as PNG</button>
        <button class="export-option" data-export="xls"><span class="icon">▦</span>Export as Excel</button>
        <button class="export-option" data-export="email"><span class="icon">✉</span>Email link</button>
      `;
      menu.classList.remove('hidden');
      $$('.export-option').forEach((b) => b.addEventListener('click', (ev) => {
        ev.stopPropagation();
        menu.classList.add('hidden');
        const kind = b.dataset.export;
        if (kind === 'pdf') exportPDF();
        else if (kind === 'png') exportPNG();
        else if (kind === 'xls') exportExcel();
        else if (kind === 'email') emailBoard();
      }));
    } else {
      menu.classList.add('hidden');
    }
  }

  // ============================================================
  //   INIT
  // ============================================================
  async function init() {
    renderExamples();
    autoResize();
    loadBoard();
    loadBoardFromHash();

    // Global click closes menus
    document.addEventListener('click', () => {
      closeIdentityMenu();
      $('composer-provider-menu').classList.add('hidden');
      $('export-menu').classList.add('hidden');
    });

    // Prevent clicks inside menus from closing them
    $('user-list').addEventListener('click', (e) => e.stopPropagation());
    $('composer-provider-menu').addEventListener('click', (e) => e.stopPropagation());
    $('export-menu').addEventListener('click', (e) => e.stopPropagation());

    // Identity
    $('active-user').addEventListener('click', toggleIdentityMenu);

    // Tabs
    $$('.tab').forEach((t) => t.addEventListener('click', () => switchTab(t.dataset.tab)));

    // Ask form
    $('ask-form').addEventListener('submit', (e) => {
      e.preventDefault();
      submitQuestion($('question').value.trim());
    });
    $('question').addEventListener('input', autoResize);
    $('question').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        submitQuestion($('question').value.trim());
      }
    });
    $('composer-provider').addEventListener('click', toggleProviderDropdown);

    // Chat drawer wiring
    const newChatBtn = $('new-chat-btn');
    if (newChatBtn) newChatBtn.addEventListener('click', handleNewChat);
    const drawerCollapse = $('chat-drawer-collapse');
    if (drawerCollapse) drawerCollapse.addEventListener('click', toggleDrawer);
    const drawerToggle = $('chat-drawer-toggle');
    if (drawerToggle) drawerToggle.addEventListener('click', toggleDrawer);
    const chatSearch = $('chat-search');
    if (chatSearch) chatSearch.addEventListener('input', renderChatDrawer);
    // Cmd/Ctrl+K focuses the chat search
    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k' && state.tab === 'ask') {
        e.preventDefault();
        chatSearch && chatSearch.focus();
      }
      // Cmd/Ctrl + Shift + O = New chat (matching ChatGPT)
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'o' && state.tab === 'ask') {
        e.preventDefault();
        handleNewChat();
      }
    });

    // Board
    $('board-title').addEventListener('input', (e) => {
      state.board.title = e.target.value.trim() || 'My Board';
      saveBoard();
    });
    $('go-ask').addEventListener('click', () => switchTab('ask'));
    $('export-btn').addEventListener('click', toggleExportMenu);
    $('share-btn').addEventListener('click', shareLink);
    $('copy-share').addEventListener('click', copyShareUrl);
    $('close-share').addEventListener('click', () => $('share-banner').classList.add('hidden'));

    // Map
    bindMapControls();

    // Connections
    $$('[data-open-conn]').forEach((b) => b.addEventListener('click', () => openConnectionModal(b.dataset.openConn)));

    // Models
    $('new-profile-btn').addEventListener('click', () => openProfileModal('openai'));

    // Ollama base URL: persist on input; test on button.
    $('ollama-base-url').addEventListener('change', (e) => {
      const v = e.target.value.trim();
      if (v) localStorage.setItem('atlas.ollamaBaseUrl', v);
      else localStorage.removeItem('atlas.ollamaBaseUrl');
    });
    $('ollama-test').addEventListener('click', async () => {
      const status = $('ollama-status');
      const btn = $('ollama-test');
      const url = $('ollama-base-url').value.trim();
      status.textContent = 'Testing…';
      status.style.color = '';
      btn.disabled = true;
      try {
        const q = url ? `?base_url=${encodeURIComponent(url)}` : '';
        const r = await api(`/api/providers/ollama/health${q}`);
        if (r.ok) {
          status.style.color = 'var(--success)';
          status.textContent = `✓ ${r.message}`;
          toast('Ollama reachable', 'success');
        } else {
          status.style.color = 'var(--danger)';
          status.textContent = `✕ ${r.message}`;
          toast('Ollama not reachable', 'error');
        }
      } catch (e) {
        status.style.color = 'var(--danger)';
        status.textContent = `✕ ${e.message}`;
      } finally {
        btn.disabled = false;
      }
    });

    // Audit
    $('audit-refresh').addEventListener('click', loadAudit);

    // Onboarding
    $('onb-next').addEventListener('click', nextOnboarding);
    $('onb-prev').addEventListener('click', prevOnboarding);
    $('onb-skip').addEventListener('click', closeOnboarding);

    // Modal close
    $('modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });

    // Escape closes menus / modal / onboarding
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeModal();
        closeIdentityMenu();
        $('composer-provider-menu').classList.add('hidden');
        $('export-menu').classList.add('hidden');
      }
    });

    // Fetch initial data
    try {
      const [users] = await Promise.all([api('/api/users'), loadProviders(), loadProfiles()]);
      state.users = users;
      renderUsers();
      updateActiveModelBadge();
      await loadGraph();
      renderMap();
      updateBoardCount();
      // Restore this identity's chat thread on first load.
      renderChatForUser(state.active);
      renderChatDrawer();
      updateChatTitle(state.active);

      // Onboarding on first visit
      if (!localStorage.getItem('atlas.onboarded')) {
        showOnboarding();
      }
    } catch (e) {
      toast(`Failed to load Atlas: ${e.message}`, 'error');
    }
  }

  init();
})();
