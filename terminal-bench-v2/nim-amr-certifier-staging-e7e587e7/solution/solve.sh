#!/bin/bash
set -euo pipefail

cat > /app/src/amrcert.nim <<'NIM'
import std/[math, os, strutils]

type
  Params = object
    nx: int
    tEnd, cfl, discontinuity, left, right: float
    maxLevel: int
    sensor: float
    boundary: string
  Cell = object
    lo, hi: float
    level: int
    u: float
  State = object
    time: float
    step: int
    initialMass: float
    cells: seq[Cell]

proc parseCase(path: string): Params =
  let w = readFile(path).splitWhitespace()
  if w.len != 9:
    quit("invalid case", 2)
  result.nx = parseInt(w[0])
  result.tEnd = parseFloat(w[1])
  result.cfl = parseFloat(w[2])
  result.discontinuity = parseFloat(w[3])
  result.left = parseFloat(w[4])
  result.right = parseFloat(w[5])
  result.maxLevel = parseInt(w[6])
  result.sensor = parseFloat(w[7])
  result.boundary = w[8]
  if result.nx < 4 or result.tEnd < 0 or result.cfl <= 0 or result.maxLevel < 0:
    quit("invalid case", 2)
  if result.boundary != "periodic" and result.boundary != "outflow":
    quit("invalid case", 2)

proc fmt(x: float): string =
  var y = x
  if abs(y) < 5e-14:
    y = 0.0
  result = formatFloat(y, ffDecimal, 12)

proc fmtChk(x: float): string =
  var y = x
  if abs(y) < 5e-17:
    y = 0.0
  result = formatFloat(y, ffScientific, 17)

proc fluxVal(u: float): float = 0.5 * u * u

proc godunov(ul, ur: float): float =
  if ul <= ur:
    if ul >= 0.0:
      result = fluxVal(ul)
    elif ur <= 0.0:
      result = fluxVal(ur)
    else:
      result = 0.0
  else:
    if 0.5 * (ul + ur) >= 0.0:
      result = fluxVal(ul)
    else:
      result = fluxVal(ur)

proc mass(cells: seq[Cell]): float =
  for c in cells:
    result += (c.hi - c.lo) * c.u

proc totalVariation(cells: seq[Cell], periodic: bool): float =
  if cells.len == 0:
    return 0.0
  for i in 1 ..< cells.len:
    result += abs(cells[i].u - cells[i - 1].u)
  if periodic and cells.len > 1:
    result += abs(cells[0].u - cells[^1].u)

proc initialState(p: Params): State =
  let dx = 1.0 / float(p.nx)
  for i in 0 ..< p.nx:
    let lo = float(i) * dx
    let hi = float(i + 1) * dx
    let mid = 0.5 * (lo + hi)
    let u = if mid < p.discontinuity: p.left else: p.right
    result.cells.add Cell(lo: lo, hi: hi, level: 0, u: u)
  result.time = 0.0
  result.step = 0
  result.initialMass = mass(result.cells)

proc jumpAt(cells: seq[Cell], i: int, periodic: bool): float =
  let n = cells.len
  var leftU = cells[i].u
  var rightU = cells[i].u
  if i > 0:
    leftU = cells[i - 1].u
  elif periodic and n > 1:
    leftU = cells[^1].u
  if i + 1 < n:
    rightU = cells[i + 1].u
  elif periodic and n > 1:
    rightU = cells[0].u
  result = max(abs(cells[i].u - leftU), abs(cells[i].u - rightU))

proc adapt(st: var State, p: Params) =
  var refined: seq[Cell] = @[]
  let periodic = p.boundary == "periodic"
  for i, c in st.cells:
    if c.level < p.maxLevel and jumpAt(st.cells, i, periodic) > p.sensor:
      let mid = 0.5 * (c.lo + c.hi)
      refined.add Cell(lo: c.lo, hi: mid, level: c.level + 1, u: c.u)
      refined.add Cell(lo: mid, hi: c.hi, level: c.level + 1, u: c.u)
    else:
      refined.add c
  var coarsened: seq[Cell] = @[]
  var i = 0
  while i < refined.len:
    var canMerge = false
    if i + 1 < refined.len and refined[i].level == refined[i + 1].level and refined[i].level > 0:
      var leftJump = 0.0
      var rightJump = 0.0
      if i > 0:
        leftJump = abs(refined[i].u - refined[i - 1].u)
      if i + 2 < refined.len:
        rightJump = abs(refined[i + 1].u - refined[i + 2].u)
      canMerge = max(max(abs(refined[i].u - refined[i + 1].u), leftJump), rightJump) < p.sensor * 0.10
    if canMerge:
      let a = refined[i]
      let b = refined[i + 1]
      let width = b.hi - a.lo
      let u = ((a.hi - a.lo) * a.u + (b.hi - b.lo) * b.u) / width
      coarsened.add Cell(lo: a.lo, hi: b.hi, level: a.level - 1, u: u)
      i += 2
    else:
      coarsened.add refined[i]
      i += 1
  st.cells = coarsened

proc stepSize(st: State, p: Params): float =
  var minDx = st.cells[0].hi - st.cells[0].lo
  var speed = 1e-12
  for c in st.cells:
    minDx = min(minDx, c.hi - c.lo)
    speed = max(speed, abs(c.u))
  result = p.cfl * minDx / speed

proc advance(st: var State, p: Params, dt: float) =
  let n = st.cells.len
  var f = newSeq[float](n + 1)
  if p.boundary == "periodic":
    f[0] = godunov(st.cells[^1].u, st.cells[0].u)
    f[n] = f[0]
  else:
    f[0] = godunov(st.cells[0].u, st.cells[0].u)
    f[n] = godunov(st.cells[^1].u, st.cells[^1].u)
  for i in 1 ..< n:
    f[i] = godunov(st.cells[i - 1].u, st.cells[i].u)
  var next = st.cells
  for i in 0 ..< n:
    let dx = st.cells[i].hi - st.cells[i].lo
    next[i].u = st.cells[i].u + dt / dx * (f[i] - f[i + 1])
  st.cells = next
  st.time += dt
  st.step += 1

proc writeCheckpoint(path: string, st: State) =
  var s = "AMR1\n"
  s.add fmtChk(st.time) & " " & $st.step & " " & fmtChk(st.initialMass) & "\n"
  s.add $st.cells.len & "\n"
  for c in st.cells:
    s.add fmtChk(c.lo) & " " & fmtChk(c.hi) & " " & $c.level & " " & fmtChk(c.u) & "\n"
  writeFile(path, s)

proc readCheckpoint(path: string): State =
  let lines = readFile(path).splitLines()
  if lines.len < 3 or lines[0] != "AMR1":
    quit("invalid checkpoint", 2)
  let h = lines[1].splitWhitespace()
  result.time = parseFloat(h[0])
  result.step = parseInt(h[1])
  result.initialMass = parseFloat(h[2])
  let n = parseInt(lines[2])
  for i in 0 ..< n:
    let w = lines[3 + i].splitWhitespace()
    result.cells.add Cell(lo: parseFloat(w[0]), hi: parseFloat(w[1]), level: parseInt(w[2]), u: parseFloat(w[3]))

proc run(st: var State, p: Params, chk: string, every: int) =
  while st.time < p.tEnd - 1e-13:
    var dt = stepSize(st, p)
    if st.time + dt > p.tEnd:
      dt = p.tEnd - st.time
    if dt <= 1e-15:
      break
    advance(st, p, dt)
    adapt(st, p)
    if chk.len > 0 and every > 0 and st.step mod every == 0:
      writeCheckpoint(chk, st)
  if chk.len > 0:
    writeCheckpoint(chk, st)

proc meshError(cells: seq[Cell]): float =
  if cells.len == 0:
    return 1.0
  result = abs(cells[0].lo)
  for i in 0 ..< cells.len:
    if i + 1 < cells.len:
      result = max(result, abs(cells[i].hi - cells[i + 1].lo))
  result = max(result, abs(cells[^1].hi - 1.0))

proc writeOutput(path: string, st: State, p: Params) =
  var mn = st.cells[0].u
  var mx = st.cells[0].u
  for c in st.cells:
    mn = min(mn, c.u)
    mx = max(mx, c.u)
  let m = mass(st.cells)
  let merr = if p.boundary == "periodic": m - st.initialMass else: 0.0
  var s = "{\n"
  s.add "  \"status\":\"ok\",\n"
  s.add "  \"case\":{\"nx\":" & $p.nx & ",\"boundary\":\"" & p.boundary & "\",\"max_level\":" & $p.maxLevel & "},\n"
  s.add "  \"summary\":{\"time\":" & fmt(st.time) & ",\"steps\":" & $st.step & ",\"cell_count\":" & $st.cells.len & ",\"mass\":" & fmt(m) & ",\"min\":" & fmt(mn) & ",\"max\":" & fmt(mx) & ",\"total_variation\":" & fmt(totalVariation(st.cells, p.boundary == "periodic")) & ",\"mesh_error\":" & fmt(meshError(st.cells)) & ",\"mass_error\":" & fmt(merr) & "},\n"
  s.add "  \"cells\":[\n"
  for i, c in st.cells:
    let comma = if i + 1 < st.cells.len: "," else: ""
    s.add "    {\"lo\":" & fmt(c.lo) & ",\"hi\":" & fmt(c.hi) & ",\"level\":" & $c.level & ",\"u\":" & fmt(c.u) & "}" & comma & "\n"
  s.add "  ]\n"
  s.add "}\n"
  writeFile(path, s)

when isMainModule:
  if paramCount() < 2:
    quit("usage: amrcert CASE OUTPUT [--checkpoint FILE --checkpoint-every N] [--resume FILE]", 2)
  let p = parseCase(paramStr(1))
  let outPath = paramStr(2)
  var chk = ""
  var every = 0
  var resume = ""
  var i = 3
  while i <= paramCount():
    let a = paramStr(i)
    if a == "--checkpoint" and i + 1 <= paramCount():
      chk = paramStr(i + 1)
      i += 2
    elif a == "--checkpoint-every" and i + 1 <= paramCount():
      every = parseInt(paramStr(i + 1))
      i += 2
    elif a == "--resume" and i + 1 <= paramCount():
      resume = paramStr(i + 1)
      i += 2
    else:
      quit("usage: amrcert CASE OUTPUT [--checkpoint FILE --checkpoint-every N] [--resume FILE]", 2)
  var st = if resume.len > 0: readCheckpoint(resume) else: initialState(p)
  if resume.len == 0:
    for k in 0 ..< p.maxLevel:
      adapt(st, p)
  run(st, p, chk, every)
  writeOutput(outPath, st, p)
NIM

make -C /app clean all
