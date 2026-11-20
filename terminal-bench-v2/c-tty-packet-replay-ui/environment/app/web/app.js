(function () {
  const pathInput = document.getElementById('capturePath');
  const searchInput = document.getElementById('search');
  const seekInput = document.getElementById('seek');
  const status = document.getElementById('status');
  const rowsBody = document.getElementById('rows');
  const sessions = document.getElementById('sessions');
  const exportBox = document.getElementById('exportJson');
  let lastData = null;
  let folded = new Set();
  let active = -1;

  const params = new URLSearchParams(location.search);
  if (params.get('capture')) pathInput.value = params.get('capture');

  function visibleRows() {
    if (!lastData) return [];
    return lastData.rows.filter(r => !folded.has(r.session));
  }

  function drawSessions() {
    sessions.textContent = '';
    if (!lastData) return;
    lastData.sessions.forEach(s => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = s.label.trim() || s.id;
      b.dataset.session = s.id;
      b.setAttribute('aria-expanded', folded.has(s.id) ? 'false' : 'true');
      b.addEventListener('click', () => { folded.has(s.id) ? folded.delete(s.id) : folded.add(s.id); active = -1; draw(); });
      sessions.appendChild(b);
    });
  }

  function draw() {
    drawSessions();
    rowsBody.textContent = '';
    const rows = visibleRows();
    rows.forEach((r, i) => {
      const tr = document.createElement('tr');
      tr.dataset.session = r.session;
      tr.dataset.channel = r.channel;
      tr.dataset.row = String(r.row);
      if (i === active) tr.className = 'active';
      [r.ts, r.session, r.channel, r.row, r.text, r.width].forEach((v, ix) => {
        const td = document.createElement('td');
        td.textContent = String(v);
        if (ix === 4) td.className = 'text';
        tr.appendChild(td);
      });
      rowsBody.appendChild(tr);
    });
    status.textContent = lastData ? `${rows.length} visible rows, ${lastData.matches} matches` : '';
  }

  async function load() {
    const qs = new URLSearchParams();
    qs.set('path', pathInput.value);
    qs.set('q', searchInput.value);
    if (seekInput.value) qs.set('seek', seekInput.value);
    qs.set('limit', '256');
    const res = await fetch('/api/replay?' + qs.toString());
    lastData = await res.json();
    if (lastData.error) throw new Error(lastData.error);
    active = -1;
    draw();
  }

  function exportVisible() {
    if (!lastData) return;
    const out = Object.assign({}, lastData, { rows: lastData.rows });
    exportBox.value = JSON.stringify(out, null, 2);
  }

  document.getElementById('load').addEventListener('click', () => load().catch(e => { status.textContent = e.message; }));
  document.getElementById('exportBtn').addEventListener('click', exportVisible);
  searchInput.addEventListener('keydown', e => { if (e.key === 'Enter') load().catch(err => { status.textContent = err.message; }); });
  seekInput.addEventListener('keydown', e => { if (e.key === 'Enter') load().catch(err => { status.textContent = err.message; }); });
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); searchInput.focus(); }
    else if (e.altKey && e.key.toLowerCase() === 'e') { e.preventDefault(); exportVisible(); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const n = visibleRows().length;
      if (n) { e.preventDefault(); active = e.key === 'ArrowDown' ? Math.min(n - 1, active + 1) : Math.max(0, active - 1); draw(); }
    }
  });
  if (pathInput.value) load().catch(e => { status.textContent = e.message; });
})();
