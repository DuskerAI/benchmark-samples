const params = new URLSearchParams(location.search);
const logPath = params.get('log') || '/app/data/session.log';
const logEl = document.getElementById('log');
const statusEl = document.getElementById('status');
const searchEl = document.getElementById('search');
const exportEl = document.getElementById('export');
const outEl = document.getElementById('export-json');
let lastRows = [];

function esc(s) {
  return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function load(q = '') {
  const res = await fetch(`/api/render?log=${encodeURIComponent(logPath)}&q=${encodeURIComponent(q)}`);
  const data = await res.json();
  lastRows = data.rows || [];
  draw(data, q);
}

function draw(data, q) {
  logEl.innerHTML = '';
  let group = '';
  for (const row of data.rows || []) {
    if (row.group && row.group !== group) {
      group = row.group;
      const b = document.createElement('button');
      b.className = 'group-toggle';
      b.type = 'button';
      b.setAttribute('aria-expanded', 'true');
      b.textContent = `Group ${group}`;
      b.addEventListener('click', () => {
        const hide = b.getAttribute('aria-expanded') === 'true';
        b.setAttribute('aria-expanded', hide ? 'false' : 'true');
        for (const el of logEl.querySelectorAll(`[data-group="${CSS.escape(group)}"]`)) el.hidden = hide;
      });
      logEl.appendChild(b);
    }
    const div = document.createElement('div');
    div.className = `row ${row.level || 'info'}`;
    div.tabIndex = 0;
    div.dataset.group = row.group || '';
    div.dataset.index = row.index;
    div.setAttribute('role', 'listitem');
    const text = String(row.text || '');
    if (q && row.match) div.innerHTML = esc(text).replace(new RegExp(esc(q), 'ig'), m => `<mark class="match">${m}</mark>`);
    else div.textContent = text;
    logEl.appendChild(div);
  }
  statusEl.textContent = `${data.rows.length} rows, ${data.matches} matches`;
}

searchEl.addEventListener('keydown', e => {
  if (e.key === 'Enter') load(searchEl.value);
});

document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
    e.preventDefault();
    searchEl.focus();
  } else if (e.key === 'ArrowDown' && document.activeElement.classList.contains('row')) {
    const rows = Array.from(document.querySelectorAll('.row:not([hidden])'));
    const at = rows.indexOf(document.activeElement);
    if (rows[at + 1]) rows[at + 1].focus();
  } else if (e.altKey && e.key.toLowerCase() === 'e') {
    exportVisible();
  }
});

async function exportVisible() {
  const res = await fetch(`/api/export?log=${encodeURIComponent(logPath)}&q=${encodeURIComponent('')}`);
  outEl.value = JSON.stringify(await res.json());
}

exportEl.addEventListener('click', exportVisible);
load();
