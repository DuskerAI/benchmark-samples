use std::collections::{BTreeMap, HashMap};
use std::env;
use std::fmt::Write;
use std::fs;

#[derive(Clone, Debug)]
enum Json {
    Null,
    Bool,
    Number(f64),
    String(String),
    Array(Vec<Json>),
    Object(BTreeMap<String, Json>),
}

struct Parser<'a> {
    input: &'a [u8],
    pos: usize,
}

#[derive(Clone)]
struct Source {
    id: String,
    group: String,
    unit: String,
    origin: i64,
    offset_ns: i64,
}

#[derive(Clone)]
struct Item {
    id: String,
    kind: String,
    source: String,
    lane: String,
    label: String,
    category: String,
    ts: i64,
    dur: i64,
    unit: Option<String>,
    value: Option<f64>,
}

#[derive(Clone)]
struct TimelineItem {
    id: String,
    kind: String,
    label: String,
    category: String,
    source: String,
    start_ns: i64,
    end_ns: i64,
    depth: u32,
    value: Option<f64>,
}

struct Lane {
    key: String,
    kind: String,
    group: String,
    lane: String,
    max_depth: u32,
    items: Vec<TimelineItem>,
}

impl<'a> Parser<'a> {
    fn new(text: &'a str) -> Self {
        Self { input: text.as_bytes(), pos: 0 }
    }
    fn parse(mut self) -> Result<Json, String> {
        let value = self.value()?;
        self.ws();
        if self.pos != self.input.len() { return Err("trailing input".to_string()); }
        Ok(value)
    }
    fn ws(&mut self) {
        while self.pos < self.input.len() && self.input[self.pos].is_ascii_whitespace() { self.pos += 1; }
    }
    fn eat(&mut self, b: u8) -> Result<(), String> {
        self.ws();
        if self.input.get(self.pos) == Some(&b) { self.pos += 1; Ok(()) } else { Err(format!("expected {}", b as char)) }
    }
    fn value(&mut self) -> Result<Json, String> {
        self.ws();
        match self.input.get(self.pos).copied() {
            Some(b'{') => self.object(),
            Some(b'[') => self.array(),
            Some(b'"') => Ok(Json::String(self.string()?)),
            Some(b'-') | Some(b'0'..=b'9') => self.number(),
            Some(b't') => { self.keyword(b"true")?; Ok(Json::Bool) }
            Some(b'f') => { self.keyword(b"false")?; Ok(Json::Bool) }
            Some(b'n') => { self.keyword(b"null")?; Ok(Json::Null) }
            _ => Err("invalid json".to_string()),
        }
    }
    fn keyword(&mut self, word: &[u8]) -> Result<(), String> {
        if self.input.get(self.pos..self.pos + word.len()) == Some(word) { self.pos += word.len(); Ok(()) } else { Err("invalid literal".to_string()) }
    }
    fn string(&mut self) -> Result<String, String> {
        self.eat(b'"')?;
        let mut out = String::new();
        while let Some(b) = self.input.get(self.pos).copied() {
            self.pos += 1;
            match b {
                b'"' => return Ok(out),
                b'\\' => {
                    let esc = self.input.get(self.pos).copied().ok_or("bad escape")?;
                    self.pos += 1;
                    out.push(match esc { b'"' => '"', b'\\' => '\\', b'/' => '/', b'n' => '\n', b'r' => '\r', b't' => '\t', b'b' => '\x08', b'f' => '\x0c', _ => return Err("bad escape".to_string()) });
                }
                _ => out.push(b as char),
            }
        }
        Err("unterminated string".to_string())
    }
    fn number(&mut self) -> Result<Json, String> {
        self.ws();
        let start = self.pos;
        if self.input.get(self.pos) == Some(&b'-') { self.pos += 1; }
        while matches!(self.input.get(self.pos), Some(b'0'..=b'9')) { self.pos += 1; }
        if self.input.get(self.pos) == Some(&b'.') {
            self.pos += 1;
            while matches!(self.input.get(self.pos), Some(b'0'..=b'9')) { self.pos += 1; }
        }
        if matches!(self.input.get(self.pos), Some(b'e') | Some(b'E')) {
            self.pos += 1;
            if matches!(self.input.get(self.pos), Some(b'+') | Some(b'-')) { self.pos += 1; }
            while matches!(self.input.get(self.pos), Some(b'0'..=b'9')) { self.pos += 1; }
        }
        let text = std::str::from_utf8(&self.input[start..self.pos]).map_err(|_| "bad number")?;
        Ok(Json::Number(text.parse::<f64>().map_err(|_| "bad number")?))
    }
    fn array(&mut self) -> Result<Json, String> {
        self.eat(b'[')?;
        let mut values = Vec::new();
        self.ws();
        if self.input.get(self.pos) == Some(&b']') { self.pos += 1; return Ok(Json::Array(values)); }
        loop {
            values.push(self.value()?);
            self.ws();
            if self.input.get(self.pos) == Some(&b']') { self.pos += 1; break; }
            self.eat(b',')?;
        }
        Ok(Json::Array(values))
    }
    fn object(&mut self) -> Result<Json, String> {
        self.eat(b'{')?;
        let mut map = BTreeMap::new();
        self.ws();
        if self.input.get(self.pos) == Some(&b'}') { self.pos += 1; return Ok(Json::Object(map)); }
        loop {
            let key = self.string()?;
            self.eat(b':')?;
            map.insert(key, self.value()?);
            self.ws();
            if self.input.get(self.pos) == Some(&b'}') { self.pos += 1; break; }
            self.eat(b',')?;
        }
        Ok(Json::Object(map))
    }
}

fn obj<'a>(v: &'a Json) -> Result<&'a BTreeMap<String, Json>, String> { if let Json::Object(m) = v { Ok(m) } else { Err("expected object".to_string()) } }
fn arr<'a>(v: &'a Json) -> Result<&'a Vec<Json>, String> { if let Json::Array(a) = v { Ok(a) } else { Err("expected array".to_string()) } }
fn field<'a>(m: &'a BTreeMap<String, Json>, key: &str) -> Result<&'a Json, String> { m.get(key).ok_or_else(|| format!("missing {key}")) }
fn str_field(m: &BTreeMap<String, Json>, key: &str) -> Result<String, String> { if let Json::String(s) = field(m, key)? { Ok(s.clone()) } else { Err(format!("bad {key}")) } }
fn i_field(m: &BTreeMap<String, Json>, key: &str, default: i64) -> Result<i64, String> { match m.get(key) { Some(Json::Number(n)) => Ok(*n as i64), None => Ok(default), _ => Err(format!("bad {key}")) } }
fn f_opt(m: &BTreeMap<String, Json>, key: &str) -> Result<Option<f64>, String> { match m.get(key) { Some(Json::Number(n)) => Ok(Some(*n)), None | Some(Json::Null) => Ok(None), _ => Err(format!("bad {key}")) } }
fn s_opt(m: &BTreeMap<String, Json>, key: &str) -> Result<Option<String>, String> { match m.get(key) { Some(Json::String(s)) => Ok(Some(s.clone())), None | Some(Json::Null) => Ok(None), _ => Err(format!("bad {key}")) } }

fn parse_bundle(text: &str) -> Result<(Vec<Source>, Vec<Item>), String> {
    let root = Parser::new(text).parse()?;
    let map = obj(&root)?;
    let mut sources = Vec::new();
    for value in arr(field(map, "sources")?)? {
        let m = obj(value)?;
        sources.push(Source { id: str_field(m, "id")?, group: str_field(m, "group")?, unit: str_field(m, "unit")?, origin: i_field(m, "origin", 0)?, offset_ns: i_field(m, "offsetNs", 0)? });
    }
    let mut items = Vec::new();
    for value in arr(field(map, "items")?)? {
        let m = obj(value)?;
        items.push(Item { id: str_field(m, "id")?, kind: str_field(m, "kind")?, source: str_field(m, "source")?, lane: str_field(m, "lane")?, label: str_field(m, "label")?, category: str_field(m, "category")?, ts: i_field(m, "ts", 0)?, dur: i_field(m, "dur", 0)?, unit: s_opt(m, "unit")?, value: f_opt(m, "value")? });
    }
    Ok((sources, items))
}

fn scale(unit: &str) -> Result<i64, String> {
    match unit { "ns" => Ok(1), "us" => Ok(1_000), "ms" => Ok(1_000), "s" => Ok(1_000_000_000), _ => Err(format!("unsupported unit {unit}")) }
}

fn esc(s: &str) -> String {
    let mut out = String::new();
    for ch in s.chars() {
        match ch { '"' => out.push_str("\\\""), '\\' => out.push_str("\\\\"), '\n' => out.push_str("\\n"), '\r' => out.push_str("\\r"), '\t' => out.push_str("\\t"), _ => out.push(ch) }
    }
    out
}

fn push_item(out: &mut String, item: &TimelineItem, indent: &str) {
    write!(out, "{}{{\n{}  \"id\": \"{}\",\n{}  \"kind\": \"{}\",\n{}  \"label\": \"{}\",\n{}  \"category\": \"{}\",\n{}  \"source\": \"{}\",\n{}  \"startNs\": {},\n{}  \"endNs\": {},\n{}  \"depth\": {}", indent, indent, esc(&item.id), indent, esc(&item.kind), indent, esc(&item.label), indent, esc(&item.category), indent, esc(&item.source), indent, item.start_ns, indent, item.end_ns, indent, item.depth).unwrap();
    if let Some(v) = item.value { write!(out, ",\n{}  \"value\": {}", indent, v).unwrap(); }
    write!(out, "\n{}}}", indent).unwrap();
}

fn write_timeline(range: (i64, i64), lanes: &[Lane]) -> String {
    let mut out = String::new();
    write!(out, "{{\n  \"version\": 1,\n  \"range\": {{\n    \"startNs\": {},\n    \"endNs\": {}\n  }},\n  \"lanes\": [", range.0, range.1).unwrap();
    let mut index_rows: Vec<(String, String, String, usize)> = Vec::new();
    for (li, lane) in lanes.iter().enumerate() {
        if li > 0 { out.push(','); }
        write!(out, "\n    {{\n      \"key\": \"{}\",\n      \"kind\": \"{}\",\n      \"group\": \"{}\",\n      \"lane\": \"{}\",\n      \"maxDepth\": {},\n      \"items\": [", esc(&lane.key), esc(&lane.kind), esc(&lane.group), esc(&lane.lane), lane.max_depth).unwrap();
        for (ii, item) in lane.items.iter().enumerate() {
            if ii > 0 { out.push(','); }
            out.push('\n');
            push_item(&mut out, item, "        ");
            index_rows.push((item.id.clone(), lane.key.clone(), lane.kind.clone(), ii));
        }
        out.push_str("\n      ]\n    }");
    }
    out.push_str("\n  ],\n  \"index\": {");
    index_rows.sort_by(|a, b| a.0.cmp(&b.0));
    for (i, (id, lane, kind, ordinal)) in index_rows.iter().enumerate() {
        if i > 0 { out.push(','); }
        write!(out, "\n    \"{}\": {{\n      \"lane\": \"{}\",\n      \"kind\": \"{}\",\n      \"ordinal\": {}\n    }}", esc(id), esc(lane), esc(kind), ordinal).unwrap();
    }
    out.push_str("\n  }\n}\n");
    out
}

fn write_report(range: (i64, i64), lanes: &[Lane]) -> String {
    let mut out = String::new();
    write!(out, "{{\n  \"version\": 1,\n  \"range\": {{\n    \"startNs\": {},\n    \"endNs\": {}\n  }},\n  \"lanes\": [", range.0, range.1).unwrap();
    for (i, lane) in lanes.iter().enumerate() {
        if i > 0 { out.push(','); }
        let first = lane.items.first().map(|x| x.start_ns).unwrap_or(0);
        let last = lane.items.last().map(|x| x.end_ns.max(x.start_ns)).unwrap_or(first);
        write!(out, "\n    {{\n      \"key\": \"{}\",\n      \"kind\": \"{}\",\n      \"count\": {},\n      \"firstNs\": {},\n      \"lastNs\": {}\n    }}", esc(&lane.key), esc(&lane.kind), lane.items.len(), first, last).unwrap();
    }
    out.push_str("\n  ],\n  \"counters\": [");
    let mut first_row = true;
    for lane in lanes.iter().filter(|l| l.kind == "counter") {
        let values: Vec<f64> = lane.items.iter().filter_map(|i| i.value).collect();
        if values.is_empty() { continue; }
        if !first_row { out.push(','); }
        first_row = false;
        let min = values.iter().copied().fold(f64::INFINITY, f64::min);
        let max = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let last = values[values.len() - 1];
        write!(out, "\n    {{\n      \"key\": \"{}\",\n      \"min\": {},\n      \"max\": {},\n      \"last\": {}\n    }}", esc(&lane.key), min, max, last).unwrap();
    }
    out.push_str("\n  ]\n}\n");
    out
}

fn run(input: &str, timeline_out: &str, report_out: &str) -> Result<(), String> {
    let (sources_vec, items) = parse_bundle(&fs::read_to_string(input).map_err(|e| e.to_string())?)?;
    let sources: HashMap<String, Source> = sources_vec.into_iter().map(|s| (s.id.clone(), s)).collect();
    let mut grouped: BTreeMap<(String, String), Vec<TimelineItem>> = BTreeMap::new();
    let mut first = i64::MAX;
    let mut last = i64::MIN;
    for item in items {
        let source = sources.get(&item.source).ok_or_else(|| "missing source".to_string())?;
        let start = source.origin + source.offset_ns + item.ts * scale(&source.unit)?;
        let end = if item.kind == "span" { start + item.dur * scale(&source.unit)? } else { start };
        first = first.min(start);
        last = last.max(end.max(start));
        let kind = if item.kind == "counter" { "counter" } else { "activity" }.to_string();
        grouped.entry((item.lane.clone(), kind)).or_default().push(TimelineItem { id: item.id, kind: item.kind, label: item.label, category: item.category, source: source.id.clone(), start_ns: start, end_ns: end, depth: 0, value: item.value });
    }
    if first == i64::MAX { first = 0; last = 0; }
    let mut lanes = Vec::new();
    for ((key, kind), mut lane_items) in grouped {
        lane_items.sort_by(|a, b| a.id.cmp(&b.id));
        for (i, item) in lane_items.iter_mut().enumerate() { item.depth = i as u32; }
        lanes.push(Lane { key: key.clone(), kind, group: key.clone(), lane: key, max_depth: lane_items.len() as u32, items: lane_items });
    }
    fs::write(timeline_out, write_timeline((first, last), &lanes)).map_err(|e| e.to_string())?;
    fs::write(report_out, write_report((first, last), &lanes)).map_err(|e| e.to_string())?;
    Ok(())
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 4 { eprintln!("usage: tracepack <input-bundle> <timeline-json> <report-json>"); std::process::exit(2); }
    if let Err(err) = run(&args[1], &args[2], &args[3]) { eprintln!("tracepack: {err}"); std::process::exit(1); }
}
