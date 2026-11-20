#!/bin/bash
set -euo pipefail

cat > /opt/shogictl/src/judge.nim <<'NIM'
import std/[json, os, strutils, tables]

type
  Color = enum Black, White
  PieceKind = enum pkP, pkL, pkN, pkS, pkG, pkB, pkR, pkK
  Cell = object
    occ: bool
    color: Color
    kind: PieceKind
    promoted: bool
  Board = array[10, array[10, Cell]]
  Hand = array[PieceKind, int]
  Hands = array[Color, Hand]
  Move = object
    isDrop: bool
    kind: PieceKind
    ff, fr, tf, tr: int
    promote: bool
  Mark = enum cmNone, cmBlack, cmWhite

const order = [pkP, pkL, pkN, pkS, pkG, pkB, pkR]

proc emptyCell(): Cell = Cell(occ: false)

proc opponent(c: Color): Color =
  if c == Black: White else: Black

proc markFor(c: Color): Mark =
  if c == Black: cmBlack else: cmWhite

proc kindChar(k: PieceKind): char =
  case k
  of pkP: 'P'
  of pkL: 'L'
  of pkN: 'N'
  of pkS: 'S'
  of pkG: 'G'
  of pkB: 'B'
  of pkR: 'R'
  of pkK: 'K'

proc kindFromChar(c: char): PieceKind =
  case c.toUpperAscii
  of 'P': pkP
  of 'L': pkL
  of 'N': pkN
  of 'S': pkS
  of 'G': pkG
  of 'B': pkB
  of 'R': pkR
  else: pkK

proc rankNum(c: char): int = ord(c) - ord('a') + 1
proc fileNum(c: char): int = ord(c) - ord('0')
proc inBoard(f, r: int): bool = f >= 1 and f <= 9 and r >= 1 and r <= 9

proc parseBoard(text: string; ok: var bool): Board =
  var b: Board
  ok = true
  for f in 0..9:
    for r in 0..9:
      b[f][r] = emptyCell()
  let rows = text.split('/')
  if rows.len != 9:
    ok = false
    return b
  for ri, row in rows:
    var f = 9
    var i = 0
    while i < row.len:
      var promoted = false
      var ch = row[i]
      if ch == '+':
        promoted = true
        inc i
        if i >= row.len or row[i] notin {'P', 'L', 'N', 'S', 'B', 'R', 'p', 'l', 'n', 's', 'b', 'r'}:
          ok = false
          return b
        ch = row[i]
      if ch.isDigit:
        let n = ord(ch) - ord('0')
        if n <= 0 or f - n < 0:
          ok = false
          return b
        f -= n
      else:
        if ch notin {'P', 'L', 'N', 'S', 'G', 'B', 'R', 'K', 'p', 'l', 'n', 's', 'g', 'b', 'r', 'k'}:
          ok = false
          return b
        if promoted and ch in {'G', 'K', 'g', 'k'}:
          ok = false
          return b
        let col = if ch.isUpperAscii: Black else: White
        b[f][ri + 1] = Cell(occ: true, color: col, kind: kindFromChar(ch), promoted: promoted)
        dec f
      inc i
    if f != 0:
      ok = false
      return b
  b

proc parseHands(text: string; ok: var bool): Hands =
  var h: Hands
  ok = true
  if text == "-": return h
  if text.len == 0:
    ok = false
    return h
  var n = 0
  var haveDigits = false
  for ch in text:
    if ch.isDigit:
      if ch == '0' and not haveDigits:
        ok = false
        return h
      n = n * 10 + ord(ch) - ord('0')
      haveDigits = true
    else:
      if ch notin {'P', 'L', 'N', 'S', 'G', 'B', 'R', 'p', 'l', 'n', 's', 'g', 'b', 'r'}:
        ok = false
        return h
      let count = if n == 0: 1 else: n
      n = 0
      haveDigits = false
      let col = if ch.isUpperAscii: Black else: White
      h[col][kindFromChar(ch)] += count
  if haveDigits:
    ok = false
  h

proc parseInitial(text: string; b: var Board; side: var Color; h: var Hands): bool =
  let parts = text.splitWhitespace()
  if parts.len != 3: return false
  var ok = true
  b = parseBoard(parts[0], ok)
  if not ok: return false
  if parts[1] notin ["b", "w"]: return false
  side = if parts[1] == "b": Black else: White
  h = parseHands(parts[2], ok)
  if not ok: return false
  var blackKings = 0
  var whiteKings = 0
  for f in 1..9:
    for r in 1..9:
      let c = b[f][r]
      if c.occ and c.kind == pkK:
        if c.color == Black: inc blackKings else: inc whiteKings
  if blackKings != 1 or whiteKings != 1: return false
  true

proc cellToken(c: Cell): string =
  if not c.occ: return "1"
  var ch = kindChar(c.kind)
  if c.color == White: ch = ch.toLowerAscii
  if c.promoted: "+" & $ch else: $ch

proc positionKey(b: Board; side: Color; h: Hands): string =
  var parts: seq[string] = @[]
  for r in 1..9:
    for f in countdown(9, 1):
      parts.add(cellToken(b[f][r]))
    parts.add("/")
  parts.add(if side == Black: " b " else: " w ")
  for col in [Black, White]:
    for k in order:
      parts.add($h[col][k])
      parts.add($kindChar(k))
    parts.add("|")
  parts.join("")

proc parseMove(text: string; mv: var Move): bool =
  if text.len == 4 and text[1] == '*':
    if text[0] notin {'P', 'L', 'N', 'S', 'G', 'B', 'R'}: return false
    mv = Move(isDrop: true, kind: kindFromChar(text[0]), tf: fileNum(text[2]), tr: rankNum(text[3]))
    return inBoard(mv.tf, mv.tr)
  if text.len == 4 or (text.len == 5 and text[4] == '+'):
    mv = Move(isDrop: false, ff: fileNum(text[0]), fr: rankNum(text[1]), tf: fileNum(text[2]), tr: rankNum(text[3]), promote: text.len == 5)
    return inBoard(mv.ff, mv.fr) and inBoard(mv.tf, mv.tr)
  false

proc step(v: int): int =
  if v > 0: 1 elif v < 0: -1 else: 0

proc clearPath(b: Board; ff, fr, tf, tr: int): bool =
  let sf = step(tf - ff)
  let sr = step(tr - fr)
  var f = ff + sf
  var r = fr + sr
  while f != tf or r != tr:
    if b[f][r].occ: return false
    f += sf
    r += sr
  true

proc goldMove(col: Color; df, dr: int): bool =
  let rr = if col == Black: -dr else: dr
  (rr == 1 and abs(df) <= 1) or (rr == 0 and abs(df) == 1) or (rr == -1 and df == 0)

proc pseudoLegal(b: Board; p: Cell; ff, fr, tf, tr: int): bool =
  let df = tf - ff
  let dr = tr - fr
  if df == 0 and dr == 0: return false
  let rr = if p.color == Black: -dr else: dr
  if p.promoted and p.kind in {pkP, pkL, pkN, pkS}:
    return goldMove(p.color, df, dr)
  case p.kind
  of pkP:
    df == 0 and rr == 1
  of pkL:
    df == 0 and rr > 0 and clearPath(b, ff, fr, tf, tr)
  of pkN:
    rr == 2 and abs(df) == 1
  of pkS:
    (rr == 1 and abs(df) <= 1) or (rr == -1 and abs(df) == 1)
  of pkG:
    goldMove(p.color, df, dr)
  of pkB:
    (abs(df) == abs(dr) and clearPath(b, ff, fr, tf, tr)) or (p.promoted and abs(df) + abs(dr) == 1)
  of pkR:
    ((df == 0 or dr == 0) and clearPath(b, ff, fr, tf, tr)) or (p.promoted and abs(df) == 1 and abs(dr) == 1)
  of pkK:
    max(abs(df), abs(dr)) == 1

proc inPromoZone(col: Color; r: int): bool =
  if col == Black: r <= 3 else: r >= 7

proc mustPromote(col: Color; k: PieceKind; r: int): bool =
  if k in {pkP, pkL}:
    return (col == Black and r == 1) or (col == White and r == 9)
  if k == pkN:
    return (col == Black and r <= 2) or (col == White and r >= 8)
  false

proc kingPos(b: Board; col: Color): tuple[ok: bool, f: int, r: int] =
  for f in 1..9:
    for r in 1..9:
      let c = b[f][r]
      if c.occ and c.color == col and c.kind == pkK:
        return (true, f, r)
  (false, 0, 0)

proc inCheck(b: Board; col: Color): bool =
  let kp = kingPos(b, col)
  if not kp.ok: return true
  let other = opponent(col)
  for f in 1..9:
    for r in 1..9:
      let c = b[f][r]
      if c.occ and c.color == other and pseudoLegal(b, c, f, r, kp.f, kp.r):
        return true
  false

proc anyLegal(b: Board; h: Hands; side: Color): bool

proc legalMoveObj(b: Board; h: Hands; side: Color; mv: Move; strict: bool; nb: var Board; nh: var Hands): bool =
  nb = b
  nh = h
  if mv.isDrop:
    if mv.kind == pkK or nb[mv.tf][mv.tr].occ or nh[side][mv.kind] <= 0: return false
    if mv.kind in {pkP, pkL} and ((side == Black and mv.tr == 1) or (side == White and mv.tr == 9)): return false
    if mv.kind == pkN and ((side == Black and mv.tr <= 2) or (side == White and mv.tr >= 8)): return false
    if mv.kind == pkP:
      for r in 1..9:
        let c = nb[mv.tf][r]
        if c.occ and c.color == side and c.kind == pkP and not c.promoted: return false
    nb[mv.tf][mv.tr] = Cell(occ: true, color: side, kind: mv.kind, promoted: false)
    dec nh[side][mv.kind]
    if inCheck(nb, side): return false
    if strict and mv.kind == pkP and inCheck(nb, opponent(side)) and not anyLegal(nb, nh, opponent(side)):
      return false
    return true
  if not nb[mv.ff][mv.fr].occ: return false
  var p = nb[mv.ff][mv.fr]
  if p.color != side: return false
  if nb[mv.tf][mv.tr].occ and nb[mv.tf][mv.tr].color == side: return false
  if nb[mv.tf][mv.tr].occ and nb[mv.tf][mv.tr].kind == pkK: return false
  if not pseudoLegal(nb, p, mv.ff, mv.fr, mv.tf, mv.tr): return false
  let canPromote = p.kind notin {pkG, pkK} and not p.promoted and (inPromoZone(side, mv.fr) or inPromoZone(side, mv.tr))
  if mv.promote and not canPromote: return false
  if not mv.promote and mustPromote(side, p.kind, mv.tr): return false
  if nb[mv.tf][mv.tr].occ:
    let cap = nb[mv.tf][mv.tr]
    if cap.kind == pkK: return false
    inc nh[side][cap.kind]
  nb[mv.ff][mv.fr] = emptyCell()
  p.promoted = p.promoted or mv.promote
  nb[mv.tf][mv.tr] = p
  if inCheck(nb, side): return false
  true

proc anyLegal(b: Board; h: Hands; side: Color): bool =
  var nb: Board
  var nh: Hands
  for ff in 1..9:
    for fr in 1..9:
      let c = b[ff][fr]
      if c.occ and c.color == side:
        for tf in 1..9:
          for tr in 1..9:
            if legalMoveObj(b, h, side, Move(isDrop: false, ff: ff, fr: fr, tf: tf, tr: tr, promote: false), false, nb, nh): return true
            if legalMoveObj(b, h, side, Move(isDrop: false, ff: ff, fr: fr, tf: tf, tr: tr, promote: true), false, nb, nh): return true
  for k in order:
    if h[side][k] > 0:
      for f in 1..9:
        for r in 1..9:
          if legalMoveObj(b, h, side, Move(isDrop: true, kind: k, tf: f, tr: r), false, nb, nh): return true
  false

proc legalText(b: Board; h: Hands; side: Color; text: string; nb: var Board; nh: var Hands): bool =
  var mv: Move
  if not parseMove(text, mv): return false
  legalMoveObj(b, h, side, mv, true, nb, nh)

proc outcomeFor(xs: seq[Mark]): string =
  if xs.len >= 4:
    var b = true
    var w = true
    for i in xs.len - 4 .. xs.len - 1:
      b = b and xs[i] == cmBlack
      w = w and xs[i] == cmWhite
    if b: return "black_perpetual_check"
    if w: return "white_perpetual_check"
    return "draw"
  ""

proc inEnteringCamp(col: Color; r: int): bool =
  if col == Black: r <= 3 else: r >= 7

proc materialPoints(b: Board; h: Hands; col: Color): int =
  for k in order:
    let value = if k in {pkB, pkR}: 5 else: 1
    result += h[col][k] * value
  for f in 1..9:
    for r in 1..9:
      let c = b[f][r]
      if c.occ and c.color == col and c.kind != pkK:
        let pieceValue = if c.kind in {pkB, pkR}: 5 else: 1
        result += pieceValue

proc enteringOutcome(b: Board; h: Hands): string =
  let bk = kingPos(b, Black)
  let wk = kingPos(b, White)
  if not bk.ok or not wk.ok: return "ok"
  let blackQualifies = inEnteringCamp(Black, bk.r) and not inCheck(b, Black) and materialPoints(b, h, Black) >= 24
  let whiteQualifies = inEnteringCamp(White, wk.r) and not inCheck(b, White) and materialPoints(b, h, White) >= 24
  if blackQualifies and whiteQualifies: return "jishogi"
  if blackQualifies: return "black_entering_king"
  if whiteQualifies: return "white_entering_king"
  "ok"

proc judgeGame(g: JsonNode): string =
  if g.kind != JObject or not g.hasKey("id") or not g.hasKey("initial") or not g.hasKey("moves"):
    return "illegal:0"
  if g["id"].kind != JString or g["initial"].kind != JString or g["moves"].kind != JArray:
    return "illegal:0"
  var b: Board
  var h: Hands
  var side: Color
  if not parseInitial(g["initial"].getStr, b, side, h): return "illegal:0"
  var seen = initTable[string, seq[Mark]]()
  let firstMark = if inCheck(b, side): markFor(opponent(side)) else: cmNone
  seen[positionKey(b, side, h)] = @[firstMark]
  var ply = 0
  for node in g["moves"].items:
    inc ply
    if node.kind != JString: return "illegal:0"
    let mover = side
    var nb: Board
    var nh: Hands
    if not legalText(b, h, side, node.getStr, nb, nh): return "illegal:" & $ply
    b = nb
    h = nh
    side = opponent(side)
    let checker = if inCheck(b, side): markFor(mover) else: cmNone
    let key = positionKey(b, side, h)
    if not seen.hasKey(key): seen[key] = @[]
    seen[key].add(checker)
    let outc = outcomeFor(seen[key])
    if outc.len > 0: return outc
  enteringOutcome(b, h)

when isMainModule:
  if paramCount() != 2:
    quit(2)
  let games = parseFile(paramStr(1))
  var outJson = newJObject()
  for g in games.items:
    outJson[g["id"].getStr] = %judgeGame(g)
  writeFile(paramStr(2), $outJson)
NIM

make -C /opt/shogictl clean install
