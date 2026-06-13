// ── lilAmy WebUI — vanilla JS SPA ───────────────────────────────────

const API = '';
let emails = [];
let selectedId = null;
let refreshTimer = null;
let lastCount = 0;

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
    <div class="module ${m.enabled ? 'active' : 'disabled'}"
         ${m.enabled ? `onclick="switchModule('${m.id}')"` : ''}
         title="${m.description}">
      <span class="icon">${m.icon}</span>
      <span>${m.name}</span>
    </div>
  `).join('');
}

function switchModule(id) {
  document.querySelectorAll('#sidebar .module').forEach(el => {
    el.classList.toggle('active', el.textContent.trim().startsWith(
      document.querySelector(`#sidebar .module[onclick*="${id}"]`)?.textContent?.trim().charAt(0) || ''
    ));
  });
  // Future: load different module content into #module-content
  document.getElementById('module-title').textContent =
    id === 'amail' ? '📧 AMail' : id;
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
    renderCards();
    document.getElementById('email-count').textContent = `📧 ${emails.length} emails`;
  } catch (e) {
    showToast('Failed to load emails');
  }
}

function renderCards() {
  const container = document.getElementById('cards-container');
  const empty = document.getElementById('cards-empty');

  if (emails.length === 0) {
    container.innerHTML = '';
    empty.style.display = 'flex';
    return;
  }
  empty.style.display = 'none';

  container.innerHTML = emails.map(e => {
    let todos = [];
    try { todos = typeof e.todos_json === 'string' ? JSON.parse(e.todos_json) : (e.todos_json || []); } catch (_) {}
    const cn = e.chinese_summary || e.subject?.substring(0, 60) || '(No Subject)';
    const urgClass = `urg-${e.urgency || 'low'}`;
    const time = (e.received_time || '').replace('T', ' ').substring(0, 16);

    return `
      <div class="card${selectedIds.has(e.entry_id) ? ' selected' : ''}"
           id="card-${e.entry_id}"
           onclick="selectEmail('${e.entry_id}', event)"
           ondblclick="openOutlook('${e.entry_id}')">
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
  try {
    const res = await fetch(`${API}${url}`, { method: 'POST' });
    const data = await res.json();
    showToast(data.message || 'Started');
    let tries = 0;
    let stable = 0;
    let prevCount = emails.length;
    const maxStable = url.includes('sync') ? 8 : 5;
    const poll = setInterval(async () => {
      await loadEmails();
      tries++;
      if (emails.length !== prevCount) {
        prevCount = emails.length;
        stable = 0;
      } else {
        stable++;
      }
      if (stable >= maxStable) {
        clearInterval(poll);
        showProgress(false);
        btn.disabled = false;
        btn.textContent = label;
        if (isPrimary) btn.classList.add('primary');
        document.getElementById('email-count').textContent = `📧 ${emails.length} emails`;
        showToast(`${emails.length} emails total`);
      }
    }, 3000);
  } catch (e) {
    showToast('Operation failed');
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

  try {
    const res = await fetch(`${API}/api/amail/emails/${selectedId}/reply`, { method: 'POST' });
    const data = await res.json();
    if (data.draft) {
      document.getElementById('det-reply').value = data.draft;
      document.getElementById('btn-refine').disabled = false;
      document.getElementById('btn-copy').disabled = false;
      document.getElementById('refine-row').style.display = 'flex';
      // Update local cache
      const email = emails.find(e => e.entry_id === selectedId);
      if (email) email.reply_draft = data.draft;
    }
  } catch (e) {
    showToast('Draft failed');
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
    }
  } catch (e) {
    showToast('Refine failed');
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
    showToast('Email removed');
  } catch (e) {
    showToast('Remove failed');
  }
}

function openOutlook(entryId) {
  // Web can't open Outlook directly — copy to clipboard and show mailto
  const email = emails.find(e => e.entry_id === entryId);
  if (email) {
    const subject = encodeURIComponent(email.subject || '');
    window.open(`mailto:?subject=Re:%20${subject}`, '_blank');
  }
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
  }, 10_000);
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

function showToast(msg) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.style.display = 'block';
  setTimeout(() => { toast.style.display = 'none'; }, 3000);
}

// ── Multi-select ────────────────────────────────────────────────────

let selectedIds = new Set();    // all selected entry_ids
let lastClickedId = null;       // for Shift+Click range

function getCardIndex(id) {
  return emails.findIndex(e => e.entry_id === id);
}

function isSelected(id) { return selectedIds.has(id); }

function selectSingle(id) {
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

  const eid = card.id?.replace('card-', '') || '';

  // If right-clicking outside current selection, select only this one
  if (!selectedIds.has(eid)) {
    selectSingle(eid);
  }
  // Otherwise keep the multi-selection

  ctxMenu.style.display = 'block';
  ctxMenu.style.left = Math.min(e.clientX, window.innerWidth - 210) + 'px';
  ctxMenu.style.top = Math.min(e.clientY, window.innerHeight - 140) + 'px';
});

document.addEventListener('click', (e) => {
  if (!e.target.closest('.ctx-menu')) ctxMenu.style.display = 'none';
});
document.getElementById('card-panel').addEventListener('scroll', () => ctxMenu.style.display = 'none');
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') ctxMenu.style.display = 'none';
});

function ctxOpenOutlook() {
  for (const id of selectedIds) openOutlook(id);
  ctxMenu.style.display = 'none';
}

async function ctxRemove() {
  const toRemove = [...selectedIds];
  if (toRemove.length === 0) { ctxMenu.style.display = 'none'; return; }

  showToast(`Removing ${toRemove.length} email(s)...`);
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
  renderCards();
  document.getElementById('email-count').textContent = `📧 ${emails.length} emails`;
  showToast(`${toRemove.length} email(s) removed`);
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
});

// Enter key on count inputs triggers fetch
document.getElementById('earlier-count').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') fetchEarlier();
});
document.getElementById('latest-count').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') fetchLatest();
});

// ── Boot ─────────────────────────────────────────────────────────────

init();
