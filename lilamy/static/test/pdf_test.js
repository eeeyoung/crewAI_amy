/* pdf_test.js — PDF Generation Test GUI
   Tab switching, auto-calculation, line item editing, PDF export.
   Canvas Editor with iframe preview.
   Vanilla JS — no framework dependency. */

// ═══════════════════════════════════════════════════════════════════
// Tab Switching
// ═══════════════════════════════════════════════════════════════════

let currentTab = 'vo';

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');

  // Highlight correct tab button
  const buttons = document.querySelectorAll('.tab-btn');
  if (tab === 'vo') buttons[0].classList.add('active');
  else if (tab === 'po') buttons[1].classList.add('active');
  else if (tab === 'canvas') { buttons[2].classList.add('active'); canvasInit(); }
}

// ═══════════════════════════════════════════════════════════════════
// Toast
// ═══════════════════════════════════════════════════════════════════

function showToast(msg, type) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + type;
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 3000);
}

// ═══════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════

function fmt(n) { return (n || 0).toLocaleString('en-AU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }

function parseNum(val) { const n = parseFloat(val); return isNaN(n) ? 0 : n; }

// ═══════════════════════════════════════════════════════════════════
// VO — Variation Order
// ═══════════════════════════════════════════════════════════════════

const VO_DEFAULTS = [
  { desc: 'Excavation and trenching for new stormwater line', qty: 28, unit: 'm', rate: 85.00, credit: 0 },
  { desc: 'Supply and lay 150mm PVC pipe', qty: 28, unit: 'm', rate: 45.00, credit: 0 },
  { desc: 'Backfill and compaction', qty: 28, unit: 'm', rate: 22.00, credit: 0 },
  { desc: 'Credit for omitted landscaping', qty: 1, unit: 'item', rate: 500.00, credit: 500.00 },
];

function voAddItem(desc, qty, unit, rate, credit) {
  const tbody = document.getElementById('vo-items-body');
  const idx = tbody.children.length + 1;
  const tr = document.createElement('tr');
  tr.innerHTML = `<td class="item-num">${idx}</td>
    <td><input class="desc" value="${desc || ''}" onchange="voRecalc()" oninput="voRecalc()"></td>
    <td><input class="num" type="number" value="${qty || 0}" onchange="voRecalc()" oninput="voRecalc()" step="any"></td>
    <td><input style="width:60px; text-align:center" value="${unit || 'item'}" onchange="voRecalc()"></td>
    <td><input class="num" type="number" value="${rate || 0}" onchange="voRecalc()" oninput="voRecalc()" step="any"></td>
    <td class="readonly-val">$0.00</td>
    <td><input class="num" type="number" value="${credit || 0}" onchange="voRecalc()" oninput="voRecalc()" step="any" style="color: var(--peach);"></td>
    <td><button class="btn-del" onclick="voDelItem(this)">&times;</button></td>`;
  tbody.appendChild(tr);
}

function voDelItem(btn) {
  if (document.getElementById('vo-items-body').children.length <= 1) return;
  btn.closest('tr').remove();
  voRenumber();
  voRecalc();
}

function voRenumber() {
  const rows = document.getElementById('vo-items-body').children;
  for (let i = 0; i < rows.length; i++) {
    rows[i].querySelector('.item-num').textContent = i + 1;
  }
}

function voGetItems() {
  const items = [];
  const rows = document.getElementById('vo-items-body').children;
  for (let i = 0; i < rows.length; i++) {
    const inputs = rows[i].querySelectorAll('input');
    items.push({
      item_number: i + 1,
      description: inputs[0].value,
      qty: parseNum(inputs[1].value),
      unit: inputs[2].value || 'item',
      rate: parseNum(inputs[3].value),
      cost: parseNum(inputs[1].value) * parseNum(inputs[3].value),
      credit: parseNum(inputs[4].value),
    });
  }
  return items;
}

function voRecalc() {
  const items = voGetItems();
  const rows = document.getElementById('vo-items-body').children;
  for (let i = 0; i < rows.length; i++) {
    const costCell = rows[i].querySelectorAll('.readonly-val')[0];
    if (costCell) costCell.textContent = '$' + fmt(items[i].cost);
  }
  const subCost = items.reduce((s, it) => s + it.cost, 0);
  const subCredit = items.reduce((s, it) => s + it.credit, 0);
  const nett = subCost - subCredit;
  const margin = Math.round(nett * 0.10 * 100) / 100;
  const excl = Math.round((nett + margin) * 100) / 100;
  const gst = Math.round(excl * 0.10 * 100) / 100;
  const total = Math.round((excl + gst) * 100) / 100;

  const summary = document.getElementById('vo-summary');
  const rows2 = summary.querySelectorAll('.summary-row');
  rows2[0].children[1].textContent = '$' + fmt(subCost);
  rows2[1].children[1].textContent = '$' + fmt(subCredit);
  rows2[2].children[1].textContent = '$' + fmt(nett);
  rows2[3].children[1].textContent = '$' + fmt(margin);
  rows2[4].children[1].textContent = '$' + fmt(excl);
  rows2[5].children[1].textContent = '$' + fmt(gst);
  rows2[6].children[1].textContent = '$' + fmt(total);
}

function voLoadSample() {
  document.getElementById('vo-project').value = 'Sample Project';
  document.getElementById('vo-date').value = '21/06/2026';
  document.getElementById('vo-job').value = '47CBR';
  document.getElementById('vo-company').value = 'Welink Construction Pty Ltd';
  document.getElementById('vo-number').value = '3';
  document.getElementById('vo-title').value = 'Additional stormwater drainage to Block B';
  document.getElementById('vo-ref').value = 'SI-2026-0042';
  document.getElementById('vo-address').value = '12 Sample Rd, Sydney NSW 2000';
  document.getElementById('vo-initials').value = 'AC';
  document.getElementById('vo-estimate').value = '0';
  document.getElementById('vo-items-body').innerHTML = '';
  VO_DEFAULTS.forEach(d => voAddItem(d.desc, d.qty, d.unit, d.rate, d.credit));
  voRecalc();
}

async function voExportPdf() {
  const btn = document.querySelector('#tab-vo .btn-success');
  const origText = btn.textContent;
  btn.textContent = '⏳ Generating...';
  btn.disabled = true;

  const items = voGetItems();
  const payload = {
    doc_type: 'vo',
    metadata: {
      project_name: document.getElementById('vo-project').value,
      date_issued: document.getElementById('vo-date').value,
      job_number: document.getElementById('vo-job').value,
      company_name: document.getElementById('vo-company').value,
      vo_number: parseInt(document.getElementById('vo-number').value) || 1,
      vo_title: document.getElementById('vo-title').value,
      site_instruction_ref: document.getElementById('vo-ref').value,
      project_location: document.getElementById('vo-address').value,
      initials: document.getElementById('vo-initials').value || 'AC',
      is_estimate: document.getElementById('vo-estimate').value === '1',
    },
    items: items,
  };

  try {
    const resp = await fetch('/api/test/pdf/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || err.error || 'Unknown error');
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `VO${payload.metadata.vo_number}_${payload.metadata.project_name.replace(/[^a-zA-Z0-9]/g, '_')}.pdf`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast('PDF exported successfully!', 'success');
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
  btn.textContent = origText;
  btn.disabled = false;
}

// ═══════════════════════════════════════════════════════════════════
// PO — Purchase Order
// ═══════════════════════════════════════════════════════════════════

const PO_DEFAULTS = [
  { desc: 'Supply and install 100mm PVC stormwater pipe', qty: 45, unit: 'm', rate: 28.50, discount: 0 },
  { desc: 'Supply and install stormwater pit 450x450mm', qty: 3, unit: 'ea', rate: 320.00, discount: 0 },
  { desc: 'Connection to existing council mains', qty: 1, unit: 'item', rate: 1500.00, discount: 0 },
  { desc: 'NOTE: All works subject to Dial Before You Dig clearance', qty: 0, unit: 'NOTE', rate: 0, discount: 0 },
];

function poAddItem(desc, qty, unit, rate, discount) {
  const tbody = document.getElementById('po-items-body');
  const idx = tbody.children.length + 1;
  const tr = document.createElement('tr');
  tr.innerHTML = `<td class="item-num">${idx}</td>
    <td><input class="desc" value="${desc || ''}" onchange="poRecalc()" oninput="poRecalc()"></td>
    <td><input class="num" type="number" value="${qty || 0}" onchange="poRecalc()" oninput="poRecalc()" step="any"></td>
    <td><input style="width:55px; text-align:center" value="${unit || 'item'}" onchange="poRecalc()"></td>
    <td><input class="num" type="number" value="${rate || 0}" onchange="poRecalc()" oninput="poRecalc()" step="any"></td>
    <td><input class="num" type="number" value="${discount || 0}" onchange="poRecalc()" oninput="poRecalc()" step="any" style="width:70px"></td>
    <td class="readonly-val">$0.00</td>
    <td><button class="btn-del" onclick="poDelItem(this)">&times;</button></td>`;
  tbody.appendChild(tr);
}

function poDelItem(btn) {
  if (document.getElementById('po-items-body').children.length <= 1) return;
  btn.closest('tr').remove();
  poRenumber();
  poRecalc();
}

function poRenumber() {
  const rows = document.getElementById('po-items-body').children;
  for (let i = 0; i < rows.length; i++) {
    rows[i].querySelector('.item-num').textContent = i + 1;
  }
}

function poGetItems() {
  const items = [];
  const rows = document.getElementById('po-items-body').children;
  for (let i = 0; i < rows.length; i++) {
    const inputs = rows[i].querySelectorAll('input');
    const qty = parseNum(inputs[1].value);
    const rate = parseNum(inputs[3].value);
    const discount = parseNum(inputs[4].value);
    items.push({
      description: inputs[0].value,
      qty: qty,
      unit: inputs[2].value || 'item',
      rate: rate,
      discount: discount,
      amount: Math.round((qty * rate - discount) * 100) / 100,
      is_note: (inputs[2].value || '').toUpperCase() === 'NOTE',
    });
  }
  return items;
}

function poRecalc() {
  const items = poGetItems();
  const rows = document.getElementById('po-items-body').children;
  for (let i = 0; i < rows.length; i++) {
    const costCell = rows[i].querySelectorAll('.readonly-val')[0];
    if (costCell) costCell.textContent = '$' + fmt(items[i].amount);
  }
  const totalEx = items.reduce((s, it) => s + (it.is_note ? 0 : it.amount), 0);
  const gst = Math.round(totalEx * 0.10 * 100) / 100;
  const gross = Math.round((totalEx + gst) * 100) / 100;

  const summary = document.getElementById('po-summary');
  const rows2 = summary.querySelectorAll('.summary-row');
  rows2[0].children[1].textContent = '$' + fmt(totalEx);
  rows2[1].children[1].textContent = '$' + fmt(gst);
  rows2[2].children[1].textContent = '$' + fmt(gross);
}

function poLoadSample() {
  document.getElementById('po-ref').value = 'PO16888';
  document.getElementById('po-date').value = '21 JUN 2026';
  document.getElementById('po-code').value = '47CBR';
  document.getElementById('po-vendor').value = 'Demo Plumbing Pty Ltd';
  document.getElementById('po-addr1').value = '12 Sample Rd';
  document.getElementById('po-addr2').value = 'Sydney NSW 2000';
  document.getElementById('po-abn').value = '12 345 678 901';
  document.getElementById('po-phone').value = '(02) 9123 4567';
  document.getElementById('po-creditor').value = 'DEM456';
  document.getElementById('po-project').value = 'Sample Project';
  document.getElementById('po-location').value = 'Sydney';
  document.getElementById('po-delivery').value = '15 JUL 2026';
  document.getElementById('po-delivery-inst').value = 'Deliver to site office, Mon-Fri 7am-3pm';
  document.getElementById('po-attention').value = 'Site Manager';
  document.getElementById('po-approved').value = 'ACHEN';
  document.getElementById('po-instructions').value = 'All materials must comply with AS/NZS standards. Provide test certificates on delivery.';
  document.getElementById('po-items-body').innerHTML = '';
  PO_DEFAULTS.forEach(d => poAddItem(d.desc, d.qty, d.unit, d.rate, d.discount));
  poRecalc();
}

async function poExportPdf() {
  const btn = document.querySelector('#tab-po .btn-success');
  const origText = btn.textContent;
  btn.textContent = '⏳ Generating...';
  btn.disabled = true;

  const items = poGetItems();
  const payload = {
    doc_type: 'po',
    metadata: {
      reference_number: document.getElementById('po-ref').value,
      vendor_name: document.getElementById('po-vendor').value,
      vendor_address_line1: document.getElementById('po-addr1').value,
      vendor_address_line2: document.getElementById('po-addr2').value,
      vendor_abn: document.getElementById('po-abn').value,
      order_number: document.getElementById('po-code').value + ' - ' + document.getElementById('po-ref').value.replace('PO', ''),
      order_date: document.getElementById('po-date').value,
      creditor_phone: document.getElementById('po-phone').value,
      creditor_code: document.getElementById('po-creditor').value,
      project_name: document.getElementById('po-project').value,
      project_code: document.getElementById('po-code').value,
      project_location: document.getElementById('po-location').value,
      delivery_date: document.getElementById('po-delivery').value,
      delivery_instructions: document.getElementById('po-delivery-inst').value,
      attention: document.getElementById('po-attention').value,
      special_instructions: document.getElementById('po-instructions').value,
      approved_by: document.getElementById('po-approved').value,
    },
    items: items,
  };

  try {
    const resp = await fetch('/api/test/pdf/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.detail || err.error || 'Unknown error');
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `PO_${payload.metadata.reference_number}_${payload.metadata.vendor_name.replace(/[^a-zA-Z0-9]/g, '_')}.pdf`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast('PDF exported successfully!', 'success');
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
  }
  btn.textContent = origText;
  btn.disabled = false;
}

// ═══════════════════════════════════════════════════════════════════
// Canvas Editor (iframe preview — real HTML, guaranteed consistent)
// ═══════════════════════════════════════════════════════════════════

let canvasState = {
  schema: null,
  selectedElId: null,
  scale: 0.5,
  initialized: false,
};

function canvasInit() {
  setTimeout(() => {
    canvasFitScale();
    canvasLoadTemplateList();
  }, 100);
  if (!canvasState.initialized) {
    canvasState.initialized = true;
    window.addEventListener('resize', canvasFitScale);
    window.addEventListener('message', canvasOnIframeMessage);
  }
}

function canvasFitScale() {
  const container = document.getElementById('preview-container');
  const wrapper = document.getElementById('preview-scale-wrapper');
  if (!container || container.clientWidth < 10) return;
  const scaleW = (container.clientWidth - 40) / (210 * 3.7795);
  const scaleH = (container.clientHeight - 40) / (297 * 3.7795);
  canvasState.scale = Math.max(0.1, Math.min(scaleW, scaleH, 1.5));
  wrapper.style.transform = 'scale(' + canvasState.scale + ')';
}

// ── Template list ───────────────────────────────────────────────

async function canvasLoadTemplateList() {
  const sel = document.getElementById('canvas-template-select');
  if (!sel) return;
  sel.innerHTML = '<option value="">loading...</option>';
  try {
    const resp = await fetch('/api/test/pdf/template/list');
    if (!resp.ok) { sel.innerHTML = '<option value="">load failed</option>'; return; }
    const data = await resp.json();
    sel.innerHTML = '<option value="">-- select --</option>';
    (data.templates || []).forEach(t => {
      sel.innerHTML += '<option value="' + t + '">' + t.replace('.json','') + '</option>';
    });
  } catch(e) { sel.innerHTML = '<option value="">error</option>'; }
}

async function canvasLoadTemplate() {
  const name = document.getElementById('canvas-template-select').value;
  if (!name) return;
  try {
    const resp = await fetch('/api/test/pdf/template/' + name);
    if (!resp.ok) throw new Error('Not found');
    canvasState.schema = await resp.json();
    canvasState.selectedElId = null;
    canvasUpdateProps();
    document.getElementById('canvas-save-name').value = canvasState.schema.name || name;
    await canvasRefreshPreview();
    showToast('Loaded: ' + (canvasState.schema.name || name), 'success');
  } catch(e) { showToast('Load failed: ' + e.message, 'error'); }
}

// ── Preview iframe ──────────────────────────────────────────────

async function canvasRefreshPreview() {
  if (!canvasState.schema) return;
  const ctx = canvasBuildSampleContext();
  try {
    const resp = await fetch('/api/test/pdf/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ schema: canvasState.schema, context: ctx }),
    });
    if (!resp.ok) throw new Error('Preview failed');
    const html = await resp.text();
    const iframe = document.getElementById('preview-iframe');
    iframe.srcdoc = html;
    // Re-fit scale after iframe loads
    setTimeout(canvasFitScale, 200);
  } catch(e) { showToast('Preview failed: ' + e.message, 'error'); }
}

function canvasBuildSampleContext() {
  const ctx = {};
  (canvasState.schema.elements || []).forEach(el => {
    if (!el.bind) return;
    if (el.type === 'table') {
      const row = {};
      (el.columns || []).forEach(c => { row[c.bind] = '•••'; });
      ctx[el.bind] = [row, row, row];
    } else if (el.type === 'text') {
      ctx[el.bind] = el.prefix ? (el.prefix + el.bind) : (el.bind || '');
    } else if (el.type === 'signature') {
      ctx[el.bind] = 'AC';
    } else if (el.type === 'image') {
      ctx[el.bind] = '';
    }
  });
  return ctx;
}

// ── Selection via iframe message ─────────────────────────────────

function canvasOnIframeMessage(e) {
  if (e.data && e.data.type === 'selectEl') {
    canvasState.selectedElId = e.data.elId;
    canvasUpdateProps();
  }
}

function canvasFindEl(id) {
  if (!canvasState.schema) return null;
  return (canvasState.schema.elements || []).find(e => e.id === id);
}

// ── Properties Panel ─────────────────────────────────────────────

function canvasUpdateProps() {
  const container = document.getElementById('canvas-props-content');
  const elId = canvasState.selectedElId;
  if (!elId) {
    container.innerHTML = '<span style="color:var(--subtext0);font-size:12px;">Click an element in the preview to edit its properties.</span>';
    return;
  }
  const el = canvasFindEl(elId);
  if (!el) { container.innerHTML = 'Element not found'; return; }

  let html = '<div style="margin-bottom:8px;"><strong>' + el.id + '</strong> <span style="color:var(--subtext0);font-size:10px;">(' + el.type + ')</span></div>';

  // Position
  html += '<div class="form-grid cols-2" style="margin-bottom:8px;">';
  html += '<div class="form-group"><label>X (mm)</label><input type="number" value="' + (el.x||0) + '" onchange="canvasSetProp(\'x\',parseFloat(this.value))" step="1"></div>';
  html += '<div class="form-group"><label>Y (mm)</label><input type="number" value="' + (el.y||0) + '" onchange="canvasSetProp(\'y\',parseFloat(this.value))" step="1"></div>';
  if (el.w !== undefined) html += '<div class="form-group"><label>Width (mm)</label><input type="number" value="' + (el.w||0) + '" onchange="canvasSetProp(\'w\',parseFloat(this.value))" step="1"></div>';
  if (el.h !== undefined) html += '<div class="form-group"><label>Height (mm)</label><input type="number" value="' + (el.h||0) + '" onchange="canvasSetProp(\'h\',parseFloat(this.value))" step="1"></div>';
  html += '</div>';

  // Table columns
  if (el.type === 'table' && el.columns) {
    html += '<div style="margin-bottom:4px;font-weight:600;font-size:11px;">Columns</div>';
    el.columns.forEach((col, i) => {
      html += '<div class="form-grid cols-3" style="margin-bottom:4px; font-size:10px;">';
      html += '<input value="' + (col.header||'') + '" onchange="canvasSetCol(' + i + ',\'header\',this.value)" placeholder="Hdr" style="width:60px">';
      html += '<input value="' + (col.bind||'') + '" onchange="canvasSetCol(' + i + ',\'bind\',this.value)" placeholder="bind" style="width:60px">';
      html += '<div style="display:flex; gap:4px; align-items:center;">';
      html += '<input type="number" value="' + (col.width||0) + '" onchange="canvasSetCol(' + i + ',\'width\',parseFloat(this.value))" step="1" style="width:50px">mm';
      html += '<button class="btn-del" onclick="canvasRemoveCol(' + i + ')" style="font-size:10px;">×</button>';
      html += '</div></div>';
    });
    html += '<button class="btn btn-secondary btn-sm" onclick="canvasAddCol()" style="margin-top:4px;">+ Column</button>';
  }

  container.innerHTML = html;
}

function canvasSetProp(prop, value) {
  const el = canvasFindEl(canvasState.selectedElId);
  if (!el) return;
  el[prop] = value;
  canvasRefreshPreview();
}

function canvasSetCol(idx, key, value) {
  const el = canvasFindEl(canvasState.selectedElId);
  if (!el || !el.columns) return;
  el.columns[idx][key] = value;
  canvasRefreshPreview();
}

function canvasAddCol() {
  const el = canvasFindEl(canvasState.selectedElId);
  if (!el || !el.columns) return;
  el.columns.push({ header: 'New', bind: 'new_field', width: 20, align: 'left' });
  canvasRefreshPreview();
}

function canvasRemoveCol(idx) {
  const el = canvasFindEl(canvasState.selectedElId);
  if (!el || !el.columns || el.columns.length <= 1) return;
  el.columns.splice(idx, 1);
  canvasRefreshPreview();
}

// ── Save ─────────────────────────────────────────────────────────

async function canvasSave() {
  if (!canvasState.schema) { showToast('No schema loaded', 'error'); return; }
  const name = document.getElementById('canvas-save-name').value.trim();
  if (!name) { showToast('Enter a save name', 'error'); return; }
  canvasState.schema.name = name.replace('.json', '');

  try {
    const resp = await fetch('/api/test/pdf/template/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: canvasState.schema.name, schema: canvasState.schema }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    showToast('Saved: ' + canvasState.schema.name, 'success');
    canvasLoadTemplateList();
  } catch(e) { showToast('Save failed: ' + e.message, 'error'); }
}

// ── Export PDF ───────────────────────────────────────────────────

async function canvasExportPdf() {
  if (!canvasState.schema) { showToast('Load a template first', 'error'); return; }
  const btn = document.querySelector('#tab-canvas .btn-success');
  const origText = btn.textContent;
  btn.textContent = '⏳ Generating...';
  btn.disabled = true;

  const ctx = canvasBuildSampleContext();
  try {
    const resp = await fetch('/api/test/pdf/generate-schema', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ schema: canvasState.schema, context: ctx }),
    });
    if (!resp.ok) throw new Error((await resp.json()).error || 'Unknown error');
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = (canvasState.schema.name || 'output') + '.pdf';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast('PDF exported!', 'success');
  } catch(e) { showToast('Export failed: ' + e.message, 'error'); }
  btn.textContent = origText;
  btn.disabled = false;
}

// ═══════════════════════════════════════════════════════════════════
// Init
// ═══════════════════════════════════════════════════════════════════

voLoadSample();
poLoadSample();
