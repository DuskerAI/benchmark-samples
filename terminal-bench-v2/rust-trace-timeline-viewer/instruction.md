Repair `/app` offline trace viewer; do not modify `/app/task_file/fixtures`.

## Tracepack

- `/app/task_file` builds with `cargo build --locked`; keep `tracepack <input-bundle> <timeline-json> <report-json>`.
- Read UTF-8 JSON with escapes/non-ASCII. Bad JSON/args/source/unit exit nonzero.
- Sources `{id,group,unit,origin?,offsetNs?}`; items `{id,kind,source,lane,label,category,ts,dur?,unit?,value?}`. Units: `ns`, `us`, `ms`, `s`. Convert source-unit `origin` to ns; `offsetNs` is ns; item `ts`/`dur` use item unit else source unit.
- Timeline `{version:1,range:{startNs,endNs},lanes,index}`. Lanes are `group/lane`, merged by group/lane but separated by activity/counter kind, with `key`, `kind`, `group`, `lane`, `maxDepth`, `items`.
- Sort lanes by key/kind; items by startNs, endNs, lane key, id. Items have id, kind, label, category, source, startNs, endNs, depth, relevant value. Pack spans into lowest free half-open rows; events/counters depth 0; `index` maps id to `{lane,kind,ordinal}`.
- Report `{version:1,range,lanes,counters}`; lane rows have key, kind, count, firstNs, lastNs; counters have key, min, max, last.

## Browser

- Build with `npm run build`, serve `/app/public`, load `/data/timeline.json`, and use required controls `#filter`, `#from-ns`, `#to-ns`, `#apply-window`, `#reset-window`, `#bookmark`, `#bookmarks`, `#details`, `#export`.
- Timeline items are `.entry`/`.sample` with `data-id`, `data-lane`, `data-start`, `data-end`; bookmarks/non-items must not use `data-id`.
- Filter by id, label, category, source, kind, lane key. Selection/bookmarks use item ids. `#bookmark` toggles selected id. Bookmark buttons display ids and restore items hidden by filter/window.
- `#export` has window, filter, selectedId, sorted bookmarks, visibleIds in display order.
