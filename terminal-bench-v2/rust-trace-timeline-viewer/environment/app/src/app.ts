type Range = { startNs: number; endNs: number };
type TimelineItem = {
  id: string;
  kind: string;
  label: string;
  category: string;
  source: string;
  startNs: number;
  endNs: number;
  depth: number;
  value?: number;
};
type Lane = { key: string; kind: string; group: string; lane: string; maxDepth: number; items: TimelineItem[] };
type Timeline = { version: number; range: Range; lanes: Lane[] };

let timeline: Timeline | null = null;
let viewRange: Range | null = null;
let query = "";
let selected = -1;
const marks: string[] = [];

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

function visible() {
  if (!timeline) return [] as Array<{ lane: Lane; item: TimelineItem }>;
  const out: Array<{ lane: Lane; item: TimelineItem }> = [];
  for (const lane of timeline.lanes) {
    for (const item of lane.items) {
      const haystack = `${item.label} ${item.category}`.toLowerCase();
      if (!query || haystack.includes(query.toLowerCase())) out.push({ lane, item });
    }
  }
  return out;
}

function draw() {
  if (!timeline || !viewRange) return;
  const root = $("timeline");
  root.innerHTML = "";
  for (const lane of timeline.lanes) {
    const items = lane.items.filter((item) => visible().some((row) => row.item.id === item.id));
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
    const full = Math.max(1, timeline.range.endNs - timeline.range.startNs);
    items.forEach((item) => {
      const el = document.createElement("button");
      el.className = lane.kind === "counter" ? "sample" : "entry";
      el.dataset.id = item.id;
      el.dataset.lane = lane.key;
      el.dataset.start = String(item.startNs);
      el.dataset.end = String(item.endNs);
      const left = ((item.startNs - timeline!.range.startNs) / full) * 100;
      const width = Math.max(0.5, (((item.endNs || item.startNs + 1) - item.startNs) / full) * 100);
      el.style.left = `${left}%`;
      el.style.width = `${width}%`;
      el.style.top = `${8 + item.depth * 24}px`;
      el.textContent = lane.kind === "counter" ? `${item.label} ${item.value ?? ""}` : item.label;
      el.setAttribute("aria-pressed", visible()[selected]?.item.id === item.id ? "true" : "false");
      if (marks.includes(item.label)) el.classList.add("bookmarked");
      el.addEventListener("click", () => {
        selected = visible().findIndex((row) => row.item.id === item.id);
        draw();
      });
      rail.appendChild(el);
    });
    rail.style.minHeight = `${Math.max(48, (lane.maxDepth + 1) * 28)}px`;
    row.appendChild(rail);
    root.appendChild(row);
  }
  const row = visible()[selected];
  $("details").textContent = row ? `${row.item.id} | ${row.item.label} | ${row.item.category} | ${row.item.source} | ${row.lane.key}` : "No selection";
  const list = $("bookmarks");
  list.innerHTML = marks.map((mark) => `<li><button>${mark}</button></li>`).join("");
  ($<HTMLTextAreaElement>("export")).value = JSON.stringify({
    window: viewRange,
    filter: query,
    selectedId: row?.item.id ?? null,
    bookmarks: marks,
    visibleIds: visible().map((entry) => entry.item.id),
  }, null, 2);
}

async function main() {
  timeline = await fetch("/data/timeline.json").then((response) => response.json());
  viewRange = { ...timeline!.range };
  ($<HTMLInputElement>("from-ns")).value = String(viewRange.startNs);
  ($<HTMLInputElement>("to-ns")).value = String(viewRange.endNs);
  ($<HTMLInputElement>("filter")).addEventListener("input", (event) => {
    query = (event.target as HTMLInputElement).value;
    draw();
  });
  $("apply-window").addEventListener("click", () => {
    viewRange = {
      startNs: Number(($<HTMLInputElement>("from-ns")).value),
      endNs: Number(($<HTMLInputElement>("to-ns")).value),
    };
    draw();
  });
  $("reset-window").addEventListener("click", () => {
    query = "";
    selected = -1;
    viewRange = { ...timeline!.range };
    ($<HTMLInputElement>("filter")).value = "";
    draw();
  });
  $("bookmark").addEventListener("click", () => {
    const row = visible()[selected];
    if (row && !marks.includes(row.item.label)) marks.push(row.item.label);
    draw();
  });
  draw();
}

main().catch((error) => {
  $("timeline").textContent = String(error);
});
