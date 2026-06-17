// ── lilAmy WebUI — vanilla JS SPA ───────────────────────────────────

const API = '';
let emails = [];
let selectedId = null;
let refreshTimer = null;
let lastCount = 0;
let currentSort = 'focused';  // 'focused' | 'others'

// ── To-Do List state ──────────────────────────────────────────────────

let todoItems = [];
let selectedTodoId = null;
let selectedTodoIds = new Set();  // multi-select
let lastClickedTodoId = null;     // for Shift+Click range
let currentModule = 'amail';      // 'amail' | 'todo'
let currentTodoFilter = null;     // null=All | 'pending' | 'done' | 'cancelled'
let _allTodoItems = [];           // raw items from backend (before client-side filters)
let todoFilters = {               // client-side filter state
  deadline_date: '',              // '' | 'has' | 'none' | 'week' | 'overdue'
  deadline_type: '',              // '' | 'exact' | 'approximate' | 'range' | 'deadline' | 'tbd'
  category: '',                   // '' | category name
  urgency: '',                    // '' | 'low' | 'medium' | 'high' | 'critical'
};
let _todoDebounceTimers = {};     // per-field debounce for inline editing

// ── Init ────────────────────────────────────────────────────────────

async function init() {
  await loadModules();
  await loadEmails();
  startAutoRefresh();
}

// ── Modules / Sidebar ───────────────────────────────────────────────

async function loadModules() {
  try {
    const res = await fetch(`${API}/api/modules`);
    const data = await res.json();
    renderSidebar(data.modules);
    setStatus('🟢 Connected');
  } catch (e) {
    setStatus('🔴 Offline');
  }
}

function renderSidebar(modules) {
  const list = document.getElementById('module-list');
  list.innerHTML = modules.map(m => `
    <div class="module ${m.enabled ? '' : 'disabled'}"
         ${m.enabled ? `onclick="switchModule('${m.id}')"` : ''}
         title="${m.description}">
      <span class="icon">${m.icon}</span>
      <span>${m.name}</span>
    </div>
  `).join('');
}

function switchModule(id) {
  // Update sidebar highlighting
  document.querySelectorAll('#sidebar .module').forEach(el => {
    el.classList.remove('active');
    if (el.onclick && el.getAttribute('onclick').includes(`'${id}'`)) {
      el.classList.add('active');
    }
  });

  currentModule = id;

  // Hide all views, then show the active one
  const amailView = document.getElementById('module-content');
  const todoView = document.getElementById('todo-content');
  const varView = document.getElementById('variation-content');
  const amailCtrls = document.getElementById('amail-controls');
  const todoCtrls = document.getElementById('todo-controls');

  // Hide all
  [amailView, todoView, varView].forEach(v => { if (v) v.style.display = 'none'; });
  [amailCtrls, todoCtrls].forEach(c => { if (c) c.style.display = 'none'; });

  if (id === 'todo') {
    if (todoView) todoView.style.display = 'flex';
    if (todoCtrls) todoCtrls.style.display = 'flex';
    document.getElementById('module-title').textContent = '📋 To-Do List';
    fetch(`${API}/api/todo/counts`).then(r => r.json()).then(c => {
      const el = document.getElementById('todo-count-header');
      if (el) el.textContent = `${c.all || 0} items`;
    }).catch(() => {});
    loadTodoItems();
  } else if (id === 'variations') {
    if (varView) varView.style.display = 'flex';
    document.getElementById('module-title').textContent = '📝 Variations';
    document.getElementById('btn-agent').style.display = '';
    loadProjects().then(() => {
      if (selectedProjectId) varSwitchProject();
    });
  } else {
    document.getElementById('btn-agent').style.display = 'none';
    // Default: AMail
    if (amailView) amailView.style.display = 'flex';
    if (amailCtrls) amailCtrls.style.display = 'contents';
    document.getElementById('module-title').textContent = '📧 AMail';
  }
}

function setStatus(text) {
  document.getElementById('status-text').textContent = text;
}

// ── Emails / Cards ──────────────────────────────────────────────────

async function loadEmails() {
  try {
    const res = await fetch(`${API}/api/amail/emails?limit=0`);
    const data = await res.json();
    emails = data.emails;
    lastCount = emails.length;
    applyFilters();
    // Lazy-load attachment pins after cards render
    setTimeout(() => loadAttachmentPins(), 200);
  } catch (e) {
    notify('Failed to load emails', 'error');
  }
}

async function loadAttachmentPins() {
  if (emails.length === 0) return;
  const ids = emails.map(e => e.entry_id);
  try {
    const res = await fetch(`${API}/api/amail/emails/attachments-check`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ entry_ids: ids }),
    });
    const data = await res.json();
    if (!data.ok) return;
    const counts = data.counts || {};
    for (const [eid, count] of Object.entries(counts)) {
      if (count > 0) {
        const card = document.getElementById(`card-${eid}`);
        if (card && !card.querySelector('.att-pin')) {
          const pin = document.createElement('span');
          pin.className = 'att-pin';
          pin.textContent = '📎';
          pin.title = `${count} attachment(s)`;
          pin.style.cssText = 'position:absolute;top:6px;right:8px;font-size:14px;';
          card.style.position = 'relative';
          card.appendChild(pin);
        }
      }
    }
  } catch (_) {}
}

function getFilteredEmails() {
  if (currentSort === 'focused') {
    return emails.filter(e => (e.sort_label || 'focused') === 'focused');
  }
  // Others mode — apply checkbox filters
  const showAds = document.getElementById('chk-ads')?.checked ?? true;
  const showReal = document.getElementById('chk-real')?.checked ?? true;
  return emails.filter(e => {
    const label = e.sort_label || 'focused';
    if (label === 'focused') return false;
    if (label === 'ads_auto' && showAds) return true;
    if (label === 'real_sender' && showReal) return true;
    return false;
  });
}

function renderCards() {
  const filtered = getFilteredEmails();
  const container = document.getElementById('cards-container');
  const empty = document.getElementById('cards-empty');

  if (filtered.length === 0) {
    container.innerHTML = '';
    empty.style.display = 'flex';
    return;
  }
  empty.style.display = 'none';

  container.innerHTML = filtered.map(e => {
    let todos = [];
    try { todos = typeof e.todos_json === 'string' ? JSON.parse(e.todos_json) : (e.todos_json || []); } catch (_) {}
    const cn = e.chinese_summary || e.subject?.substring(0, 60) || '(No Subject)';
    const urgClass = `urg-${e.urgency || 'low'}`;
    const time = (e.received_time || '').replace('T', ' ').substring(0, 16);

    return `
      <div class="card${selectedIds.has(e.entry_id) ? ' selected' : ''}"
           id="card-${e.entry_id}"
           onclick="selectEmail('${e.entry_id}', event)"
           ondblclick="openOutlook('${e.entry_id}', true)">
        <div class="cn">${esc(cn)}</div>
        <div class="subject">📧 ${esc(e.subject || '(No Subject)')}</div>
        <div class="meta">
          <span class="category">📂 ${esc(e.category || 'General')}</span>
          <span class="urgency ${urgClass}">⚠️ ${esc(e.urgency || 'low')}</span>
          ${e.assignee ? `<span class="assignee">👔 ${esc(e.assignee)}</span>` : ''}
        </div>
        ${todos.length ? `<div class="todos">✅ ${todos.slice(0, 2).map(esc).join(' · ')}</div>` : ''}
        <div class="time">🕐 ${time}</div>
      </div>
    `;
  }).join('');
}

function selectEmail(entryId) {
  // Auto-mark previous email as READ in Outlook
  if (selectedId && selectedId !== entryId) {
    markEmailRead(selectedId);
  }
  selectedId = entryId;
  // Update card selection
  document.querySelectorAll('.card').forEach(c => c.classList.remove('selected'));
  const card = document.getElementById(`card-${entryId}`);
  if (card) card.classList.add('selected');
  // Load detail
  loadDetail(entryId);
}

async function loadDetail(entryId) {
  const empty = document.getElementById('detail-empty');
  const content = document.getElementById('detail-content');

  const email = emails.find(e => e.entry_id === entryId);
  if (!email) {
    empty.style.display = 'flex';
    content.style.display = 'none';
    return;
  }

  empty.style.display = 'none';
  content.style.display = 'flex';

  // Clear agent info panel when switching emails
  hideAgentInfo();

  document.getElementById('det-subject').textContent = email.subject || '(No Subject)';
  document.getElementById('det-sender').textContent = `From: ${email.sender || 'Unknown'}`;
  document.getElementById('det-meta').innerHTML = `
    <span>📂 ${esc(email.category || 'General')}</span>
    <span class="urg-${email.urgency || 'low'}">⚠️ ${esc(email.urgency || 'low')}</span>
    <span>🕐 ${(email.received_time || '').replace('T', ' ').substring(0, 16)}</span>
  `;
  document.getElementById('det-assignee').textContent = email.assignee ? `👔 ${email.assignee}` : '';
  let todos = [];
  try { todos = typeof email.todos_json === 'string' ? JSON.parse(email.todos_json) : (email.todos_json || []); } catch (_) {}
  document.getElementById('det-todos').textContent = todos.length ? `✅ ${todos.join(' · ')}` : '';

  // Attachments
  loadAttachments(entryId);

  // Body
  const body = email.body || email.email_body || '';
  document.getElementById('det-body').textContent = body || '(body not available — open in Outlook for full content)';

  // Reply
  document.getElementById('det-reply').value = email.reply_draft || '';
  document.getElementById('btn-draft').disabled = false;
  document.getElementById('btn-draft').textContent = '✏️ Draft Reply';
  document.getElementById('btn-refine').disabled = !email.reply_draft;
  document.getElementById('btn-copy').disabled = !email.reply_draft;
  document.getElementById('refine-row').style.display = email.reply_draft ? 'flex' : 'none';
  document.getElementById('refine-input').value = '';
}

// ── Attachments ──────────────────────────────────────────────────────

async function loadAttachments(entryId) {
  _currentAttachments = [];
  _selectedAttIndices.clear();
  const panel = document.getElementById('det-attachments');
  const list = document.getElementById('det-att-list');
  const btnDl = document.getElementById('btn-att-download');
  const btnDlOpen = document.getElementById('btn-att-download-open');

  try {
    const res = await fetch(`${API}/api/amail/emails/${entryId}/attachments`);
    const data = await res.json();
    if (!data.ok || !data.attachments?.length) {
      panel.style.display = 'none';
      return;
    }
    _currentAttachments = data.attachments;
    panel.style.display = 'block';
    renderAttachmentList();
    btnDl.disabled = false;
    btnDlOpen.disabled = false;
  } catch (_) {
    panel.style.display = 'none';
  }
}

function renderAttachmentList() {
  const list = document.getElementById('det-att-list');
  list.innerHTML = _currentAttachments.map(a => {
    const sel = _selectedAttIndices.has(a.index);
    const sizeStr = a.size ? `${(a.size / 1024).toFixed(0)} KB` : '';
    return `
      <div class="att-item${sel ? ' selected' : ''}"
           style="display:flex;align-items:center;gap:8px;padding:4px 8px;border-radius:4px;
                  cursor:pointer;${sel ? 'background:var(--blue);color:#fff;' : 'background:var(--surface);'}
                  font-size:12px;"
           onclick="toggleAttSelect(${a.index}, event)"
           ondblclick="downloadSingleAtt(${a.index})">
        <span>📄</span>
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(a.filename || `Attachment ${a.index}`)}</span>
        ${sizeStr ? `<span style="font-size:10px;color:${sel ? '#fff' : 'var(--muted)'};">${sizeStr}</span>` : ''}
      </div>
    `;
  }).join('');
}

function toggleAttSelect(index, event) {
  if (event?.shiftKey && _currentAttachments.length) {
    // Range select
    const indices = _currentAttachments.map(a => a.index);
    const curIdx = indices.indexOf(index);
    const lastIdx = _selectedAttIndices.size ? indices.indexOf([..._selectedAttIndices].pop()) : curIdx;
    const [lo, hi] = curIdx < lastIdx ? [curIdx, lastIdx] : [lastIdx, curIdx];
    for (let i = lo; i <= hi; i++) _selectedAttIndices.add(indices[i]);
  } else if (event?.ctrlKey || event?.metaKey) {
    if (_selectedAttIndices.has(index)) _selectedAttIndices.delete(index);
    else _selectedAttIndices.add(index);
  } else {
    if (_selectedAttIndices.has(index) && _selectedAttIndices.size === 1) {
      _selectedAttIndices.clear();
    } else {
      _selectedAttIndices.clear();
      _selectedAttIndices.add(index);
    }
  }
  renderAttachmentList();
}

async function downloadSingleAtt(index) {
  try {
    const a = document.createElement('a');
    a.href = `${API}/api/amail/emails/${selectedId}/attachments/${index}/download?open_inline=true`;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  } catch (_) {}
}

async function downloadAttachments(openAfter) {
  const indices = _selectedAttIndices.size > 0 ? [..._selectedAttIndices] : _currentAttachments.map(a => a.index);
  if (indices.length === 0) return;
  for (const idx of indices) {
    const a = document.createElement('a');
    a.href = `${API}/api/amail/emails/${selectedId}/attachments/${idx}/download?open_inline=${openAfter}`;
    a.download = '';  // let server Content-Disposition set filename
    // Download-only: no target → browser downloads, never opens
    // Download & Open: target=_blank → inline disposition opens in new tab
    if (openAfter) a.target = '_blank';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    await new Promise(r => setTimeout(r, 300));
  }
}

// ── Actions ──────────────────────────────────────────────────────────

function getCount(id, fallback) {
  const el = document.getElementById(id);
  const v = parseInt(el?.value) || fallback;
  return Math.max(10, Math.min(2000, Math.round(v / 10) * 10)); // clamp + round to 10
}

async function fetchEarlier() {
  const count = getCount('earlier-count', 20);
  const btn = document.getElementById('btn-earlier');
  btn.disabled = true;
  btn.textContent = '⏳ Fetching...';
  showProgress(true);
  await runFetch(`/api/amail/fetch-earlier?count=${count}`, btn, '⬆ Earlier');
}

async function fetchLatest() {
  const count = getCount('latest-count', 20);
  const btn = document.getElementById('btn-latest');
  btn.disabled = true;
  btn.textContent = '⏳ Fetching...';
  showProgress(true);
  await runFetch(`/api/amail/fetch-latest?count=${count}`, btn, '⬇ Latest', true);
}

async function syncInbox() {
  // Show confirmation modal first
  try {
    const res = await fetch(`${API}/api/amail/emails?limit=0`);
    const data = await res.json();
    const count = data.count || 0;
    // Estimate: each email takes 3-5 seconds to process if new, but for sync
    // most are already stored, so ~5s base + 0.5s per email for body check
    const estSeconds = Math.max(5, Math.ceil(count * 0.5));
    const estText = estSeconds < 60 ? `${estSeconds} seconds` : `${Math.ceil(estSeconds / 60)} minute(s)`;

    // Get date range
    const first = data.emails?.[data.emails.length - 1];
    const last = data.emails?.[0];
    const fromDate = first?.received_time?.replace('T',' ').substring(0,16) || 'unknown';
    const toDate = last?.received_time?.replace('T',' ').substring(0,16) || 'unknown';

    document.getElementById('sync-info').innerHTML = `
      <strong>Date range:</strong> ${fromDate}<br>
      <strong>→</strong> ${toDate}<br><br>
      <strong>Emails in storage:</strong> ${count}<br>
      <strong>Estimated time:</strong> ~${estText}
    `;
  } catch (e) {
    document.getElementById('sync-info').textContent = 'Could not load storage info.';
  }
  document.getElementById('sync-modal').classList.add('show');
}

function closeSyncModal() {
  document.getElementById('sync-modal').classList.remove('show');
}

async function doSync() {
  closeSyncModal();
  const btn = document.getElementById('btn-sync');
  btn.disabled = true;
  btn.textContent = '⏳ Syncing...';
  showProgress(true);
  await runFetch('/api/amail/sync', btn, '🔄 Sync');
}

async function runFetch(url, btn, label, isPrimary) {
  const notifyId = 'fetch-' + Date.now();
  notifyProgress(notifyId, label + '...', 'Starting...');
  let phaseStarted = false;

  try {
    // Kick off the fetch (returns immediately)
    const res = await fetch(`${API}${url}`, { method: 'POST' });
    const data = await res.json();
    if (data.status !== 'started') {
      updateNotify(notifyId, 'error', 'Failed', data.message || 'Could not start');
      showProgress(false);
      btn.disabled = false;
      btn.textContent = label;
      if (isPrimary) btn.classList.add('primary');
      return;
    }

    // Poll fetch-status for real-time progress
    const poll = setInterval(async () => {
      try {
        const sr = await fetch(`${API}/api/amail/fetch-status`);
        const st = await sr.json();

        if (st.phase === 'error') {
          clearInterval(poll);
          updateNotify(notifyId, 'error', 'Failed', st.error || st.message || '');
          showProgress(false);
          btn.disabled = false;
          btn.textContent = label;
          if (isPrimary) btn.classList.add('primary');
          return;
        }

        if (st.phase === 'complete') {
          clearInterval(poll);
          showProgress(false);
          btn.disabled = false;
          btn.textContent = label;
          if (isPrimary) btn.classList.add('primary');
          // Reload emails
          await loadEmails();
          document.getElementById('email-count').textContent = `📧 ${emails.length} emails`;
          const added = st.added || 0;
          updateNotify(notifyId, 'success', 'Complete',
            `${emails.length} emails total` + (added > 0 ? ` (${added} new)` : ''));
          return;
        }

        // In progress — update the toast with real-time info
        if (!phaseStarted && st.phase !== 'idle') phaseStarted = true;

        let msg = '';
        if (st.phase === 'fetching') {
          msg = '📥 Fetching from Outlook...';
        } else if (st.phase === 'summarizing') {
          const pct = st.total > 0 ? Math.round(st.done / st.total * 100) : 0;
          msg = `🤖 Summarizing [${st.done}/${st.total}] ${pct}%`;
          if (st.current) msg += `\n   ${st.current}`;
        }
        updateNotify(notifyId, 'progress', label + '...', msg || st.message || 'Working...');

        // Update progress bar
        if (st.total > 0) {
          document.getElementById('progress-container').style.display = 'block';
          document.getElementById('progress-fill').style.width = Math.round(st.done / st.total * 100) + '%';
        }
      } catch (_) {
        // status endpoint might not be ready yet — keep polling
      }
    }, 2000);

    // Safety timeout: stop polling after 5 minutes
    setTimeout(() => {
      clearInterval(poll);
      if (btn.disabled) {
        btn.disabled = false;
        btn.textContent = label;
        if (isPrimary) btn.classList.add('primary');
        showProgress(false);
        updateNotify(notifyId, 'warning', 'Timeout', 'Operation took too long — emails may still be processing');
      }
    }, 300_000);

  } catch (e) {
    updateNotify(notifyId, 'error', 'Failed', e.message || 'Operation did not complete');
    showProgress(false);
    btn.disabled = false;
    btn.textContent = label;
    if (isPrimary) btn.classList.add('primary');
  }
}

// Legacy: keep for Ctrl+R shortcut
async function refreshInbox() {
  await fetchLatest();
}

async function draftReply() {
  if (!selectedId) return;
  const btn = document.getElementById('btn-draft');
  btn.disabled = true;
  btn.textContent = '⏳ Drafting...';
  const notifyId = 'draft-' + Date.now();
  notifyProgress(notifyId, 'Drafting reply...', 'AI is generating a response');
  // Show loading state in agent panel
  showAgentInfoLoading();

  try {
    const res = await fetch(`${API}/api/amail/emails/${selectedId}/reply`, { method: 'POST' });
    const data = await res.json();
    if (data.draft) {
      document.getElementById('det-reply').value = data.draft;
      document.getElementById('btn-refine').disabled = false;
      document.getElementById('btn-copy').disabled = false;
      document.getElementById('refine-row').style.display = 'flex';
      const email = emails.find(e => e.entry_id === selectedId);
      if (email) email.reply_draft = data.draft;
      // Populate the agent info panel
      renderAgentInfo(data.agent_info);
      updateNotify(notifyId, 'success', 'Draft ready', 'Reply generated — review before sending');
    } else if (data.error) {
      updateNotify(notifyId, 'error', 'Draft failed', data.error);
      hideAgentInfo();
    }
  } catch (e) {
    updateNotify(notifyId, 'error', 'Draft failed', e.message || '');
    hideAgentInfo();
  } finally {
    btn.disabled = false;
    btn.textContent = '✏️ Draft Reply';
  }
}

function showRefine() {
  document.getElementById('refine-row').style.display = 'flex';
  document.getElementById('refine-input').focus();
}

async function doRefine() {
  const instr = document.getElementById('refine-input').value.trim();
  if (!instr || !selectedId) return;

  const btn = document.getElementById('btn-refine');
  btn.disabled = true;
  btn.textContent = '⏳ Refining...';
  const notifyId = 'refine-' + Date.now();
  notifyProgress(notifyId, 'Refining draft...', 'Applying your edits');

  try {
    const res = await fetch(`${API}/api/amail/emails/${selectedId}/refine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instructions: instr, draft: document.getElementById('det-reply').value }),
    });
    const data = await res.json();
    if (data.draft) {
      document.getElementById('det-reply').value = data.draft;
      document.getElementById('refine-input').value = '';
      updateNotify(notifyId, 'success', 'Refined', 'Draft updated with your instructions');
    } else if (data.error) {
      updateNotify(notifyId, 'error', 'Refine failed', data.error);
    }
  } catch (e) {
    updateNotify(notifyId, 'error', 'Refine failed', e.message || '');
  } finally {
    btn.disabled = false;
    btn.textContent = '✨ Refine';
  }
}

function copyReply() {
  const text = document.getElementById('det-reply').value.trim();
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById('btn-copy');
    btn.textContent = '✅ Copied!';
    setTimeout(() => { btn.textContent = '📋 Copy'; }, 2000);
  });
}

// ── Reply Agent Info Panel ──────────────────────────────────────────

function showAgentInfoLoading() {
  const panel = document.getElementById('agent-info-panel');
  panel.style.display = 'flex';
  // Hide all data cards, show loading spinner
  document.getElementById('agent-loading').style.display = 'block';
  document.getElementById('agent-sender').style.display = 'none';
  document.getElementById('agent-intent').style.display = 'none';
  document.getElementById('agent-style').style.display = 'none';
  document.getElementById('agent-context').style.display = 'none';
  document.getElementById('agent-examples').style.display = 'none';
  document.getElementById('agent-confidence').textContent = '';
  document.getElementById('agent-confidence').className = 'confidence-badge';
}

function hideAgentInfo() {
  document.getElementById('agent-info-panel').style.display = 'none';
}

function renderAgentInfo(info) {
  const panel = document.getElementById('agent-info-panel');
  if (!info) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'flex';
  // Hide loading spinner
  document.getElementById('agent-loading').style.display = 'none';

  // Confidence badge
  const conf = info.confidence || 0;
  const confPct = Math.round(conf * 100);
  let confClass = 'low';
  if (confPct >= 60) confClass = 'high';
  else if (confPct >= 30) confClass = 'medium';
  const badge = document.getElementById('agent-confidence');
  if (badge) {
    badge.textContent = confPct + '% confident';
    badge.className = 'confidence-badge ' + confClass;
  }

  // Sender profile
  const sp = info.sender_profile;
  const senderCard = document.getElementById('agent-sender');
  if (senderCard) {
    if (sp) {
      senderCard.style.display = 'block';
      setText('ai-sender-name', sp.name);
      setText('ai-sender-email', sp.email);
      setText('ai-sender-tier', (sp.tier || 'unknown') + ' (L' + (sp.tier_level || '?') + ')');
      setText('ai-sender-reply-rate', sp.reply_rate != null ? Math.round(sp.reply_rate * 100) + '%' : '—');
      setText('ai-sender-latency', sp.avg_latency_hours != null ? sp.avg_latency_hours.toFixed(1) + 'h' : '—');
      setText('ai-sender-words', sp.avg_reply_words != null ? Math.round(sp.avg_reply_words) + ' words' : '—');
      setText('ai-sender-greeting', sp.preferred_greeting);
      setText('ai-sender-signoff', sp.signoff_preference);
      setText('ai-sender-intent', sp.top_intent);
    } else {
      senderCard.style.display = 'none';
    }
  }

  // Predicted intent
  const intentCard = document.getElementById('agent-intent');
  if (intentCard) {
    if (info.predicted_intent) {
      intentCard.style.display = 'block';
      setText('ai-intent', info.predicted_intent);
    } else {
      intentCard.style.display = 'none';
    }
  }

  // Style params
  const style = info.style_params;
  const styleCard = document.getElementById('agent-style');
  if (styleCard) {
    if (style) {
      styleCard.style.display = 'block';
      setText('ai-style-structure', style.structure_type);
      setText('ai-style-formality', style.formality_label
        ? style.formality_label + ' (' + (style.formality != null ? style.formality.toFixed(1) : '?') + '/5)'
        : '—');
      setText('ai-style-greeting', style.greeting_style);
      setText('ai-style-signoff', style.signoff);
      setText('ai-style-samples', style.sample_count != null ? String(style.sample_count) : '0');
    } else {
      styleCard.style.display = 'none';
    }
  }

  // Behavioral context
  const ctxCard = document.getElementById('agent-context');
  const ctxText = info.behavioral_context || '';
  if (ctxCard) {
    if (ctxText) {
      ctxCard.style.display = 'block';
      const ctxEl = document.getElementById('ai-context-text');
      if (ctxEl) {
        ctxEl.textContent = ctxText;
        ctxEl.style.display = 'none'; // collapsed by default
      }
    } else {
      ctxCard.style.display = 'none';
    }
  }

  // Matched examples
  const exCard = document.getElementById('agent-examples');
  if (exCard) {
    if (info.matched_examples > 0) {
      exCard.style.display = 'block';
      setText('ai-examples-count', info.matched_examples + ' historical replies');
    } else {
      exCard.style.display = 'none';
    }
  }
}

// Safe DOM setter — no-op if element missing
function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value || '—';
}

function toggleAgentContext() {
  const el = document.getElementById('ai-context-text');
  if (el.style.display === 'none') {
    el.style.display = 'block';
  } else {
    el.style.display = 'none';
  }
}

async function removeEmail() {
  if (!selectedId) return;
  try {
    await fetch(`${API}/api/amail/emails/${selectedId}/remove`, { method: 'POST' });
    emails = emails.filter(e => e.entry_id !== selectedId);
    selectedId = null;
    renderCards();
    document.getElementById('email-count').textContent = `📧 ${emails.length} emails`;
    document.getElementById('detail-empty').style.display = 'flex';
    document.getElementById('detail-content').style.display = 'none';
    notify('Email removed', 'success');
  } catch (e) {
    notify('Remove failed', 'error');
  }
}

async function openOutlook(entryId, replyMode = false, replyAll = false, forward = false) {
  // Call backend to open email in native Outlook via COM
  let url = `${API}/api/amail/emails/${entryId}/open-in-outlook`;
  if (forward) {
    url += `?forward=true`;
  } else if (replyAll) {
    url += `?reply_all=true`;
  } else if (replyMode) {
    url += `?reply_mode=true`;
  }
  try {
    const res = await fetch(url, { method: 'POST' });
    const data = await res.json();
    if (!data.ok) {
      // Fallback: open mailto link
      const email = emails.find(e => e.entry_id === entryId);
      if (email) {
        window.open(`mailto:?subject=Re:%20${encodeURIComponent(email.subject || '')}`, '_blank');
      }
    }
  } catch (_) {
    // Fallback on network error
    const email = emails.find(e => e.entry_id === entryId);
    if (email) {
      window.open(`mailto:?subject=Re:%20${encodeURIComponent(email.subject || '')}`, '_blank');
    }
  }
}

// ── Mail sorting ─────────────────────────────────────────────────────

function setSort(mode) {
  currentSort = mode;
  const btnF = document.getElementById('btn-focused');
  const btnO = document.getElementById('btn-others');
  const filters = document.getElementById('others-filters');
  if (btnF) btnF.classList.toggle('active', mode === 'focused');
  if (btnO) btnO.classList.toggle('active', mode === 'others');
  if (filters) filters.style.display = mode === 'others' ? 'flex' : 'none';
  applyFilters();
}

function applyFilters() {
  renderCards();
  // Reload attachment pins after card re-render
  setTimeout(() => loadAttachmentPins(), 200);
  // Update count display
  const filtered = getFilteredEmails();
  const countEl = document.getElementById('email-count');
  if (countEl) {
    countEl.textContent =
      `📧 ${filtered.length} / ${emails.length} emails` +
      (selectedIds.size > 0 ? ` (${selectedIds.size} selected)` : '');
  }
}

// ── Date-range sync ──────────────────────────────────────────────────

async function openSyncRange() {
  // Pre-fill with storage range
  try {
    const res = await fetch('/api/amail/emails?limit=0');
    const data = await res.json();
    if (data.emails && data.emails.length > 0) {
      const times = data.emails.map(e => (e.received_time || '').replace('T',' ').substring(0,16));
      const earliest = times.reduce((a,b) => a < b ? a : b);
      const latest = times.reduce((a,b) => a > b ? a : b);
      document.getElementById('sync-range-from').value = earliest;
      document.getElementById('sync-range-to').value = latest;
    }
  } catch (_) {}
  document.getElementById('sync-range-modal').classList.add('show');
}

function closeSyncRange() {
  document.getElementById('sync-range-modal').classList.remove('show');
}

async function doSyncRange() {
  closeSyncRange();
  const from = document.getElementById('sync-range-from').value;
  const to = document.getElementById('sync-range-to').value;
  const params = new URLSearchParams();
  if (from) params.append('from_date', from + ':00');
  if (to) params.append('to_date', to + ':00');

  const btn = document.getElementById('btn-sync');
  btn.disabled = true;
  btn.textContent = '⏳ Syncing...';
  showProgress(true);
  await runFetch('/api/amail/sync?' + params.toString(), btn, '🔄 Sync');
}

// ── Auto-refresh ─────────────────────────────────────────────────────

function startAutoRefresh() {
  refreshTimer = setInterval(async () => {
    // Silently fetch latest in background
    try { await fetch(`${API}/api/amail/fetch-latest?count=10`, { method: 'POST' }); } catch (_) {}
    await loadEmails();
    document.getElementById('email-count').textContent = `📧 ${emails.length} emails`;
    if (selectedId && !emails.find(e => e.entry_id === selectedId)) {
      selectedId = null;
      document.getElementById('detail-empty').style.display = 'flex';
      document.getElementById('detail-content').style.display = 'none';
    }
  }, 600_000);
}

// ── Utilities ────────────────────────────────────────────────────────

function esc(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = String(str);
  return div.innerHTML;
}

function showProgress(show) {
  document.getElementById('progress-container').style.display = show ? 'block' : 'none';
  document.getElementById('progress-fill').style.width = show ? '30%' : '0%';
}

// ── Toast Notification System ────────────────────────────────────────

const ICONS = { info: 'ℹ️', progress: '🔄', success: '✅', warning: '⚠️', error: '❌' };
const MAX_TOASTS = 4;

function notify(msg, type = 'info', duration = 4000) {
  const id = 'ntf-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
  _renderToast(id, type, msg, '', duration, []);
  return id;
}

function notifyProgress(id, title, msg = '') {
  _renderToast(id, 'progress', title, msg, 0, [], true);
  return id;
}

function updateNotify(id, type, title, msg = '') {
  const el = document.getElementById('toast-' + id);
  if (!el) return;
  // Update type class
  el.className = 'toast-item ' + type;
  // Update icon
  const icon = el.querySelector('.toast-icon');
  if (icon) {
    if (type === 'progress') {
      icon.innerHTML = _spinnerHTML();
    } else {
      icon.textContent = ICONS[type] || '';
    }
  }
  // Update title
  const titleEl = el.querySelector('.toast-title');
  if (titleEl) titleEl.textContent = title;
  // Update message
  const msgEl = el.querySelector('.toast-msg');
  if (msgEl) { msgEl.textContent = msg; msgEl.style.display = msg ? 'block' : 'none'; }
  // Add auto-dismiss for success/warning/info
  if (type === 'success' || type === 'info') {
    setTimeout(() => dismissNotify(id), type === 'success' ? 5000 : 4000);
  }
}

function dismissNotify(id) {
  const el = document.getElementById('toast-' + id);
  if (!el) return;
  el.classList.add('removing');
  setTimeout(() => { if (el.parentNode) el.remove(); }, 300);
}

function _spinnerHTML() {
  return '<span class=\"spinner\"></span>';
}

function _renderToast(id, type, title, msg, duration, actions, useSpinner) {
  // Ensure container exists (defensive against cached HTML)
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  // Enforce max toasts
  const items = container.querySelectorAll('.toast-item');
  if (items.length >= MAX_TOASTS) {
    const oldest = items[items.length - 1]; // last in column-reverse = oldest
    oldest.classList.add('removing');
    setTimeout(() => { if (oldest.parentNode) oldest.remove(); }, 300);
  }

  const iconHTML = useSpinner ? _spinnerHTML() : (ICONS[type] || '');
  const closeBtn = (type === 'progress' || type === 'error')
    ? `<button class="toast-close" onclick="dismissNotify('${id}')">✕</button>`
    : '';

  const msgHTML = msg ? `<div class="toast-msg">${esc(msg)}</div>` : '';
  const progressHTML = (duration > 0 || type === 'progress')
    ? '<div class="toast-progress"><div class="toast-progress-fill" style="width:100%"></div></div>'
    : '';

  let actionsHTML = '';
  if (actions.length > 0) {
    actionsHTML = '<div class="toast-actions">' + actions.map(a =>
      `<button class="${a.primary ? 'primary' : ''}" onclick="${a.onclick}">${esc(a.label)}</button>`
    ).join('') + '</div>';
  }

  const html = `<div class="toast-item ${type}" id="toast-${id}">`
    + '<div class="toast-header">'
    + `<span class="toast-icon">${iconHTML}</span>`
    + `<span class="toast-title">${esc(title)}</span>`
    + closeBtn
    + '</div>'
    + msgHTML
    + progressHTML
    + actionsHTML
    + '</div>';

  container.insertAdjacentHTML('afterbegin', html);

  // Auto-dismiss timer
  if (duration > 0) {
    // Animate progress bar
    const el = document.getElementById('toast-' + id);
    if (el) {
      const fill = el.querySelector('.toast-progress-fill');
      if (fill) {
        setTimeout(() => { fill.style.width = '0%'; }, 50);
      }
    }
    setTimeout(() => dismissNotify(id), duration);
  }
}

// Legacy wrapper for backward compat
function showToast(msg) { notify(msg, 'info', 3000); }

// ── Multi-select ────────────────────────────────────────────────────

let selectedIds = new Set();    // all selected entry_ids
let lastClickedId = null;       // for Shift+Click range
let _currentAttachments = [];   // attachments for selected email
let _selectedAttIndices = new Set();  // selected attachment indices for download

function getCardIndex(id) {
  return emails.findIndex(e => e.entry_id === id);
}

function isSelected(id) { return selectedIds.has(id); }

function selectSingle(id) {
  // Auto-mark previous email as READ in Outlook (user clicked away = processed)
  if (selectedId && selectedId !== id) {
    markEmailRead(selectedId);
  }
  selectedIds.clear();
  selectedIds.add(id);
  lastClickedId = id;
  selectedId = id;
  applySelection();
  loadDetail(id);
}

function selectRange(fromId, toId) {
  const a = getCardIndex(fromId);
  const b = getCardIndex(toId);
  if (a < 0 || b < 0) return;
  selectedIds.clear();
  const [lo, hi] = a < b ? [a, b] : [b, a];
  for (let i = lo; i <= hi; i++) selectedIds.add(emails[i].entry_id);
  lastClickedId = toId;
  applySelection();
}

function toggleSelect(id) {
  if (selectedIds.has(id)) {
    selectedIds.delete(id);
  } else {
    selectedIds.add(id);
  }
  lastClickedId = id;
  if (selectedIds.size === 1) {
    selectedId = [...selectedIds][0];
    loadDetail(selectedId);
  }
  applySelection();
}

// Click empty area → deselect all
document.getElementById('card-panel').addEventListener('click', (e) => {
  if (!e.target.closest('.card')) {
    // Auto-mark last viewed as READ
    if (selectedId) markEmailRead(selectedId);
    selectedIds.clear();
    lastClickedId = null;
    applySelection();
  }
});

function applySelection() {
  document.querySelectorAll('.card').forEach(c => {
    const eid = c.id?.replace('card-', '') || '';
    c.classList.toggle('selected', selectedIds.has(eid));
  });
  document.getElementById('email-count').textContent =
    `📧 ${emails.length} emails` + (selectedIds.size > 0 ? ` (${selectedIds.size} selected)` : '');
}

// Override old selectEmail — now handles Shift/Ctrl
function selectEmail(entryId, event) {
  if (event?.shiftKey && lastClickedId) {
    selectRange(lastClickedId, entryId);
  } else if (event?.ctrlKey || event?.metaKey) {
    toggleSelect(entryId);
  } else {
    selectSingle(entryId);
  }
}

// ── Context menu ────────────────────────────────────────────────────

const ctxMenu = document.getElementById('ctx-menu');

document.addEventListener('contextmenu', (e) => {
  const card = e.target.closest('.card');
  if (!card) { ctxMenu.style.display = 'none'; return; }
  e.preventDefault();

  // ── Module-based visibility ──────────────────────────────────────
  const mod = currentModule;
  const isTrash = (mod === 'todo' && currentTodoFilter === 'cancelled');
  const isVoidFilter = (mod === 'variations' && currentVarFilter === 'void');

  // Determine which "tag" applies for this module context
  let activeTag = mod;
  if (isTrash) activeTag = 'trash';
  if (isVoidFilter) activeTag = 'void';

  document.querySelectorAll('#ctx-menu .item, #ctx-menu .divider').forEach(el => {
    const tags = (el.getAttribute('data-modules') || '').split(/\s+/).filter(Boolean);
    el.style.display = tags.includes(activeTag) ? '' : 'none';
  });

  // Update remove label per module
  const removeLabel = document.querySelector('.ctx-remove-label');
  if (removeLabel) {
    const labels = { amail: 'Remove emails', todo: 'Remove tasks', trash: 'Remove tasks', variations: 'Remove variations', void: 'Remove variations' };
    removeLabel.textContent = labels[activeTag] || 'Remove';
  }

  // ── Resolve selection from card ID ────────────────────────────────
  if (mod === 'amail') {
    const eid = card.id?.replace('card-', '') || '';
    if (!selectedIds.has(eid)) selectSingle(eid);
  } else if (mod === 'todo') {
    const tid = card.id?.replace('todo-card-', '') || '';
    if (!selectedTodoIds.has(tid)) selectTodoSingle(tid);
  } else if (mod === 'variations') {
    const vid = card.id?.replace('varcard-', '') || '';
    if (!selectedVarIds.has(vid)) selectVarSingle(vid);
  }

  ctxMenu.style.display = 'block';
  ctxMenu.style.left = Math.min(e.clientX, window.innerWidth - 210) + 'px';
  ctxMenu.style.top = Math.min(e.clientY, window.innerHeight - 140) + 'px';
});

document.addEventListener('click', (e) => {
  if (!e.target.closest('.ctx-menu')) ctxMenu.style.display = 'none';
});
// Close menu on scroll for all card panels
['card-panel', 'todo-card-panel', 'var-card-panel'].forEach(id => {
  document.getElementById(id)?.addEventListener('scroll', () => ctxMenu.style.display = 'none');
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') ctxMenu.style.display = 'none';
});

function ctxOpenOutlook() {
  for (const id of selectedIds) openOutlook(id);
  ctxMenu.style.display = 'none';
}

function ctxReply() {
  for (const id of selectedIds) openOutlook(id, true, false);
  ctxMenu.style.display = 'none';
}

function ctxReplyAll() {
  for (const id of selectedIds) openOutlook(id, false, true);
  ctxMenu.style.display = 'none';
}

async function ctxRemove() {
  const toRemove = [...selectedIds];
  if (toRemove.length === 0) { ctxMenu.style.display = 'none'; return; }

  const notifyId = 'remove-' + Date.now();
  notifyProgress(notifyId, `Removing ${toRemove.length} email(s)...`, 'Please wait');
  for (const id of toRemove) {
    try { await fetch(`${API}/api/amail/emails/${id}/remove`, { method: 'POST' }); } catch (_) {}
  }
  emails = emails.filter(e => !selectedIds.has(e.entry_id));
  if (selectedIds.has(selectedId)) {
    selectedId = null;
    document.getElementById('detail-empty').style.display = 'flex';
    document.getElementById('detail-content').style.display = 'none';
  }
  selectedIds.clear();
  lastClickedId = null;
  applyFilters();
  updateNotify(notifyId, 'success', 'Removed', `${toRemove.length} email(s) removed`);
  ctxMenu.style.display = 'none';
}

// ── Mark email read/unread in Outlook (fire-and-forget) ────────────

async function markEmailRead(entryId) {
  try {
    await fetch(`${API}/api/amail/emails/mark-read`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ entry_ids: [entryId] }),
    });
  } catch (_) {}
}

async function markEmailsRead(entryIds) {
  if (!entryIds.length) return;
  try {
    await fetch(`${API}/api/amail/emails/mark-read`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ entry_ids: entryIds }),
    });
  } catch (_) {}
}

async function ctxMarkUnread() {
  const toMark = [...selectedIds];
  if (toMark.length === 0) { ctxMenu.style.display = 'none'; return; }
  try {
    await fetch(`${API}/api/amail/emails/mark-unread`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ entry_ids: toMark }),
    });
    notify(`Marked ${toMark.length} email(s) as unread`, 'success');
  } catch (_) {}
  ctxMenu.style.display = 'none';
}

async function ctxDismissRead() {
  // Dismiss cards from AMail AND mark as READ in Outlook
  const toDismiss = [...selectedIds];
  if (toDismiss.length === 0) { ctxMenu.style.display = 'none'; return; }
  const notifyId = 'dismiss-' + Date.now();
  notifyProgress(notifyId, `Dismissing ${toDismiss.length} email(s)...`, '');
  // Mark as READ in Outlook
  await markEmailsRead(toDismiss);
  // Remove from AMail store
  for (const id of toDismiss) {
    try { await fetch(`${API}/api/amail/emails/${id}/remove`, { method: 'POST' }); } catch (_) {}
  }
  emails = emails.filter(e => !selectedIds.has(e.entry_id));
  if (selectedIds.has(selectedId)) {
    selectedId = null;
    document.getElementById('detail-empty').style.display = 'flex';
    document.getElementById('detail-content').style.display = 'none';
  }
  selectedIds.clear();
  lastClickedId = null;
  applyFilters();
  updateNotify(notifyId, 'success', 'Dismissed', `${toDismiss.length} email(s) dismissed & marked read`);
  ctxMenu.style.display = 'none';
}

async function ctxFlagAndPush() {
  // Flag in Outlook AND push to To-Do List
  const selected = [...selectedIds];
  if (selected.length === 0) { ctxMenu.style.display = 'none'; return; }

  const notifyId = 'flagpush-' + Date.now();
  notifyProgress(notifyId, `Flagging & pushing ${selected.length} email(s)...`, '');

  // Fire both in parallel
  const flagP = fetch(`${API}/api/amail/emails/mark-flagged`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ entry_ids: selected }),
  }).catch(() => {});
  const pushP = fetch(`${API}/api/todo/push-from-emails`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ email_ids: selected }),
  }).then(r => r.json()).catch(() => null);

  const [flagRes, pushData] = await Promise.all([flagP, pushP]);
  const count = pushData?.count || 0;
  updateNotify(notifyId, 'success', 'Flagged & Pushed',
    `${selected.length} email(s) flagged in Outlook · ${count} task(s) created`);
  ctxMenu.style.display = 'none';
}

function ctxRemoveCurrent() {
  if (currentModule === 'variations') {
    ctxRemoveVariations();
  } else if (currentModule === 'todo') {
    ctxRemoveTodo();
  } else {
    ctxRemove();  // amail
  }
}

async function ctxRemoveVariations() {
  const toRemove = [...selectedVarIds];
  if (toRemove.length === 0) { ctxMenu.style.display = 'none'; return; }

  const notifyId = 'remove-var-' + Date.now();
  notifyProgress(notifyId, `Removing ${toRemove.length} variation(s)...`, 'Please wait');
  for (const id of toRemove) {
    try { await fetch(`${API}/api/variations/${id}`, { method: 'DELETE' }); } catch (_) {}
  }
  variations = variations.filter(v => !selectedVarIds.has(v.entry_id));
  if (selectedVarIds.has(selectedVarId)) {
    selectedVarId = null;
    document.getElementById('var-detail').style.display = 'none';
    document.getElementById('var-detail-empty').style.display = 'flex';
  }
  selectedVarIds.clear();
  lastClickedVarId = null;
  renderVarCards();
  updateNotify(notifyId, 'success', 'Removed', `${toRemove.length} variation(s) voided`);
  ctxMenu.style.display = 'none';
}

async function ctxRemoveTodo() {
  const toRemove = [...selectedTodoIds];
  if (toRemove.length === 0) { ctxMenu.style.display = 'none'; return; }

  const notifyId = 'remove-todo-' + Date.now();
  notifyProgress(notifyId, `Removing ${toRemove.length} task(s)...`, 'Please wait');
  for (const id of toRemove) {
    try { await fetch(`${API}/api/todo/items/${id}`, { method: 'DELETE' }); } catch (_) {}
  }
  todoItems = todoItems.filter(i => !selectedTodoIds.has(i.entry_id));
  if (selectedTodoIds.has(selectedTodoId)) {
    selectedTodoId = null;
    const empty = document.getElementById('todo-detail-empty');
    const detail = document.getElementById('todo-detail');
    if (empty) empty.style.display = 'flex';
    if (detail) detail.style.display = 'none';
  }
  selectedTodoIds.clear();
  lastClickedTodoId = null;
  renderTodoCards();
  updateNotify(notifyId, 'success', 'Removed', `${toRemove.length} task(s) removed`);
  ctxMenu.style.display = 'none';
}

// ── Dispatch: restore (variations or todo) ─────────────────────────

function ctxRestoreCurrent() {
  if (currentModule === 'variations') {
    ctxRestoreVariations();
  } else {
    ctxRestoreTodo();
  }
}

function ctxPermanentRemoveCurrent() {
  if (currentModule === 'variations') {
    ctxPermanentRemoveVariations();
  } else {
    ctxPermanentRemoveTodo();
  }
}

// ── Variation restore / permanent-delete ────────────────────────────

async function ctxRestoreVariations() {
  const toRestore = [...selectedVarIds];
  if (toRestore.length === 0) { ctxMenu.style.display = 'none'; return; }
  const notifyId = 'restore-var-' + Date.now();
  notifyProgress(notifyId, `Restoring ${toRestore.length} variation(s)...`, '');
  let restored = 0;
  for (const id of toRestore) {
    try {
      const res = await fetch(`${API}/api/variations/${id}/restore`, { method: 'POST' });
      const data = await res.json();
      if (data.ok) restored++;
    } catch (_) {}
  }
  selectedVarIds.clear();
  lastClickedVarId = null;
  selectedVarId = null;
  document.getElementById('var-detail').style.display = 'none';
  document.getElementById('var-detail-empty').style.display = 'flex';
  await loadVariations(currentVarFilter);
  updateNotify(notifyId, 'success', 'Restored', `${restored} variation(s) moved back to draft`);
  ctxMenu.style.display = 'none';
}

async function ctxPermanentRemoveVariations() {
  const toDelete = [...selectedVarIds];
  if (toDelete.length === 0) { ctxMenu.style.display = 'none'; return; }
  if (!confirm(`Permanently delete ${toDelete.length} variation(s)? This cannot be undone.`)) {
    ctxMenu.style.display = 'none';
    return;
  }
  const notifyId = 'perm-del-var-' + Date.now();
  notifyProgress(notifyId, `Deleting ${toDelete.length} variation(s)...`, '');
  for (const id of toDelete) {
    try { await fetch(`${API}/api/variations/${id}/permanent`, { method: 'DELETE' }); } catch (_) {}
  }
  selectedVarIds.clear();
  lastClickedVarId = null;
  selectedVarId = null;
  document.getElementById('var-detail').style.display = 'none';
  document.getElementById('var-detail-empty').style.display = 'flex';
  await loadVariations(currentVarFilter);
  updateNotify(notifyId, 'success', 'Deleted', `${toDelete.length} variation(s) permanently removed`);
  ctxMenu.style.display = 'none';
}

async function ctxRestoreTodo() {
  const toRestore = [...selectedTodoIds];
  if (toRestore.length === 0) { ctxMenu.style.display = 'none'; return; }
  const notifyId = 'restore-' + Date.now();
  notifyProgress(notifyId, `Restoring ${toRestore.length} task(s)...`, '');
  let restored = 0;
  for (const id of toRestore) {
    try {
      const res = await fetch(`${API}/api/todo/items/${id}/restore`, { method: 'POST' });
      const data = await res.json();
      if (data.ok) restored++;
    } catch (_) {}
  }
  selectedTodoIds.clear();
  lastClickedTodoId = null;
  await loadTodoItems(currentTodoFilter);
  updateNotify(notifyId, 'success', 'Restored', `${restored} task(s) moved back to pending`);
  ctxMenu.style.display = 'none';
}

async function ctxPermanentRemoveTodo() {
  const toDelete = [...selectedTodoIds];
  if (toDelete.length === 0) { ctxMenu.style.display = 'none'; return; }
  if (!confirm(`Permanently delete ${toDelete.length} task(s)? This cannot be undone.`)) {
    ctxMenu.style.display = 'none';
    return;
  }
  const notifyId = 'perm-del-' + Date.now();
  notifyProgress(notifyId, `Deleting ${toDelete.length} task(s)...`, '');
  let deleted = 0;
  for (const id of toDelete) {
    try {
      const res = await fetch(`${API}/api/todo/items/${id}/permanent`, { method: 'DELETE' });
      const data = await res.json();
      if (data.ok) deleted++;
    } catch (_) {}
  }
  selectedTodoIds.clear();
  lastClickedTodoId = null;
  await loadTodoItems(currentTodoFilter);
  updateNotify(notifyId, 'success', 'Deleted', `${deleted} task(s) permanently removed`);
  ctxMenu.style.display = 'none';
}

async function ctxPushToCalendar() {
  const toPush = [...selectedTodoIds];
  if (toPush.length === 0) { ctxMenu.style.display = 'none'; return; }

  // Validate all selected items have deadline_date AND deadline_time
  const missing = [];
  for (const id of toPush) {
    const item = _allTodoItems.find(i => i.entry_id === id);
    if (!item || !item.deadline_date || !item.deadline_time) {
      missing.push(item?.description?.substring(0, 50) || id.substring(0, 12));
    }
  }

  if (missing.length > 0) {
    alert(`Cannot push to calendar — ${missing.length} task(s) missing deadline date or time:\n\n${missing.join('\n')}\n\nSet both deadline date and time for all selected tasks before pushing.`);
    ctxMenu.style.display = 'none';
    return;
  }

  const notifyId = 'cal-' + Date.now();
  notifyProgress(notifyId, `Pushing ${toPush.length} task(s) to Outlook Calendar...`, '');
  try {
    const res = await fetch(`${API}/api/todo/push-to-calendar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ todo_ids: toPush }),
    });
    const data = await res.json();
    if (data.ok) {
      const succeeded = data.results.filter(r => r.ok).length;
      const failed = data.results.filter(r => !r.ok).length;
      let msg = `${succeeded} appointment(s) created`;
      if (failed > 0) msg += `, ${failed} failed`;
      updateNotify(notifyId, succeeded > 0 ? 'success' : 'error', 'Calendar', msg);
    } else if (data.missing) {
      alert(`Cannot push to calendar — ${data.missing.length} task(s) missing deadline date or time:\n\n${data.missing.map(m => m.description).join('\n')}`);
      dismissNotify(notifyId);
    } else {
      updateNotify(notifyId, 'error', 'Calendar', data.error || 'Push failed');
    }
  } catch (e) {
    updateNotify(notifyId, 'error', 'Calendar', e.message || '');
  }
  ctxMenu.style.display = 'none';
}

async function ctxCreateVariation() {
  if (selectedIds.size === 0) { ctxMenu.style.display = 'none'; return; }
  ctxMenu.style.display = 'none';

  const emailEntryId = [...selectedIds][0];  // Take first selected
  const email = emails.find(e => e.entry_id === emailEntryId);
  if (!email) { notify('Email not found', 'warning'); return; }

  const notifyId = 'var-create-' + Date.now();
  notifyProgress(notifyId, 'Creating variation...', 'Analyzing email content');

  try {
    const res = await fetch(`${API}/api/variations/from-email/${emailEntryId}`, { method: 'POST' });
    const data = await res.json();
    if (data.entry_id) {
      updateNotify(notifyId, 'success', 'Variation created!', 'Switch to Variations module to view');
      // Auto-switch to variations module
      setTimeout(() => switchModule('variations'), 1500);
    } else {
      updateNotify(notifyId, 'error', 'Failed', data.error || 'Could not create variation from email');
    }
  } catch (e) {
    updateNotify(notifyId, 'error', 'Failed', e.message);
  }
}

async function ctxPushToTodo() {
  const toPush = [...selectedIds];
  if (toPush.length === 0) { ctxMenu.style.display = 'none'; return; }

  const notifyId = 'push-' + Date.now();
  notifyProgress(notifyId, `Pushing ${toPush.length} email(s) to To-Do List...`, 'Extracting todos and deadlines');

  try {
    const res = await fetch(`${API}/api/todo/push-from-emails`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email_ids: toPush }),
    });
    const data = await res.json();
    if (data.ok) {
      const count = data.count || 0;
      updateNotify(notifyId, 'success', 'Pushed to To-Do List',
        `${count} task(s) created from ${toPush.length} email(s)`);
    } else {
      updateNotify(notifyId, 'error', 'Push failed', 'Could not create to-do items');
    }
  } catch (e) {
    updateNotify(notifyId, 'error', 'Push failed', e.message || '');
  }
  ctxMenu.style.display = 'none';
}

// ── Keyboard shortcuts ──────────────────────────────────────────────

document.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.key === 'r') { e.preventDefault(); refreshInbox(); }
  // Ctrl+A: select all
  if (e.ctrlKey && e.key === 'a' && document.activeElement === document.body) {
    e.preventDefault();
    selectedIds.clear();
    emails.forEach(em => selectedIds.add(em.entry_id));
    applySelection();
  }
  // T: push selected emails to To-Do List (only in AMail view, not in inputs)
  if (e.key === 't' && currentModule === 'amail' && selectedIds.size > 0
      && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)) {
    e.preventDefault();
    ctxPushToTodo();
  }
  // Ctrl+H: open reply panel in Outlook — reply to sender (same as double-click)
  if (e.ctrlKey && e.key === 'h' && currentModule === 'amail'
      && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)) {
    e.preventDefault();
    for (const id of selectedIds) openOutlook(id, true, false);
  }
  // Ctrl+Shift+H: reply ALL in Outlook
  if (e.ctrlKey && e.shiftKey && e.key === 'H' && currentModule === 'amail'
      && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)) {
    e.preventDefault();
    for (const id of selectedIds) openOutlook(id, false, true);
  }
  // Ctrl+F: flag emails in Outlook & push to To-Do List
  if (e.ctrlKey && e.key === 'f' && currentModule === 'amail' && selectedIds.size > 0
      && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)) {
    e.preventDefault();
    ctxFlagAndPush();
  }
  // Spacebar: dismiss selected cards (remove from UI + mark READ)
  if (e.key === ' ' && currentModule === 'amail' && selectedIds.size > 0
      && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)) {
    e.preventDefault();
    ctxDismissRead();
  }
  // Ctrl+D: download all attachments of the selected email
  if (e.ctrlKey && e.key === 'd' && currentModule === 'amail' && selectedId
      && _currentAttachments.length > 0
      && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)) {
    e.preventDefault();
    downloadAttachments(false);
  }
  // Enter: open selected email in Outlook (single-select only)
  if (e.key === 'Enter' && currentModule === 'amail' && selectedIds.size > 0
      && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)) {
    e.preventDefault();
    if (selectedIds.size > 1) {
      notify('Please select only one email to open in Outlook', 'warning');
    } else {
      openOutlook([...selectedIds][0]);
    }
  }
  // Ctrl+T: forward email in Outlook (single-select only, includes attachments)
  if (e.ctrlKey && e.key === 't' && currentModule === 'amail' && selectedIds.size > 0
      && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)) {
    e.preventDefault();
    if (selectedIds.size > 1) {
      notify('Please select only one email to forward', 'warning');
    } else {
      openOutlook([...selectedIds][0], false, false, true);
    }
  }
});

// Enter key on count inputs triggers fetch
document.getElementById('earlier-count').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') fetchEarlier();
});
document.getElementById('latest-count').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') fetchLatest();
});

// ── To-Do List ───────────────────────────────────────────────────────

async function loadTodoItems(status) {
  currentTodoFilter = status || null;
  try {
    let url = `${API}/api/todo/items?limit=0`;
    if (status) url += `&status=${status}`;
    const res = await fetch(url);
    const data = await res.json();
    _allTodoItems = data.items || [];
    applyTodoFilters();
  } catch (e) {
    notify('Failed to load to-do items', 'error');
  }
}

function onFilterChange() {
  // Read filter state from dropdowns
  todoFilters.deadline_date = document.getElementById('filter-deadline-date')?.value || '';
  todoFilters.deadline_type = document.getElementById('filter-deadline-type')?.value || '';
  todoFilters.category = document.getElementById('filter-category')?.value || '';
  todoFilters.urgency = document.getElementById('filter-urgency')?.value || '';
  applyTodoFilters();
}

function applyTodoFilters() {
  const today = new Date().toISOString().substring(0, 10);
  const weekEnd = new Date(Date.now() + 7*86400000).toISOString().substring(0, 10);

  todoItems = _allTodoItems.filter(item => {
    // ── deadline_date filter ──────────────────────────────────────
    const df = todoFilters.deadline_date;
    if (df === 'has' && !item.deadline_date) return false;
    if (df === 'none' && item.deadline_date) return false;
    if (df === 'week') {
      if (!item.deadline_date) return false;
      if (item.deadline_date < today || item.deadline_date > weekEnd) return false;
    }
    if (df === 'overdue') {
      if (!item.deadline_date) return false;
      if (item.deadline_date >= today) return false;
    }
    // ── deadline_type filter ──────────────────────────────────────
    if (todoFilters.deadline_type && item.deadline_type !== todoFilters.deadline_type) return false;
    // ── category filter ───────────────────────────────────────────
    if (todoFilters.category && item.category !== todoFilters.category) return false;
    // ── urgency filter ────────────────────────────────────────────
    if (todoFilters.urgency && item.urgency !== todoFilters.urgency) return false;
    return true;
  });
  renderTodoCards();
}

function renderTodoCards() {
  const container = document.getElementById('todo-cards-container');
  const empty = document.getElementById('todo-cards-empty');

  if (!container) return;
  if (todoItems.length === 0) {
    container.innerHTML = '';
    if (empty) empty.style.display = 'flex';
    return;
  }
  if (empty) empty.style.display = 'none';

  container.innerHTML = todoItems.map(item => {
    const deadline = item.deadline_date ? item.deadline_date.substring(0, 10) : '';
    const deadlineLabel = deadline ? `📅 ${deadline}` : '';
    const statusClass = item.status === 'done' ? 'todo-done' : (item.status === 'cancelled' ? 'todo-cancelled' : '');
    const urgencyClass = `urg-${item.urgency || 'low'}`;

    return `
      <div class="card todo-card ${statusClass}${selectedTodoIds.has(item.entry_id) ? ' selected' : ''}"
           id="todo-card-${item.entry_id}"
           onclick="selectTodoItem('${item.entry_id}', event)">
        <div class="cn" style="color:var(--${item.status === 'done' ? 'muted' : 'text'});">${esc(item.description || '(no description)')}</div>
        <div class="meta">
          <span class="category">📂 ${esc(item.category || 'General')}</span>
          <span class="urgency ${urgencyClass}">⚠️ ${esc(item.urgency || 'low')}</span>
          ${item.project ? `<span style="color:var(--green);font-size:11px;">🏗 ${esc(item.project)}</span>` : ''}
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:4px;">
          <span class="time">${deadlineLabel}</span>
          <span style="font-size:11px;color:var(--${item.status === 'done' ? 'green' : item.status === 'cancelled' ? 'red' : 'yellow'});">${item.status}</span>
        </div>
      </div>
    `;
  }).join('');

  // Update filter counts
  updateTodoCounts();
}

function updateTodoCounts() {
  // Always fetch real counts from backend — local todoItems only holds
  // the currently filtered slice (e.g. just cancelled items in Trash view).
  fetch(`${API}/api/todo/counts`)
    .then(r => r.json())
    .then(c => {
      setText('todo-count-all', String(c.all || 0));
      setText('todo-count-pending', String(c.pending || 0));
      setText('todo-count-done', String(c.done || 0));
      setText('todo-count-cancelled', String(c.cancelled || 0));
    })
    .catch(() => {});
}

// ── To-Do Multi-select ──────────────────────────────────────────────

function getTodoCardIndex(id) {
  return todoItems.findIndex(i => i.entry_id === id);
}

function selectTodoSingle(id) {
  selectedTodoIds.clear();
  selectedTodoIds.add(id);
  lastClickedTodoId = id;
  selectedTodoId = id;
  applyTodoSelection();
  renderTodoDetail(id);
}

function selectTodoRange(fromId, toId) {
  const a = getTodoCardIndex(fromId);
  const b = getTodoCardIndex(toId);
  if (a < 0 || b < 0) return;
  selectedTodoIds.clear();
  const [lo, hi] = a < b ? [a, b] : [b, a];
  for (let i = lo; i <= hi; i++) selectedTodoIds.add(todoItems[i].entry_id);
  lastClickedTodoId = toId;
  applyTodoSelection();
}

function toggleTodoSelect(id) {
  if (selectedTodoIds.has(id)) {
    selectedTodoIds.delete(id);
  } else {
    selectedTodoIds.add(id);
  }
  lastClickedTodoId = id;
  if (selectedTodoIds.size === 1) {
    selectedTodoId = [...selectedTodoIds][0];
    renderTodoDetail(selectedTodoId);
  }
  applyTodoSelection();
}

function selectTodoItem(entryId, event) {
  if (event?.shiftKey && lastClickedTodoId) {
    selectTodoRange(lastClickedTodoId, entryId);
  } else if (event?.ctrlKey || event?.metaKey) {
    toggleTodoSelect(entryId);
  } else {
    selectTodoSingle(entryId);
  }
}

function applyTodoSelection() {
  document.querySelectorAll('.todo-card').forEach(c => {
    const tid = c.id?.replace('todo-card-', '') || '';
    c.classList.toggle('selected', selectedTodoIds.has(tid));
  });
  // Fetch the real ALL count from backend for the header
  fetch(`${API}/api/todo/counts`)
    .then(r => r.json())
    .then(c => {
      const countEl = document.getElementById('todo-count-header');
      if (countEl) {
        countEl.textContent = `${c.all || 0} items` + (selectedTodoIds.size > 0 ? ` (${selectedTodoIds.size} selected)` : '');
      }
    })
    .catch(() => {
      const countEl = document.getElementById('todo-count-header');
      if (countEl) countEl.textContent = `${todoItems.length} items` + (selectedTodoIds.size > 0 ? ` (${selectedTodoIds.size} selected)` : '');
    });
}

// Click empty area in todo panel → deselect all
document.getElementById('todo-card-panel')?.addEventListener('click', (e) => {
  if (!e.target.closest('.todo-card')) {
    selectedTodoIds.clear();
    lastClickedTodoId = null;
    applyTodoSelection();
  }
});

function renderTodoDetail(entryId) {
  const item = todoItems.find(i => i.entry_id === entryId);
  if (!item) return;

  const detail = document.getElementById('todo-detail');
  const empty = document.getElementById('todo-detail-empty');
  if (detail) detail.style.display = 'flex';
  if (empty) empty.style.display = 'none';

  // Populate fields
  setText('todo-det-description', item.description);

  // Fetch source email summary from AMail
  if (item.source_email_id) {
    setText('todo-det-source', '📧 Loading source email...');
    fetch(`${API}/api/amail/emails/${item.source_email_id}`)
      .then(r => r.json())
      .then(email => {
        const cn = email.chinese_summary || email.subject || '';
        const summary = cn.length > 80 ? cn.substring(0, 80) + '...' : cn;
        setText('todo-det-source', cn ? `📧 ${summary}` : `📧 ${email.subject || '(no summary)'}`);
      })
      .catch(() => setText('todo-det-source', ''));
  } else {
    setText('todo-det-source', '');
  }

  // Editable inputs
  const descInput = document.getElementById('todo-edit-description');
  if (descInput) {
    descInput.value = item.description || '';
    descInput.onchange = () => updateTodoField(entryId, 'description', descInput.value);
  }

  setSelectValue('todo-edit-category', item.category || 'General');
  setSelectValue('todo-edit-urgency', item.urgency || 'low');
  setSelectValue('todo-edit-deadline-type', item.deadline_type || 'tbd');
  setSelectValue('todo-edit-status', item.status || 'pending');

  const dateInput = document.getElementById('todo-edit-deadline');
  if (dateInput) {
    dateInput.value = item.deadline_date ? item.deadline_date.substring(0, 10) : '';
    dateInput.onchange = () => {
      const newDate = dateInput.value || null;
      // Auto-set time to 12:00 when date is first set and no time exists
      const timeInput = document.getElementById('todo-edit-deadline-time');
      if (newDate && !item.deadline_time && timeInput) {
        timeInput.value = '12:00';
        updateTodoField(entryId, 'deadline_time', '12:00');
      }
      updateTodoField(entryId, 'deadline_date', newDate);
    };
  }

  const timeInput = document.getElementById('todo-edit-deadline-time');
  if (timeInput) {
    timeInput.value = item.deadline_time || '';
    timeInput.onchange = () => updateTodoField(entryId, 'deadline_time', timeInput.value || null);
  }

  const projectInput = document.getElementById('todo-edit-project');
  if (projectInput) {
    projectInput.value = item.project || '';
    projectInput.onchange = () => updateTodoField(entryId, 'project', projectInput.value);
  }

  const assigneeInput = document.getElementById('todo-edit-assignee');
  if (assigneeInput) {
    assigneeInput.value = item.assignee || '';
    assigneeInput.onchange = () => updateTodoField(entryId, 'assignee', assigneeInput.value);
  }

  // Source email link
  const sourceLink = document.getElementById('todo-source-link');
  if (sourceLink) {
    if (item.source_email_id) {
      sourceLink.style.display = 'block';
      sourceLink.onclick = () => navigateToSourceEmail(item.source_email_id);
    } else {
      sourceLink.style.display = 'none';
    }
  }

  // Wire select onchange handlers (do this after setting values)
  ['todo-edit-category', 'todo-edit-urgency', 'todo-edit-deadline-type', 'todo-edit-status'].forEach(selId => {
    const sel = document.getElementById(selId);
    if (sel && !sel._wired) {
      sel._wired = true;
      const fieldMap = {
        'todo-edit-category': 'category',
        'todo-edit-urgency': 'urgency',
        'todo-edit-deadline-type': 'deadline_type',
        'todo-edit-status': 'status',
      };
      sel.onchange = () => updateTodoField(entryId, fieldMap[selId], sel.value);
    }
  });
}

function setSelectValue(id, value) {
  const sel = document.getElementById(id);
  if (!sel) return;
  // Set value, fall back to first option
  for (const opt of sel.options) {
    if (opt.value === value) { sel.value = value; return; }
  }
  sel.value = sel.options[0]?.value || '';
}

async function updateTodoField(entryId, field, value) {
  // Debounce: 300ms per field per entry
  const timerKey = `${entryId}_${field}`;
  if (_todoDebounceTimers[timerKey]) clearTimeout(_todoDebounceTimers[timerKey]);
  _todoDebounceTimers[timerKey] = setTimeout(async () => {
    try {
      const res = await fetch(`${API}/api/todo/items/${entryId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [field]: value }),
      });
      const data = await res.json();
      if (data.ok) {
        // Update both filtered and raw arrays
        const idx = todoItems.findIndex(i => i.entry_id === entryId);
        const rawIdx = _allTodoItems.findIndex(i => i.entry_id === entryId);
        if (idx >= 0 && data.item) todoItems[idx] = data.item;
        if (rawIdx >= 0 && data.item) _allTodoItems[rawIdx] = data.item;
        renderTodoCards();
        // Show brief saved indicator
        const indicator = document.getElementById('todo-saved-indicator');
        if (indicator) {
          indicator.textContent = '✓ Saved';
          indicator.style.color = 'var(--green)';
          setTimeout(() => { indicator.textContent = ''; }, 1500);
        }
      }
    } catch (e) {
      notify('Save failed', 'error');
    }
  }, 300);
}

function navigateToSourceEmail(emailEntryId) {
  switchModule('amail');
  // Wait a tick for the DOM, then select
  setTimeout(() => {
    const found = emails.find(e => e.entry_id === emailEntryId);
    if (found) {
      selectSingle(emailEntryId);
    } else {
      notify('Source email not found in current view — it may have been removed or is in a different sort', 'warning', 5000);
    }
  }, 100);
}

// Debounce utility
function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

// ── Variations Module ────────────────────────────────────────────────

let variations = [];
let projects = [];
let selectedProjectId = null;
let selectedVarId = null;
let selectedVarIds = new Set();   // multi-select
let lastClickedVarId = null;       // for Shift+Click range
let currentVarFilter = null;       // null=All | 'draft'|'submitted'|'approved'|'void'
let _varApprovalType = 'client';   // 'bank' | 'client' — which column in Internal Register
let _currentVoNumber = null;       // vo_number of the currently selected variation
let _varSaveTimer = null;

// ── Project management ──────────────────────────────────────────────

async function loadProjects() {
  try {
    const res = await fetch(`${API}/api/projects`);
    const data = await res.json();
    projects = data.projects || [];
    renderProjectSelector();
  } catch (e) {
    notify('Failed to load projects', 'error');
  }
}

function renderProjectSelector() {
  const sel = document.getElementById('var-project-select');
  if (!sel) return;
  const currentVal = sel.value;
  sel.innerHTML = '<option value="">-- Select Project --</option>' +
    projects.map(p => `<option value="${p.entry_id}">${esc(p.name || 'Unnamed')} (${p.vo_count || 0} VOs)</option>`).join('');
  // Restore selection
  if (currentVal && projects.find(p => p.entry_id === currentVal)) {
    sel.value = currentVal;
  }
}

async function varSwitchProject() {
  const sel = document.getElementById('var-project-select');
  selectedProjectId = sel?.value || null;
  if (selectedProjectId) {
    document.getElementById('btn-var-push').disabled = false;
    document.getElementById('btn-var-config').disabled = false;
    document.getElementById('btn-var-delete-proj').disabled = false;
    await loadVariations();
    await loadVarRegisters();
  } else {
    document.getElementById('btn-var-push').disabled = true;
    document.getElementById('btn-var-config').disabled = true;
    document.getElementById('btn-var-delete-proj').disabled = true;
    variations = [];
    renderVarCards();
    document.getElementById('var-register-section').style.display = 'none';
  }
}

function varNewProject() {
  document.getElementById('var-new-project-modal').classList.add('show');
}

async function varCreateProject() {
  const name = document.getElementById('var-new-proj-name')?.value || '';
  if (!name) { notify('Project name is required', 'warning'); return; }
  try {
    // Build xlsx path from folder + filename
    const folder = document.getElementById('var-new-proj-path')?.value || 'data/projects';
    const filename = document.getElementById('var-new-proj-filename')?.value || name.replace(/[^a-zA-Z0-9]/g, '_');
    const xlsxPath = `${folder}/${filename}.xlsx`;

    const res = await fetch(`${API}/api/projects`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name,
        job_number: document.getElementById('var-new-proj-job')?.value || '',
        location: document.getElementById('var-new-proj-loc')?.value || '',
        base_contract_amount: parseFloat(document.getElementById('var-new-proj-base')?.value) || 0,
        xlsx_path: xlsxPath,
      }),
    });
    const data = await res.json();
    if (data.entry_id) {
      document.getElementById('var-new-project-modal').classList.remove('show');

      // Auto-create VO1
      const job = document.getElementById('var-new-proj-job')?.value || '';
      const loc = document.getElementById('var-new-proj-loc')?.value || '';
      const base = parseFloat(document.getElementById('var-new-proj-base')?.value) || 0;
      await fetch(`${API}/api/variations`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          project_entry_id: data.entry_id,
          project_name: name,
          project_location: loc,
          job_number: job,
          base_contract_amount: base,
          vo_number: 1,
          vo_title: `VO1 - `,
          date_issued: new Date().toISOString().substring(0, 10),
        }),
      });

      await loadProjects();
      document.getElementById('var-project-select').value = data.entry_id;
      await varSwitchProject();
      notify('Project created with VO1', 'success');
    }
  } catch (e) { notify('Failed to create project', 'error'); }
}

async function varBrowsePath() {
  try {
    const handle = await window.showDirectoryPicker({ mode: 'read' });
    document.getElementById('var-new-proj-path').value = handle.name;
  } catch (e) {
    if (e.name !== 'AbortError') {
      notify('Folder picker not supported. Please type the path manually.', 'warning', 4000);
    }
  }
}

async function varBrowseConfigPath() {
  try {
    const handle = await window.showDirectoryPicker({ mode: 'read' });
    document.getElementById('var-config-folder').value = handle.name;
  } catch (e) {
    if (e.name !== 'AbortError') {
      notify('Folder picker not supported. Please type the path manually.', 'warning', 4000);
    }
  }
}

function varConfigProject() {
  if (!selectedProjectId) return;
  const proj = projects.find(p => p.entry_id === selectedProjectId);
  if (!proj) return;
  document.getElementById('var-config-name').value = proj.name || '';
  document.getElementById('var-config-job').value = proj.job_number || '';
  document.getElementById('var-config-loc').value = proj.location || '';
  document.getElementById('var-config-base').value = proj.base_contract_amount || 0;

  // Parse existing xlsx_path into folder + filename
  const xlsxPath = proj.xlsx_path || '';
  const lastSlash = xlsxPath.lastIndexOf('/');
  if (lastSlash >= 0) {
    document.getElementById('var-config-folder').value = xlsxPath.substring(0, lastSlash);
    const fname = xlsxPath.substring(lastSlash + 1);
    document.getElementById('var-config-filename').value = fname.replace(/\.xlsx$/, '');
  } else {
    document.getElementById('var-config-folder').value = 'data/projects';
    document.getElementById('var-config-filename').value = xlsxPath.replace(/\.xlsx$/, '') || 'Project_Client_Variations';
  }

  document.getElementById('var-config-modal').classList.add('show');
}

async function varSaveConfig() {
  if (!selectedProjectId) return;
  const folder = document.getElementById('var-config-folder')?.value || 'data/projects';
  const filename = document.getElementById('var-config-filename')?.value || 'Project_Client_Variations';
  const fields = {
    name: document.getElementById('var-config-name')?.value || '',
    job_number: document.getElementById('var-config-job')?.value || '',
    location: document.getElementById('var-config-loc')?.value || '',
    base_contract_amount: parseFloat(document.getElementById('var-config-base')?.value) || 0,
    xlsx_path: `${folder}/${filename}.xlsx`,
  };
  try {
    await fetch(`${API}/api/projects/${selectedProjectId}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(fields),
    });
    document.getElementById('var-config-modal').classList.remove('show');
    await loadProjects();
    // Update the currently displayed VO detail if any
    if (selectedVarId) {
      document.getElementById('var-edit-project').value = fields.name;
      document.getElementById('var-edit-job').value = fields.job_number;
      document.getElementById('var-edit-location').value = fields.location;
      document.getElementById('var-edit-base').value = fields.base_contract_amount;
    }
    await loadVarRegisters();
    notify('Project config saved', 'success');
  } catch (e) {
    notify('Failed to save config', 'error');
  }
}

function varImportProject() {
  document.getElementById('var-import-modal').classList.add('show');
}

async function varDeleteProject() {
  if (!selectedProjectId) return;
  const proj = projects.find(p => p.entry_id === selectedProjectId);
  const name = proj?.name || 'this project';
  if (!confirm(`Delete project "${name}" and ALL its VOs?\n\nThis will permanently remove the project and all variations from the database. The xlsx file on disk will NOT be deleted.\n\nThis cannot be undone.`)) return;

  const notifyId = 'del-proj-' + Date.now();
  notifyProgress(notifyId, 'Deleting project...', name);
  try {
    await fetch(`${API}/api/projects/${selectedProjectId}`, { method: 'DELETE' });
    selectedProjectId = null;
    document.getElementById('var-detail').style.display = 'none';
    document.getElementById('var-detail-empty').style.display = 'flex';
    document.getElementById('var-register-section').style.display = 'none';
    document.getElementById('btn-var-push').disabled = true;
    document.getElementById('btn-var-config').disabled = true;
    document.getElementById('btn-var-delete-proj').disabled = true;
    await loadProjects();
    document.getElementById('var-project-select').value = '';
    variations = [];
    renderVarCards();
    updateNotify(notifyId, 'success', 'Deleted', `Project "${name}" removed`);
  } catch (e) {
    updateNotify(notifyId, 'error', 'Failed', e.message);
  }
}

async function varDoImport() {
  const fileInput = document.getElementById('var-import-file');
  const file = fileInput?.files?.[0];
  if (!file) { notify('Please select an xlsx file', 'warning'); return; }
  document.getElementById('var-import-modal').classList.remove('show');
  const notifyId = 'import-' + Date.now();
  notifyProgress(notifyId, 'Uploading...', file.name);

  try {
    // Upload the file
    const formData = new FormData();
    formData.append('file', file);
    const uploadRes = await fetch(`${API}/api/projects/import-upload`, {
      method: 'POST',
      body: formData,
    });
    const uploadData = await uploadRes.json();

    if (uploadData.entry_id) {
      await loadProjects();
      fileInput.value = '';  // reset
      document.getElementById('var-project-select').value = uploadData.entry_id;
      await varSwitchProject();
      updateNotify(notifyId, 'success', 'Imported', `${variations.length} VOs imported from ${file.name}`);
    } else {
      updateNotify(notifyId, 'error', 'Failed', uploadData.error || 'Import failed');
    }
  } catch (e) { updateNotify(notifyId, 'error', 'Failed', e.message); }
}

// ── PUSH ─────────────────────────────────────────────────────────────

function varPush() {
  if (!selectedProjectId) return;
  const proj = projects.find(p => p.entry_id === selectedProjectId);
  if (proj?.xlsx_path) {
    document.getElementById('var-push-info').innerHTML = `
      <strong>Project:</strong> ${esc(proj.name)}<br>
      <strong>Save to:</strong> ${esc(proj.xlsx_path)}<br>
      <strong>VOs to compile:</strong> ${variations.length}<br>
      <span style="font-size:11px;color:var(--muted);">A timestamped backup will be created first.</span>
    `;
  }
  document.getElementById('var-push-modal').classList.add('show');
}

async function varDoPush() {
  if (!selectedProjectId) return;
  document.getElementById('var-push-modal').classList.remove('show');
  const notifyId = 'push-' + Date.now();
  notifyProgress(notifyId, 'Pushing...', 'Compiling xlsx with backup');
  try {
    await fetch(`${API}/api/projects/${selectedProjectId}/push`, { method: 'POST' });
    // Poll for completion
    const poll = setInterval(async () => {
      const r = await fetch(`${API}/api/projects/${selectedProjectId}`);
      const p = await r.json();
      if (p.updated_at) {
        clearInterval(poll);
        updateNotify(notifyId, 'success', 'Pushed!', `xlsx compiled to ${p.xlsx_path || 'file'}`);
      }
    }, 2000);
    setTimeout(() => clearInterval(poll), 120000);
  } catch (e) { updateNotify(notifyId, 'error', 'Failed', e.message); }
}

// ── Register cards ───────────────────────────────────────────────────

async function loadVarRegisters() {
  if (!selectedProjectId) return;
  document.getElementById('var-register-section').style.display = 'block';

  // Register
  try {
    const r = await fetch(`${API}/api/projects/${selectedProjectId}/register`);
    const reg = await r.json();
    document.getElementById('var-reg-count').textContent = `${reg.rows?.length || 0} VOs`;
    if (reg.rows?.length) {
      let html = '<table style=\"width:100%;font-size:10px;border-collapse:collapse;\">';
      html += '<tr style=\"color:var(--muted);\"><th style=\"text-align:left;padding:2px;\">#</th><th style=\"text-align:left;\">Description</th><th style=\"text-align:right;\">Value</th><th>Status</th></tr>';
      for (const row of reg.rows) {
        html += `<tr style=\"border-top:1px solid #1a1a2e;\">
          <td style=\"padding:3px;\">${row.vo_number || ''}</td>
          <td style=\"padding:3px;\">${esc((row.description || '').substring(0, 25))}</td>
          <td style=\"padding:3px;text-align:right;\">$${(row.variation_value || 0).toLocaleString(undefined, {minimumFractionDigits: 0})}</td>
          <td style=\"padding:3px;text-align:center;\">${row.status}</td>
        </tr>`;
      }
      // Totals
      const t = reg.totals || {};
      html += `<tr style=\"border-top:2px solid var(--overlay);font-weight:600;\">
        <td colspan=\"2\" style=\"padding:3px;\">TOTALS</td>
        <td style=\"padding:3px;text-align:right;\">$${(t.variation_value || 0).toLocaleString()}</td>
        <td></td></tr>`;
      html += `<tr style=\"font-size:10px;color:var(--muted);\">
        <td colspan=\"4\" style=\"padding:3px;\">Base Contract: $${(reg.project?.base_contract_amount || 0).toLocaleString()} | Projected: $${(reg.project?.projected_total || 0).toLocaleString()}</td></tr>`;
      html += '</table>';
      document.getElementById('var-register-table').innerHTML = html;
    }
  } catch (_) {}

  // Internal Register
  try {
    const r = await fetch(`${API}/api/projects/${selectedProjectId}/internal-register`);
    const ireg = await r.json();
    document.getElementById('var-ireg-count').textContent = `${ireg.rows?.length || 0} VOs`;
    if (ireg.rows?.length) {
      let html = '<table style=\"width:100%;font-size:10px;border-collapse:collapse;\">';
      html += '<tr style=\"color:var(--muted);\"><th style=\"text-align:left;padding:2px;\">#</th><th style=\"text-align:left;\">Description</th><th style=\"text-align:right;\">Value</th><th style=\"text-align:right;\">Bank</th><th style=\"text-align:right;\">Client</th><th style=\"text-align:right;\">Pending</th></tr>';
      for (const row of ireg.rows) {
        html += `<tr style=\"border-top:1px solid #1a1a2e;\">
          <td style=\"padding:3px;\">${row.seq || ''}</td>
          <td style=\"padding:3px;\">${esc((row.description || '').substring(0, 20))}</td>
          <td style=\"padding:3px;text-align:right;\">$${(row.variation_value || 0).toLocaleString()}</td>
          <td style=\"padding:3px;text-align:right;\">$${(row.bank_approved || 0).toLocaleString()}</td>
          <td style=\"padding:3px;text-align:right;\">$${(row.client_approved || 0).toLocaleString()}</td>
          <td style=\"padding:3px;text-align:right;\">$${(row.pending || 0).toLocaleString()}</td>
        </tr>`;
      }
      const t = ireg.totals || {};
      html += `<tr style=\"border-top:2px solid var(--overlay);font-weight:600;\">
        <td colspan=\"2\" style=\"padding:3px;\">TOTALS</td>
        <td style=\"padding:3px;text-align:right;\">$${(t.variation_value || 0).toLocaleString()}</td>
        <td style=\"padding:3px;text-align:right;\">$${(t.bank_approved || 0).toLocaleString()}</td>
        <td style=\"padding:3px;text-align:right;\">$${(t.client_approved || 0).toLocaleString()}</td>
        <td style=\"padding:3px;text-align:right;\">$${(t.pending || 0).toLocaleString()}</td></tr>`;
      html += '</table>';
      document.getElementById('var-iregister-table').innerHTML = html;
    }
  } catch (_) {}
}

function varToggleRegister() {
  const tbl = document.getElementById('var-register-table');
  tbl.style.display = tbl.style.display === 'none' ? 'block' : 'none';
}

function varToggleInternalRegister() {
  const tbl = document.getElementById('var-iregister-table');
  tbl.style.display = tbl.style.display === 'none' ? 'block' : 'none';
}

// ── Variation loading ────────────────────────────────────────────────

async function loadVariations(status) {
  currentVarFilter = status || null;
  if (!selectedProjectId) {
    variations = [];
    renderVarCards();
    return;
  }
  try {
    const params = new URLSearchParams();
    params.set('project_entry_id', selectedProjectId);
    if (status) params.set('status', status);
    const res = await fetch(`${API}/api/variations?${params}`);
    const data = await res.json();
    variations = data.variations || [];

    // Fetch counts for filter buttons
    const counts = {};
    for (const s of ['draft', 'submitted', 'approved', 'void']) {
      try {
        const r = await fetch(`${API}/api/variations?project_entry_id=${selectedProjectId}&status=${s}`);
        const d = await r.json();
        counts[s] = d.count || 0;
      } catch (_) { counts[s] = 0; }
    }
    counts.all = 0;
    try {
      const rAll = await fetch(`${API}/api/variations?project_entry_id=${selectedProjectId}`);
      const dAll = await rAll.json();
      counts.all = dAll.count || 0;
    } catch (_) {}

    ['all', 'draft', 'submitted', 'approved', 'void'].forEach(s => {
      const el = document.getElementById(`var-count-${s}`);
      if (el) el.textContent = counts[s] ?? 0;
    });

    // Highlight active filter
    document.querySelectorAll('#var-card-panel .btn[id^=\"var-btn-\"]').forEach(b => {
      b.style.background = ''; b.style.color = ''; b.style.fontWeight = '';
    });
    const activeBtnId = status ? `var-btn-${status}` : 'var-btn-all';
    const activeBtn = document.getElementById(activeBtnId);
    if (activeBtn) {
      activeBtn.style.background = 'var(--blue)';
      activeBtn.style.color = 'var(--base)';
      activeBtn.style.fontWeight = '600';
    }

    document.getElementById('var-count-header').textContent = `${variations.length} VOs`;
    selectedVarIds.clear();
    lastClickedVarId = null;
    selectedVarId = null;
    document.getElementById('var-detail').style.display = 'none';
    document.getElementById('var-detail-empty').style.display = 'flex';
    renderVarCards();
  } catch (e) {
    notify('Failed to load variations', 'error');
  }
}

// Deselect variations on empty-area click
document.getElementById('var-card-panel')?.addEventListener('click', (e) => {
  if (!e.target.closest('.card')) {
    selectedVarIds.clear();
    lastClickedVarId = null;
    applyVarSelection();
  }
});

// ── Variation multi-select ────────────────────────────────────────

function selectVarSingle(id) {
  selectedVarIds.clear();
  selectedVarIds.add(id);
  lastClickedVarId = id;
  applyVarSelection();
}

function selectVarRange(fromId, toId) {
  const fromIdx = variations.findIndex(v => v.entry_id === fromId);
  const toIdx = variations.findIndex(v => v.entry_id === toId);
  if (fromIdx < 0 || toIdx < 0) return;
  const lo = Math.min(fromIdx, toIdx);
  const hi = Math.max(fromIdx, toIdx);
  selectedVarIds.clear();
  for (let i = lo; i <= hi; i++) selectedVarIds.add(variations[i].entry_id);
  lastClickedVarId = toId;
  applyVarSelection();
}

function selectVarToggle(id) {
  if (selectedVarIds.has(id)) {
    selectedVarIds.delete(id);
  } else {
    selectedVarIds.add(id);
  }
  lastClickedVarId = id;
  applyVarSelection();
}

function applyVarSelection() {
  document.querySelectorAll('#var-cards-container .card').forEach(c => {
    const vid = c.id?.replace('varcard-', '') || '';
    c.classList.toggle('selected', selectedVarIds.has(vid));
  });
  const countEl = document.getElementById('var-count-header');
  if (countEl) {
    countEl.textContent = `${variations.length} VOs` + (selectedVarIds.size > 0 ? ` (${selectedVarIds.size} selected)` : '');
  }
}

function selectVariation(entryId, event) {
  if (event?.shiftKey && lastClickedVarId) {
    selectVarRange(lastClickedVarId, entryId);
  } else if (event?.ctrlKey || event?.metaKey) {
    selectVarToggle(entryId);
  } else {
    selectedVarIds.clear();
    selectedVarIds.add(entryId);
    lastClickedVarId = entryId;
    applyVarSelection();
  }

  selectedVarId = entryId;
  if (selectedVarIds.size === 1) {
    loadVarDetail(entryId);
  }
}

function renderVarCards() {
  const container = document.getElementById('var-cards-container');
  const empty = document.getElementById('var-cards-empty');

  if (variations.length === 0) {
    container.innerHTML = '';
    empty.style.display = 'flex';
    return;
  }
  empty.style.display = 'none';

  const statusColors = {draft:'var(--muted)', submitted:'var(--blue)', approved:'var(--green)', approved_for_signing:'var(--yellow)', not_approved:'var(--red)', void:'var(--red)'};
  const statusIcons = {draft:'📝', submitted:'📤', approved:'✅', approved_for_signing:'✍️', not_approved:'🚫', void:'❌'};
  const statusNames = {draft:'Draft', submitted:'Submitted', approved:'Approved', approved_for_signing:'Appr. for Signing', not_approved:'Not Approved', void:'Void'};

  container.innerHTML = variations.map(v => {
    const total = v.totals?.total_incl_gst || 0;
    const st = v.status || 'draft';
    return `
      <div class="card${selectedVarIds.has(v.entry_id) ? ' selected' : ''}"
           id="varcard-${v.entry_id}"
           draggable="true"
           ondragstart="varDragStart(event, '${v.entry_id}')"
           ondragover="varDragOver(event)"
           ondragleave="varDragLeave(event)"
           ondrop="varDrop(event, '${v.entry_id}')"
           ondragend="varDragEnd(event)"
           onclick="selectVariation('${v.entry_id}', event)">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <span style="display:flex;align-items:center;gap:4px;">
            <span class="var-drag-handle" style="cursor:grab;color:var(--muted);font-size:14px;user-select:none;" title="Drag to reorder">⋮⋮</span>
            <span style="color:var(--blue);font-weight:600;">${esc(v.vo_title || `VO${v.vo_number || '?'}`)}</span>
          </span>
          <span style="font-size:11px;color:${statusColors[st] || 'var(--muted)'};">${statusIcons[st] || ''} ${statusNames[st] || st}</span>
        </div>
        <div style="font-size:12px;color:var(--subtext);margin-top:4px;">🏗 ${esc(v.project_name || 'No project')}</div>
        <div style="font-size:12px;color:var(--green);margin-top:2px;">💰 $${total.toLocaleString(undefined, {minimumFractionDigits: 2})}</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px;">📦 ${v.item_count || 0} items · ${esc(v.vo_type || '')}</div>
      </div>
    `;
  }).join('');
}

async function loadVarDetail(entryId) {
  try {
    const res = await fetch(`${API}/api/variations/${entryId}`);
    const v = await res.json();
    if (v.error) { notify(v.error, 'error'); return; }

    document.getElementById('var-detail-empty').style.display = 'none';
    document.getElementById('var-detail').style.display = 'flex';

    document.getElementById('var-edit-title').value = v.vo_title || '';
    document.getElementById('var-edit-status').value = v.status || 'draft';
    _currentVoNumber = v.vo_number || null;

    // Project-level fields (global, from project config)
    const proj = projects.find(p => p.entry_id === selectedProjectId);
    document.getElementById('var-edit-project').value = proj?.name || '';
    document.getElementById('var-edit-job').value = proj?.job_number || '';
    document.getElementById('var-edit-location').value = proj?.location || '';
    document.getElementById('var-edit-date').value = (v.date_issued || '').substring(0, 10);
    document.getElementById('var-edit-base').value = proj?.base_contract_amount || 0;

    // Radio
    const radios = document.getElementsByName('var-vo-type');
    radios.forEach(r => { if (r.value === (v.vo_type || 'Head Contract VO')) r.checked = true; });
    // Approval values
    const status = v.status || 'submitted';
    const showApproval = ['approved', 'approved_for_signing', 'not_approved'].includes(status);
    const panel = document.getElementById('var-approval-values');
    if (panel) panel.style.display = showApproval ? 'grid' : 'none';
    if (showApproval) {
      document.getElementById('var-edit-approved-val').value = v.approved_value || 0;
      document.getElementById('var-edit-notapproved-val').value = v.not_approved_value || 0;
      _varApprovalType = v.approval_type || 'client';
      varSetApprovalType(_varApprovalType);
    }

    // Items
    renderVarItems(v.items || []);
    updateVarTotals(v.items || []);

    // Toggle delete/restore buttons based on active filter
    const isVoid = currentVarFilter === 'void';
    document.getElementById('var-btn-delete').textContent = isVoid ? '⛔ Permanently Delete' : '🗑 Delete';
    document.getElementById('var-btn-delete').style.background = isVoid ? 'var(--red)' : '';
    document.getElementById('var-btn-delete').style.color = isVoid ? 'var(--base)' : '';
    document.getElementById('var-btn-restore').style.display = isVoid ? '' : 'none';

    // Disable editing in void view
    const editFields = ['var-edit-title', 'var-edit-status', 'var-edit-project', 'var-edit-job',
                        'var-edit-location', 'var-edit-date', 'var-edit-base'];
    editFields.forEach(fid => {
      const el = document.getElementById(fid);
      if (el) el.disabled = isVoid;
    });

    // Pre-fill email subject
    const job = v.job_number || '';
    const title = v.vo_title || `VO${v.vo_number || ''}`;
    document.getElementById('var-email-subject').value = `${job} - Variation Submission - ${title}`;

  } catch (e) {
    notify('Failed to load variation', 'error');
  }
}

// ── Items rendering ──────────────────────────────────────────────────

function renderVarItems(items) {
  const container = document.getElementById('var-items-rows');
  if (!items.length) {
    container.innerHTML = '<div style="font-size:12px;color:var(--muted);padding:8px;">No items yet — click "+ Add Item"</div>';
    return;
  }
  container.innerHTML = items.map((it, i) => {
    const cost = (it.qty || 0) * (it.rate || 0);
    return `
    <div style="display:grid;grid-template-columns:40px 1fr 60px 55px 80px 80px 70px 35px;gap:4px;padding:4px 0;align-items:center;border-bottom:1px solid #1a1a2e;">
      <span style="font-size:12px;color:var(--muted);text-align:center;">${i+1}</span>
      <input type="text" value="${esc(it.description || '')}" onchange="varItemUpdate(${it.id}, 'description', this.value)"
        style="background:var(--base);border:1px solid var(--overlay);border-radius:4px;padding:4px 6px;color:var(--text);font-size:12px;width:100%;">
      <input type="number" value="${it.qty || 0}" onchange="varItemQtyRateChanged(${it.id}, 'qty', this)"
        style="background:var(--base);border:1px solid var(--overlay);border-radius:4px;padding:4px 6px;color:var(--text);font-size:12px;width:100%;text-align:center;" step="any">
      <input type="text" value="${esc(it.unit || 'item')}" onchange="varItemUpdate(${it.id}, 'unit', this.value)"
        style="background:var(--base);border:1px solid var(--overlay);border-radius:4px;padding:4px 6px;color:var(--text);font-size:12px;width:100%;">
      <input type="number" value="${it.rate || 0}" onchange="varItemQtyRateChanged(${it.id}, 'rate', this)"
        style="background:var(--base);border:1px solid var(--overlay);border-radius:4px;padding:4px 6px;color:var(--text);font-size:12px;width:100%;text-align:right;" step="any">
      <span class="var-item-cost" style="font-size:12px;color:var(--green);text-align:right;padding-right:4px;">$${cost.toFixed(2)}</span>
      <input type="number" value="${it.credit || 0}" onchange="varItemUpdate(${it.id}, 'credit', this.value);varRecalc();"
        style="background:var(--base);border:1px solid var(--overlay);border-radius:4px;padding:4px 6px;color:var(--orange);font-size:12px;width:100%;text-align:right;" step="any">
      <button onclick="varRemoveItem(${it.id})" style="background:none;border:none;color:var(--red);cursor:pointer;font-size:14px;padding:2px;" title="Remove">×</button>
    </div>
  `;}).join('');
}

function updateVarTotals(items) {
  const doc = document;
  if (!items || !items.length) {
    doc.getElementById('var-totals').style.display = 'none';
    return;
  }
  doc.getElementById('var-totals').style.display = 'block';
  // Match the Excel template formulas:
  //   Sub Total = Σ(qty × rate)
  //   Credits = Σ(credit)
  //   Nett = Sub - Credits
  //   Margin = Nett × 10%
  //   Excl GST = Nett + Margin
  //   GST = Excl GST × 10%
  //   Total = Excl GST + GST
  const sub = items.reduce((s,i) => s + (i.qty||0)*(i.rate||0), 0);
  const credits = items.reduce((s,i) => s + (i.credit||0), 0);
  const nett = sub - credits;
  const margin = nett * 0.10;
  const excl = nett + margin;
  const gst = excl * 0.10;
  const total = excl + gst;

  const fmt = n => n.toLocaleString(undefined, {minimumFractionDigits: 2});
  doc.getElementById('var-subtotal').textContent = fmt(sub);
  doc.getElementById('var-credits').textContent = fmt(credits);
  doc.getElementById('var-nett').textContent = fmt(nett);
  doc.getElementById('var-margin').textContent = fmt(margin);
  doc.getElementById('var-excl').textContent = fmt(excl);
  doc.getElementById('var-gst').textContent = fmt(gst);
  doc.getElementById('var-total').textContent = fmt(total);
}

// ── Item CRUD ────────────────────────────────────────────────────────

async function varAddItem() {
  if (!selectedVarId) return;
  // Count actual item rows, excluding the "no items" placeholder
  const rows = document.querySelectorAll('#var-items-rows > div');
  const nextNum = rows.length + 1;
  try {
    const res = await fetch(`${API}/api/variations/${selectedVarId}/items`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({item_number: nextNum, description: '', qty: 0, unit: 'item', rate: 0}),
    });
    const data = await res.json();
    if (data.ok) {
      await loadVarDetail(selectedVarId);
    }
  } catch (e) {}
}

async function varItemQtyRateChanged(itemId, field, inputEl) {
  if (!selectedVarId) return;
  const value = parseFloat(inputEl.value) || 0;

  // Find the sibling inputs for qty and rate
  const row = inputEl.closest('div');
  const inputs = row.querySelectorAll('input[type="number"]');
  const qtyInput = inputs[0];   // first number input is qty
  const rateInput = inputs[1];  // second number input is rate
  const creditInput = inputs[2]; // third is credit

  const qty = field === 'qty' ? value : (parseFloat(qtyInput?.value) || 0);
  const rate = field === 'rate' ? value : (parseFloat(rateInput?.value) || 0);
  const cost = qty * rate;

  // Update the cost display in the same row
  const costSpan = row.querySelector('.var-item-cost');
  if (costSpan) costSpan.textContent = `$${cost.toFixed(2)}`;

  // Persist: send qty/rate AND the computed cost
  try {
    await fetch(`${API}/api/variations/${selectedVarId}/items/${itemId}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ [field]: value, cost: cost }),
    });
    varRecalc();
    loadVarRegisters();  // update Project Summary
  } catch (e) {}
}

async function varItemUpdate(itemId, field, value) {
  if (!selectedVarId) return;
  const body = {};
  body[field] = ['qty','rate','cost','credit'].includes(field) ? parseFloat(value) || 0 : value;
  try {
    await fetch(`${API}/api/variations/${selectedVarId}/items/${itemId}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (field === 'credit') { varRecalc(); loadVarRegisters(); }
  } catch (e) {}
}

async function varRemoveItem(itemId) {
  if (!selectedVarId) return;
  try {
    await fetch(`${API}/api/variations/${selectedVarId}/items/${itemId}`, { method: 'DELETE' });
    await loadVarDetail(selectedVarId);
    loadVarRegisters();  // update Project Summary
  } catch (e) {}
}

// ── Live recalculation ───────────────────────────────────────────────

function varRecalc() {
  const rows = document.querySelectorAll('#var-items-rows > div');
  const items = [];
  rows.forEach(r => {
    const inputs = r.querySelectorAll('input[type="number"]');
    const textInputs = r.querySelectorAll('input[type="text"]');
    if (inputs.length >= 3) {
      const qty = parseFloat(inputs[0]?.value) || 0;
      const rate = parseFloat(inputs[1]?.value) || 0;
      const credit = parseFloat(inputs[2]?.value) || 0;
      const desc = textInputs[0]?.value || '';
      const unit = textInputs[1]?.value || 'item';
      items.push({description: desc, qty, unit, rate, credit});
    }
  });
  updateVarTotals(items);
}

// ── Create / Auto-save ───────────────────────────────────────────────

async function varNew() {
  if (!selectedProjectId) { notify('Select a project first', 'warning'); return; }
  const proj = projects.find(p => p.entry_id === selectedProjectId);

  // Get next VO number from server (count of active VOs + 1)
  let nextVo = 1;
  try {
    const r = await fetch(`${API}/api/variations/next-vo-number?project_entry_id=${selectedProjectId}`);
    const d = await r.json();
    nextVo = d.vo_number || 1;
  } catch (_) {}
  const autoTitle = `VO${nextVo} - `;
  const today = new Date().toISOString().substring(0, 10);

  // Put new VO at bottom (max sort_order + 1)
  const maxSort = variations.reduce((max, v) => Math.max(max, v.sort_order || 0), 0);

  try {
    const res = await fetch(`${API}/api/variations`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        project_entry_id: selectedProjectId,
        project_name: proj?.name || '',
        project_location: proj?.location || '',
        job_number: proj?.job_number || '',
        base_contract_amount: proj?.base_contract_amount || 0,
        vo_number: nextVo,
        vo_title: autoTitle,
        date_issued: today,
        sort_order: maxSort + 1,
      }),
    });
    const data = await res.json();
    if (data.entry_id) {
      await loadVariations(currentVarFilter);
      await loadVarRegisters();
      selectVariation(data.entry_id);
      notify(`VO${nextVo} created`, 'success');
    }
  } catch (e) {
    notify('Failed to create variation', 'error');
  }
}

async function varSave() {
  if (!selectedVarId) return;
  await _varPersistFields();
}

// ── Status change → show/hide approval values ──────────────────────

function varStatusChanged() {
  const status = document.getElementById('var-edit-status')?.value || '';
  const panel = document.getElementById('var-approval-values');
  const showApproval = ['approved', 'approved_for_signing', 'not_approved'].includes(status);

  if (panel) {
    panel.style.display = showApproval ? 'grid' : 'none';
  }

  if (showApproval) {
    // Get total calculated cost from line items
    const total = _varCalcTotalFromItems();
    const approvedEl = document.getElementById('var-edit-approved-val');
    const notApprovedEl = document.getElementById('var-edit-notapproved-val');

    if (status === 'approved' || status === 'approved_for_signing') {
      if (approvedEl) approvedEl.value = total.toFixed(2);
      if (notApprovedEl) notApprovedEl.value = '0.00';
    } else if (status === 'not_approved') {
      if (approvedEl) approvedEl.value = '0.00';
      if (notApprovedEl) notApprovedEl.value = total.toFixed(2);
    }

    // Default select Client Approved
    varSetApprovalType('client');
  }

  varAutoSave();  // persist the status change + values
}

function varSetApprovalType(type) {
  // Update button styles
  const bankBtn = document.getElementById('var-btn-bank');
  const clientBtn = document.getElementById('var-btn-client');
  if (type === 'bank') {
    bankBtn.style.background = 'var(--blue)'; bankBtn.style.color = 'var(--base)'; bankBtn.style.fontWeight = '700';
    clientBtn.style.background = 'var(--surface)'; clientBtn.style.color = 'var(--text)'; clientBtn.style.fontWeight = '500';
  } else {
    clientBtn.style.background = 'var(--blue)'; clientBtn.style.color = 'var(--base)'; clientBtn.style.fontWeight = '700';
    bankBtn.style.background = 'var(--surface)'; bankBtn.style.color = 'var(--text)'; bankBtn.style.fontWeight = '500';
  }
  _varApprovalType = type;
  varAutoSave();
}

function varApprovalValueChanged(which) {
  const total = _varCalcTotalFromItems();
  const approvedEl = document.getElementById('var-edit-approved-val');
  const notApprovedEl = document.getElementById('var-edit-notapproved-val');

  if (which === 'approved') {
    const approved = parseFloat(approvedEl?.value) || 0;
    if (notApprovedEl) notApprovedEl.value = Math.max(0, total - approved).toFixed(2);
  } else {
    const notApproved = parseFloat(notApprovedEl?.value) || 0;
    if (approvedEl) approvedEl.value = Math.max(0, total - notApproved).toFixed(2);
  }

  varAutoSave();  // persist
}

function _varCalcTotalFromItems() {
  const rows = document.querySelectorAll('#var-items-rows > div');
  let total = 0;
  rows.forEach(r => {
    const inputs = r.querySelectorAll('input[type="number"]');
    if (inputs.length >= 2) {
      const qty = parseFloat(inputs[0]?.value) || 0;
      const rate = parseFloat(inputs[1]?.value) || 0;
      total += qty * rate;
    }
  });
  // Apply margin + GST
  const margin = total * 0.10;
  const excl = total + margin;
  const gst = excl * 0.10;
  return excl + gst;
}

// Debounced auto-save — fires on every field change, updates DB + Project Summary
const varAutoSave = debounce(async () => {
  if (!selectedVarId) return;
  await _varPersistFields();
}, 600);

async function _varPersistFields() {
  const vid = selectedVarId;  // capture before any reload clears it
  if (!vid) return;

  const fields = {
    vo_title: document.getElementById('var-edit-title')?.value || '',
    date_issued: document.getElementById('var-edit-date')?.value || null,
    status: document.getElementById('var-edit-status')?.value || 'submitted',
  };
  const voType = document.querySelector('input[name="var-vo-type"]:checked');
  if (voType) fields.vo_type = voType.value;

  // Include approval values if visible
  const status = fields.status;
  if (['approved', 'approved_for_signing', 'not_approved'].includes(status)) {
    fields.approved_value = parseFloat(document.getElementById('var-edit-approved-val')?.value) || 0;
    fields.not_approved_value = parseFloat(document.getElementById('var-edit-notapproved-val')?.value) || 0;
    fields.approval_type = _varApprovalType;
  }

  try {
    await fetch(`${API}/api/variations/${vid}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(fields),
    });
    // Update Project Summary immediately
    await loadVarRegisters();
    // Refresh VO list silently (keep selection)
    await loadVariationsSilent(currentVarFilter);
  } catch (e) {
    // Silent fail on auto-save; only show error on explicit Save click
  }
}

// Silent reload — doesn't clear selection or detail panel
async function loadVariationsSilent(status) {
  if (!selectedProjectId) return;
  try {
    const params = new URLSearchParams();
    params.set('project_entry_id', selectedProjectId);
    if (status) params.set('status', status);
    const res = await fetch(`${API}/api/variations?${params}`);
    const data = await res.json();
    variations = data.variations || [];
    renderVarCards();
  } catch (e) {}
}

async function varExportSinglePdf() {
  if (!selectedVarId) return;
  const btn = document.getElementById('btn-export-pdf');
  btn.disabled = true;
  btn.textContent = '⏳ Exporting...';
  try {
    // Trigger PDF generation and download
    const res = await fetch(`${API}/api/variations/${selectedVarId}/export-single-pdf`, { method: 'POST' });
    if (res.ok) {
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      // Build filename from VO data (avoids CORS header issues)
      const projName = document.getElementById('var-edit-project')?.value || 'Project';
      const voNum = _currentVoNumber || '';
      const voTitle = document.getElementById('var-edit-title')?.value || '';
      // Strip "VO{num} - " prefix from title to get description
      const descMatch = voTitle.match(/^VO\d+\s*[-–—]\s*(.+)/);
      const desc = descMatch ? descMatch[1] : voTitle;
      // Sanitize for filenames
      const safeProj = projName.replace(/[<>:"/\\|?*]/g, '').trim();
      const safeDesc = desc.replace(/[<>:"/\\|?*]/g, '').trim();
      let dlName = safeDesc ? `${safeProj}_VO${voNum}_${safeDesc}.pdf` : `${safeProj}_VO${voNum}.pdf`;
      a.download = dlName;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);
      notify('PDF downloaded', 'success');
    } else {
      const data = await res.json();
      notify(data.error || 'PDF export failed', 'error');
    }
  } catch (e) {
    notify('PDF export failed: ' + e.message, 'error');
  }
  btn.disabled = false;
  btn.textContent = '📄 Export PDF';
}

// ── Excel / PDF ──────────────────────────────────────────────────────

async function varGenerateExcel() {
  if (!selectedVarId) return;
  const btn = document.getElementById('btn-gen-excel');
  btn.disabled = true;
  btn.textContent = '⏳ Generating...';
  try {
    await fetch(`${API}/api/variations/${selectedVarId}/generate-excel`, { method: 'POST' });
    // Poll for completion
    const poll = setInterval(async () => {
      const r = await fetch(`${API}/api/variations/${selectedVarId}`);
      const v = await r.json();
      if (v.excel_path) {
        clearInterval(poll);
        btn.textContent = '📊 Generate Excel';
        btn.disabled = false;
        await loadVarDetail(selectedVarId);
        notify('Excel generated!', 'success');
      }
    }, 1000);
    setTimeout(() => { clearInterval(poll); btn.disabled = false; btn.textContent = '📊 Generate Excel'; }, 60000);
  } catch (e) {
    notify('Excel generation failed', 'error');
    btn.disabled = false;
    btn.textContent = '📊 Generate Excel';
  }
}

async function varExportPdf() {
  if (!selectedVarId) return;
  const btn = document.getElementById('btn-gen-pdf');
  btn.disabled = true;
  btn.textContent = '⏳ Exporting...';
  try {
    await fetch(`${API}/api/variations/${selectedVarId}/export-pdf`, { method: 'POST' });
    const poll = setInterval(async () => {
      const r = await fetch(`${API}/api/variations/${selectedVarId}`);
      const v = await r.json();
      if (v.pdf_path) {
        clearInterval(poll);
        btn.textContent = '📄 Export PDF';
        btn.disabled = false;
        await loadVarDetail(selectedVarId);
        notify('PDF exported!', 'success');
      }
    }, 1000);
    setTimeout(() => { clearInterval(poll); btn.disabled = false; btn.textContent = '📄 Export PDF'; }, 60000);
  } catch (e) {
    notify('PDF export failed', 'error');
    btn.disabled = false;
    btn.textContent = '📄 Export PDF';
  }
}

// ── Email ────────────────────────────────────────────────────────────

async function varGenerateEmail() {
  if (!selectedVarId) return;
  const btn = document.querySelector('#var-detail button[onclick="varGenerateEmail()"]');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Generating...'; }
  try {
    await fetch(`${API}/api/variations/${selectedVarId}/generate-email`, { method: 'POST' });
    // Poll for the email_drafted signal — just wait a bit then reload
    setTimeout(async () => {
      // Check if email draft is available (stored in service's last draft)
      // For now, reload the var to get any updates
      if (btn) { btn.disabled = false; btn.textContent = '🤖 Generate Draft'; }
      notify('Email draft generated — check the email body field', 'success');
      // The draft is emitted via signal, but we can't directly read it from REST
      // For now, do a best-effort approach
    }, 3000);
  } catch (e) {
    notify('Email generation failed', 'error');
    if (btn) { btn.disabled = false; btn.textContent = '🤖 Generate Draft'; }
  }
}

async function varSendEmail() {
  if (!selectedVarId) return;
  const to = document.getElementById('var-email-to')?.value || '';
  const cc = document.getElementById('var-email-cc')?.value || '';
  const subject = document.getElementById('var-email-subject')?.value || '';
  const body = document.getElementById('var-email-body')?.value || '';

  if (!to) { notify('Please enter a recipient email', 'warning'); return; }

  try {
    const res = await fetch(`${API}/api/variations/${selectedVarId}/send`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({to, cc, subject, body}),
    });
    const data = await res.json();
    if (data.ok) {
      notify('Email sent!', 'success');
      await loadVarDetail(selectedVarId);
    } else {
      notify('Failed to send email', 'error');
    }
  } catch (e) {
    notify('Failed to send email', 'error');
  }
}

// ── Drag-and-drop reordering ────────────────────────────────────────

let _varDragSourceId = null;
let _varDragInsertBefore = true;

function varDragStart(e, entryId) {
  _varDragSourceId = entryId;
  const card = document.getElementById(`varcard-${entryId}`);
  if (card) {
    card.style.opacity = '0.4';
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', entryId);
  }
}

function varDragOver(e) {
  e.preventDefault();
  e.stopPropagation();
  e.dataTransfer.dropEffect = 'move';
  // Clear all indicators first
  document.querySelectorAll('#var-cards-container .card').forEach(c => {
    c.style.borderTop = ''; c.style.borderBottom = '';
  });
  const card = e.target.closest('.card');
  if (card && card.id?.startsWith('varcard-')) {
    const rect = card.getBoundingClientRect();
    _varDragInsertBefore = e.clientY < (rect.top + rect.height / 2);
    if (_varDragInsertBefore) {
      card.style.borderTop = '2px solid var(--blue)';
    } else {
      card.style.borderBottom = '2px solid var(--blue)';
    }
  }
}

function varDragLeave(e) {
  const card = e.target.closest('.card');
  if (card?.id?.startsWith('varcard-')) {
    const rel = e.relatedTarget;
    if (!rel || !card.contains(rel)) {
      card.style.borderTop = '';
      card.style.borderBottom = '';
    }
  }
}

function varDragEnd(e) {
  _varDragSourceId = null;
  document.querySelectorAll('#var-cards-container .card').forEach(c => {
    c.style.opacity = ''; c.style.borderTop = ''; c.style.borderBottom = '';
  });
}

// Drop on container (below all cards)
function varContainerDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  document.querySelectorAll('#var-cards-container .card').forEach(c => {
    c.style.borderTop = ''; c.style.borderBottom = '';
  });
}

async function varContainerDrop(e) {
  e.preventDefault();
  const src = _varDragSourceId;
  varDragEnd(e);
  if (!src) return;
  const fromIdx = variations.findIndex(v => v.entry_id === src);
  if (fromIdx < 0) return;
  const moved = variations.splice(fromIdx, 1)[0];
  variations.push(moved);
  variations.forEach((v, i) => { v.sort_order = i; });
  renderVarCards();
  applyVarSelection();
  try {
    await fetch(`${API}/api/variations/reorder`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ ordered_ids: variations.map(v => v.entry_id) }),
    });
  } catch (_) {}
}

async function varDrop(e, targetId) {
  e.preventDefault();
  e.stopPropagation();
  const src = _varDragSourceId;
  varDragEnd(e);
  if (!src || src === targetId) return;

  const fromIdx = variations.findIndex(v => v.entry_id === src);
  let toIdx = variations.findIndex(v => v.entry_id === targetId);
  if (fromIdx < 0 || toIdx < 0) return;

  // Insert below target if dragged to bottom half
  if (!_varDragInsertBefore) toIdx = Math.min(toIdx + 1, variations.length);
  if (fromIdx < toIdx) toIdx--;

  const moved = variations.splice(fromIdx, 1)[0];
  variations.splice(toIdx, 0, moved);
  variations.forEach((v, i) => { v.sort_order = i; });

  renderVarCards();
  applyVarSelection();

  try {
    await fetch(`${API}/api/variations/reorder`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ ordered_ids: variations.map(v => v.entry_id) }),
    });
  } catch (_) {}
}

// ── Delete ───────────────────────────────────────────────────────────

async function varRestore() {
  if (!selectedVarId) return;
  try {
    // Get next VO number from server
    let nextVo = 1;
    try {
      const r = await fetch(`${API}/api/variations/next-vo-number?project_entry_id=${selectedProjectId}`);
      const d = await r.json();
      nextVo = d.vo_number || 1;
    } catch (_) {}

    // Restore AND update vo_number + vo_title
    const res = await fetch(`${API}/api/variations/${selectedVarId}/restore`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      // Rename with new VO number
      await fetch(`${API}/api/variations/${selectedVarId}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ vo_number: nextVo, vo_title: `VO${nextVo} - ` }),
      });

      selectedVarIds.delete(selectedVarId);
      document.getElementById('var-detail').style.display = 'none';
      document.getElementById('var-detail-empty').style.display = 'flex';
      selectedVarId = null;
      await loadVariations('void');
      await loadVarRegisters();
      notify(`Variation restored as VO${nextVo}`, 'success');
    }
  } catch (e) {
    notify('Failed to restore', 'error');
  }
}

async function varDelete() {
  if (!selectedVarId) return;

  if (currentVarFilter === 'void') {
    // Permanent delete
    if (!confirm('Permanently delete this variation? This cannot be undone.')) return;
    try {
      await fetch(`${API}/api/variations/${selectedVarId}/permanent`, { method: 'DELETE' });
      selectedVarIds.delete(selectedVarId);
      document.getElementById('var-detail').style.display = 'none';
      document.getElementById('var-detail-empty').style.display = 'flex';
      selectedVarId = null;
      await loadVariations('void');
      notify('Variation permanently deleted', 'success');
    } catch (e) {
      notify('Failed to delete', 'error');
    }
  } else {
    // Soft-delete → void
    if (!confirm('Void this variation? It will be hidden from the main list.')) return;
    try {
      await fetch(`${API}/api/variations/${selectedVarId}`, { method: 'DELETE' });
      selectedVarIds.delete(selectedVarId);
      document.getElementById('var-detail').style.display = 'none';
      document.getElementById('var-detail-empty').style.display = 'flex';
      selectedVarId = null;
      await loadVariations(currentVarFilter);
      notify('Variation voided', 'success');
    } catch (e) {
      notify('Failed to void', 'error');
    }
  }
}

// ── Variation Agent ──────────────────────────────────────────────────

let _agentFiles = [];  // {name, file} pairs waiting to be uploaded

function openAgentModal() {
  document.getElementById('agent-modal').classList.add('show');
  document.getElementById('agent-text').value = '';
  _agentFiles = [];
  renderAgentFiles();
  document.getElementById('agent-results').style.display = 'none';
}

function closeAgentModal() {
  document.getElementById('agent-modal').classList.remove('show');
}

function agentHandleDrop(e) {
  e.preventDefault();
  e.target.style.borderColor = 'var(--overlay)';
  agentHandleFiles(e.dataTransfer.files);
}

function agentHandleFiles(fileList) {
  for (const f of fileList) {
    if (!_agentFiles.find(af => af.name === f.name && af.size === f.size)) {
      _agentFiles.push({ name: f.name, size: f.size, file: f });
    }
  }
  renderAgentFiles();
}

function renderAgentFiles() {
  const container = document.getElementById('agent-files');
  if (!_agentFiles.length) {
    container.innerHTML = '';
    return;
  }
  container.innerHTML = _agentFiles.map((f, i) => `
    <div style="display:flex;align-items:center;gap:8px;padding:4px 8px;background:var(--surface);border-radius:6px;margin-bottom:4px;font-size:12px;">
      <span>📄</span>
      <span style="flex:1;color:var(--text);">${esc(f.name)}</span>
      <span style="color:var(--muted);font-size:10px;">${(f.size / 1024).toFixed(0)}KB</span>
      <button onclick="_agentFiles.splice(${i},1);renderAgentFiles()"
        style="background:none;border:none;color:var(--red);cursor:pointer;">×</button>
    </div>
  `).join('');
}

async function agentAnalyze() {
  const text = document.getElementById('agent-text')?.value?.trim() || '';
  if (!text && !_agentFiles.length) {
    notify('Please enter text or attach files', 'warning');
    return;
  }

  const btn = document.getElementById('btn-agent-analyze');
  const spinner = document.getElementById('agent-spinner');
  const results = document.getElementById('agent-results');
  btn.disabled = true;
  spinner.style.display = 'inline-block';
  results.style.display = 'none';

  // ── Build stacked progress log ──────────────────────────────────
  const logEl = document.createElement('div');
  logEl.id = 'agent-progress-log';
  logEl.style.cssText = 'margin-top:8px;padding:8px 12px;background:var(--base);border-radius:8px;font-size:11px;line-height:1.8;max-height:200px;overflow-y:auto;font-family:monospace;';

  function addLog(msg, color) {
    const line = document.createElement('div');
    line.style.color = color || 'var(--subtext)';
    line.textContent = msg;
    logEl.appendChild(line);
    logEl.scrollTop = logEl.scrollHeight;
  }

  // Remove old log if exists
  const oldLog = document.getElementById('agent-progress-log');
  if (oldLog) oldLog.remove();
  btn.parentElement.appendChild(logEl);

  // ── Pre-LLM progress ────────────────────────────────────────────
  addLog('▸ Reading prompt...', 'var(--blue)');
  await sleep(200);
  if (text) {
    addLog(`  ✓ Text prompt: ${text.length} chars`, 'var(--green)');
  } else {
    addLog(`  ⚠ No text prompt — analyzing files only`, 'var(--yellow)');
  }
  await sleep(200);

  if (_agentFiles.length) {
    addLog(`▸ ${_agentFiles.length} file(s) attached:`, 'var(--blue)');
    for (const af of _agentFiles) {
      addLog(`  📄 ${af.name} (${(af.size / 1024).toFixed(0)} KB)`, 'var(--subtext)');
    }
  }
  await sleep(300);

  // ── LLM call ────────────────────────────────────────────────────
  addLog('▸ Calling Gemini AI...', 'var(--purple)');
  const thinkMsgs = [
    '  ⏳ LilAmy is thinking...',
    '  ⏳ Analyzing project details...',
    '  ⏳ Extracting line items...',
    '  ⏳ Matching projects...',
  ];
  let thinkIdx = 0;
  addLog(thinkMsgs[0], 'var(--blue)');
  const thinkTimer = setInterval(() => {
    thinkIdx = (thinkIdx + 1) % thinkMsgs.length;
    // Replace last line
    const lines = logEl.querySelectorAll('div');
    if (lines.length > 0) {
      const last = lines[lines.length - 1];
      if (last.textContent.startsWith('  ⏳')) {
        last.textContent = thinkMsgs[thinkIdx];
      } else {
        addLog(thinkMsgs[thinkIdx], 'var(--blue)');
      }
    }
  }, 2000);

  try {
    const formData = new FormData();
    formData.append('text', text);
    for (const af of _agentFiles) {
      formData.append('files', af.file);
    }

    const t0 = Date.now();
    const res = await fetch(`${API}/api/variations/agent/analyze`, {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();
    const elapsed = ((Date.now() - t0) / 1000).toFixed(1);

    clearInterval(thinkTimer);
    // Remove last thinking line
    const lines = logEl.querySelectorAll('div');
    if (lines.length > 0) {
      const last = lines[lines.length - 1];
      if (last.textContent.startsWith('  ⏳')) last.remove();
    }

    if (data.error) {
      addLog(`✗ Error: ${data.error}`, 'var(--red)');
    } else {
      addLog(`✓ Analysis complete (${elapsed}s)`, 'var(--green)');
      const a = data.analysis || {};
      if (a.project_name) addLog(`  Project: ${a.project_name}`, 'var(--text)');
      if (a.line_items) addLog(`  Items found: ${a.line_items.length}`, 'var(--text)');
      if (a.total_estimated_cost) addLog(`  Est. cost: $${a.total_estimated_cost.toLocaleString()}`, 'var(--green)');
      setTimeout(() => logEl.remove(), 2000);
      renderAgentResults(data);
    }
  } catch (e) {
    clearInterval(thinkTimer);
    addLog(`✗ Failed: ${e.message}`, 'var(--red)');
    setTimeout(() => logEl.remove(), 4000);
  } finally {
    btn.disabled = false;
    spinner.style.display = 'none';
  }
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function renderAgentResults(data) {
  const panel = document.getElementById('agent-results');
  const content = document.getElementById('agent-results-content');
  panel.style.display = 'block';

  const analysis = data.analysis || {};
  const match = data.project_match;
  const nextVo = data.next_vo_number;

  if (analysis.parse_error || analysis.raw_response) {
    content.innerHTML = `<div style="color:var(--red);font-size:12px;">⚠️ Could not parse structured data. Raw response:</div>
      <pre style="background:var(--base);padding:8px;border-radius:6px;font-size:11px;color:var(--subtext);max-height:200px;overflow-y:auto;">${esc(analysis.raw_response || '')}</pre>`;
    return;
  }

  let html = '';

  // Project match
  if (match) {
    html += `<div class="agent-card" style="margin-bottom:8px;">
      <div class="agent-card-title">📁 Project</div>
      <div style="color:var(--green);font-weight:600;">✅ ${esc(match.name)} — matched</div>
      <div style="font-size:11px;color:var(--muted);">Next VO number: <b>VO${nextVo}</b></div>
    </div>`;
  } else {
    html += `<div class="agent-card" style="margin-bottom:8px;">
      <div class="agent-card-title">📁 Project</div>
      <div style="color:var(--yellow);">⚠️ No match for "${esc(analysis.project_name || 'unknown')}"</div>
      <button class="btn" onclick="agentCreateProject()" style="margin-top:6px;font-size:11px;">🏗 Create New Project</button>
    </div>`;
  }

  // VO summary
  html += `<div class="agent-card" style="margin-bottom:8px;">
    <div class="agent-card-title">📝 Variation</div>
    <div style="font-weight:600;color:var(--blue);">VO${nextVo || '?'} - ${esc(analysis.vo_title || '(no title)')}</div>
    <div style="font-size:12px;color:var(--subtext);margin-top:4px;">${esc(analysis.vo_summary || '')}</div>
    <div style="font-size:11px;color:var(--muted);margin-top:4px;">Type: ${esc(analysis.vo_type || 'Head Contract VO')}</div>
  </div>`;

  // Line items
  const items = analysis.line_items || [];
  if (items.length) {
    html += `<div class="agent-card" style="margin-bottom:8px;">
      <div class="agent-card-title">📊 Line Items (${items.length})</div>`;
    let totalEst = 0;
    items.forEach((it, i) => {
      const cost = (it.qty || 0) * (it.rate || 0);
      totalEst += cost;
      html += `<div style="font-size:11px;padding:2px 0;border-bottom:1px solid #1a1a2e;">
        ${i+1}. ${esc(it.description || 'Item')} — ${it.qty || 0} ${esc(it.unit || 'item')} × $${(it.rate || 0).toLocaleString()} = <b>$${cost.toLocaleString()}</b>
      </div>`;
    });
    html += `<div style="font-size:12px;font-weight:600;color:var(--green);margin-top:4px;">Estimated Total: $${totalEst.toLocaleString()}</div>`;
    html += `</div>`;
  }

  // Notes
  if (analysis.notes) {
    html += `<div class="agent-card" style="margin-bottom:8px;">
      <div class="agent-card-title">📝 Notes</div>
      <div style="font-size:11px;color:var(--subtext);">${esc(analysis.notes)}</div>
    </div>`;
  }

  // Action button
  if (match) {
    html += `<button class="btn btn-primary" onclick="agentCreateVO('${match.entry_id}', ${nextVo})"
      style="width:100%;padding:10px;">✅ Create VO${nextVo} in ${esc(match.name)}</button>`;
  }

  content.innerHTML = html;
}

async function agentCreateVO(projectId, voNumber) {
  // Switch to the matched project and create the VO
  const analysis = {}; // stored as last analysis — we read from DOM results
  // Actually we need the analysis data. Store it globally.
  if (!_lastAgentData || !_lastAgentData.analysis) {
    notify('No analysis data available', 'error');
    return;
  }

  const a = _lastAgentData.analysis;
  const proj = _lastAgentData.existing_projects?.find(p => p.entry_id === projectId);

  try {
    // Ensure we're on the right project
    if (selectedProjectId !== projectId) {
      document.getElementById('var-project-select').value = projectId;
      await varSwitchProject();
    }

    // Create the VO
    const res = await fetch(`${API}/api/variations`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        project_entry_id: projectId,
        project_name: proj?.name || '',
        vo_number: voNumber,
        vo_title: `VO${voNumber} - ${a.vo_title || ''}`,
        vo_type: a.vo_type || 'Head Contract VO',
        date_issued: new Date().toISOString().substring(0, 10),
        status: 'submitted',
      }),
    });
    const data = await res.json();
    if (!data.entry_id) { notify('Failed to create VO', 'error'); return; }

    // Add line items
    for (const it of (a.line_items || [])) {
      await fetch(`${API}/api/variations/${data.entry_id}/items`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          item_number: (a.line_items?.indexOf(it) || 0) + 1,
          description: it.description || '',
          qty: it.qty || 0,
          unit: it.unit || 'item',
          rate: it.rate || 0,
          credit: 0,
        }),
      });
    }

    closeAgentModal();
    await loadVariations(currentVarFilter);
    await loadVarRegisters();
    selectVariation(data.entry_id);
    notify(`VO${voNumber} created with ${a.line_items?.length || 0} items!`, 'success');
  } catch (e) {
    notify('Failed: ' + e.message, 'error');
  }
}

let _lastAgentData = null;

// Override renderAgentResults to store data
const _origRenderAgentResults = renderAgentResults;
renderAgentResults = function(data) {
  _lastAgentData = data;
  _origRenderAgentResults(data);
};

async function agentCreateProject() {
  if (!_lastAgentData?.analysis) return;
  const a = _lastAgentData.analysis;

  // Pre-fill the new project modal
  document.getElementById('agent-modal').classList.remove('show');
  document.getElementById('var-new-proj-name').value = a.project_name || '';
  document.getElementById('var-new-proj-filename').value = (a.project_name || 'Project').replace(/[^a-zA-Z0-9]/g, '_');
  document.getElementById('var-new-project-modal').classList.add('show');

  // Store that this is an agent-triggered creation
  window._agentPendingVO = a;
}

// Override varCreateProject to handle agent-triggered creation
const _origVarCreateProject = varCreateProject;
varCreateProject = async function() {
  await _origVarCreateProject();
  // If agent-triggered, create the first VO
  if (window._agentPendingVO && selectedProjectId) {
    const a = window._agentPendingVO;
    const nextVo = 1; // First VO in new project
    await agentCreateVO(selectedProjectId, nextVo);
    window._agentPendingVO = null;
  }
};

// ── Boot ─────────────────────────────────────────────────────────────

init();
