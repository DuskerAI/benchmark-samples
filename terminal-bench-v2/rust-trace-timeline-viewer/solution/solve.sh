#!/bin/bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path

path = Path('/app/task_file/src/main.rs')
text = path.read_text()
replacements = {
    '"ms" => Ok(1_000),': '"ms" => Ok(1_000_000),',
    'out.push(match esc { b\'"\' => \'"\', b\'\\\\\' => \'\\\\\', b\'/\' => \'/\', b\'n\' => \'\\n\', b\'r\' => \'\\r\', b\'t\' => \'\\t\', b\'b\' => \'\\x08\', b\'f\' => \'\\x0c\', _ => return Err("bad escape".to_string()) });': 'let ch = match esc {\n                        b\'"\' => \'"\',\n                        b\'\\\\\' => \'\\\\\',\n                        b\'/\' => \'/\',\n                        b\'n\' => \'\\n\',\n                        b\'r\' => \'\\r\',\n                        b\'t\' => \'\\t\',\n                        b\'b\' => \'\\x08\',\n                        b\'f\' => \'\\x0c\',\n                        b\'u\' => {\n                            let hex = self.input.get(self.pos..self.pos + 4).ok_or("bad escape")?;\n                            self.pos += 4;\n                            let hex_text = std::str::from_utf8(hex).map_err(|_| "bad escape")?;\n                            let code = u32::from_str_radix(hex_text, 16).map_err(|_| "bad escape")?;\n                            char::from_u32(code).ok_or_else(|| "bad escape".to_string())?\n                        }\n                        _ => return Err("bad escape".to_string()),\n                    };\n                    out.push(ch);',
    'let start = source.origin + source.offset_ns + item.ts * scale(&source.unit)?;\n        let end = if item.kind == "span" { start + item.dur * scale(&source.unit)? } else { start };': 'let unit = item.unit.as_deref().unwrap_or(&source.unit);\n        let start = source.origin * scale(&source.unit)? + source.offset_ns + item.ts * scale(unit)?;\n        let end = if item.kind == "span" { start + item.dur.max(0) * scale(unit)? } else { start };',
    'grouped.entry((item.lane.clone(), kind)).or_default().push(TimelineItem { id: item.id, kind: item.kind, label: item.label, category: item.category, source: source.id.clone(), start_ns: start, end_ns: end, depth: 0, value: item.value });': 'let key = format!("{}/{}", source.group, item.lane);\n        grouped.entry((key, kind)).or_default().push(TimelineItem { id: item.id, kind: item.kind, label: item.label, category: item.category, source: source.id.clone(), start_ns: start, end_ns: end, depth: 0, value: item.value });',
    'let last = lane.items.last().map(|x| x.end_ns.max(x.start_ns)).unwrap_or(first);': 'let last = lane.items.iter().map(|x| x.end_ns.max(x.start_ns)).max().unwrap_or(first);',
    'lane_items.sort_by(|a, b| a.id.cmp(&b.id));\n        for (i, item) in lane_items.iter_mut().enumerate() { item.depth = i as u32; }\n        lanes.push(Lane { key: key.clone(), kind, group: key.clone(), lane: key, max_depth: lane_items.len() as u32, items: lane_items });': 'lane_items.sort_by(|a, b| a.start_ns.cmp(&b.start_ns).then(a.end_ns.cmp(&b.end_ns)).then(a.id.cmp(&b.id)));\n        let mut occupied: Vec<i64> = Vec::new();\n        let mut max_depth = 0u32;\n        for item in lane_items.iter_mut() {\n            if item.kind != "span" { item.depth = 0; continue; }\n            let mut depth = 0usize;\n            while depth < occupied.len() && occupied[depth] > item.start_ns { depth += 1; }\n            if depth == occupied.len() { occupied.push(item.end_ns); } else { occupied[depth] = item.end_ns; }\n            item.depth = depth as u32;\n            max_depth = max_depth.max(item.depth + 1);\n        }\n        let mut parts = key.splitn(2, \'/\');\n        let group = parts.next().unwrap_or("").to_string();\n        let lane_name = parts.next().unwrap_or("").to_string();\n        lanes.push(Lane { key, kind, group, lane: lane_name, max_depth, items: lane_items });',
}
for old, new in replacements.items():
    if old not in text:
        raise SystemExit(f'missing replacement: {old[:60]}')
    text = text.replace(old, new)
path.write_text(text)
PY

cat > /app/src/app.ts <<'TS'
type Range = { startNs: number; endNs: number };
type TimelineItem = { id: string; kind: string; label: string; category: string; source: string; startNs: number; endNs: number; depth: number; value?: number };
type Lane = { key: string; kind: string; group: string; lane: string; maxDepth: number; items: TimelineItem[] };
type Timeline = { version: number; range: Range; lanes: Lane[] };

let timeline: Timeline | null = null;
let windowRange: Range | null = null;
let filterText = "";
let selectedId: string | null = null;
const bookmarks = new Set<string>();
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

function clampWindow(start: number, end: number, range: Range): Range {
  const low = Math.max(range.startNs, Math.min(start, end));
  const high = Math.min(range.endNs, Math.max(start, end));
  return high > low ? { startNs: low, endNs: high } : { ...range };
}

function matches(item: TimelineItem, lane: Lane): boolean {
  const q = filterText.trim().toLowerCase();
  if (!q) return true;
  return [item.id, item.label, item.category, item.source, lane.key, item.kind].some((value) => value.toLowerCase().includes(q));
}

function overlaps(item: TimelineItem, range: Range): boolean {
  const end = item.endNs === item.startNs ? item.startNs + 1 : item.endNs;
  return end > range.startNs && item.startNs < range.endNs;
}

function visibleItems(): Array<{ lane: Lane; item: TimelineItem }> {
  if (!timeline || !windowRange) return [];
  const rows: Array<{ lane: Lane; item: TimelineItem }> = [];
  for (const lane of timeline.lanes) {
    for (const item of lane.items) if (matches(item, lane) && overlaps(item, windowRange)) rows.push({ lane, item });
  }
  return rows;
}

function position(item: TimelineItem, range: Range) {
  const span = Math.max(1, range.endNs - range.startNs);
  const end = item.endNs === item.startNs ? item.startNs + Math.max(1, Math.floor(span / 200)) : item.endNs;
  const left = ((item.startNs - range.startNs) / span) * 100;
  const width = ((end - item.startNs) / span) * 100;
  return { left: Math.max(0, Math.min(100, left)), width: Math.max(0.35, Math.min(100, width)) };
}

function syncControls() {
  if (!windowRange) return;
  ($<HTMLInputElement>("from-ns")).value = String(windowRange.startNs);
  ($<HTMLInputElement>("to-ns")).value = String(windowRange.endNs);
}

function findById(id: string | null): { lane: Lane; item: TimelineItem } | null {
  if (!timeline || !id) return null;
  for (const lane of timeline.lanes) {
    const item = lane.items.find((candidate) => candidate.id === id);
    if (item) return { lane, item };
  }
  return null;
}

function renderDetails() {
  const selected = findById(selectedId);
  $("details").textContent = selected ? `${selected.item.id} | ${selected.item.label} | ${selected.item.category} | ${selected.item.source} | ${selected.lane.key} | ${selected.item.startNs}-${selected.item.endNs}` : "No selection";
}

function renderBookmarks() {
  const list = $("bookmarks");
  list.innerHTML = "";
  [...bookmarks].sort().forEach((id) => {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.textContent = id;
    button.addEventListener("click", () => { selectedId = id; draw(); });
    li.appendChild(button);
    list.appendChild(li);
  });
}

function renderExport() {
  ($<HTMLTextAreaElement>("export")).value = JSON.stringify({ window: windowRange, filter: filterText, selectedId, bookmarks: [...bookmarks].sort(), visibleIds: visibleItems().map(({ item }) => item.id) }, null, 2);
}

function draw() {
  if (!timeline || !windowRange) return;
  const root = $("timeline");
  root.innerHTML = "";
  const range = windowRange;
  for (const lane of timeline.lanes) {
    const items = lane.items.filter((item) => matches(item, lane) && overlaps(item, range));
    if (items.length === 0) continue;
    const row = document.createElement("section");
    row.className = "lane";
    row.dataset.key = lane.key;
    row.dataset.kind = lane.kind;
    const title = document.createElement("h2");
    title.textContent = `${lane.key} ${lane.kind}`;
    row.appendChild(title);
    const rail = document.createElement("div");
    rail.className = "rail";
    for (const item of items) {
      const el = document.createElement("button");
      el.className = lane.kind === "counter" ? "sample" : "entry";
      el.dataset.id = item.id;
      el.dataset.lane = lane.key;
      el.dataset.start = String(item.startNs);
      el.dataset.end = String(item.endNs);
      const pos = position(item, range);
      el.style.setProperty("--left", `${pos.left}%`);
      el.style.setProperty("--width", `${pos.width}%`);
      el.style.left = `${pos.left}%`;
      el.style.width = `${pos.width}%`;
      el.style.top = `${8 + item.depth * 24}px`;
      el.textContent = lane.kind === "counter" ? `${item.label} ${item.value ?? ""}` : item.label;
      el.setAttribute("aria-pressed", selectedId === item.id ? "true" : "false");
      if (bookmarks.has(item.id)) el.classList.add("bookmarked");
      el.addEventListener("click", () => { selectedId = item.id; draw(); });
      rail.appendChild(el);
    }
    rail.style.minHeight = `${Math.max(48, (lane.maxDepth + 1) * 28)}px`;
    row.appendChild(rail);
    root.appendChild(row);
  }
  renderDetails();
  renderBookmarks();
  renderExport();
}

async function main() {
  timeline = await fetch("/data/timeline.json").then((response) => response.json());
  windowRange = { ...timeline!.range };
  syncControls();
  ($<HTMLInputElement>("filter")).addEventListener("input", (event) => { filterText = (event.target as HTMLInputElement).value; draw(); });
  $("apply-window").addEventListener("click", () => { windowRange = clampWindow(Number(($<HTMLInputElement>("from-ns")).value), Number(($<HTMLInputElement>("to-ns")).value), timeline!.range); syncControls(); draw(); });
  $("reset-window").addEventListener("click", () => { windowRange = { ...timeline!.range }; filterText = ""; ($<HTMLInputElement>("filter")).value = ""; syncControls(); draw(); });
  $("bookmark").addEventListener("click", () => { if (!selectedId) return; bookmarks.has(selectedId) ? bookmarks.delete(selectedId) : bookmarks.add(selectedId); draw(); });
  draw();
}

main().catch((error) => { $("timeline").textContent = String(error); });
TS

cd /app/task_file
cargo build --locked
mkdir -p /app/public/data
./target/debug/tracepack fixtures/session.json /app/public/data/timeline.json /app/public/data/report.json
cd /app
npm run build
