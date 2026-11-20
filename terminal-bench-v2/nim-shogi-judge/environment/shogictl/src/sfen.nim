import std/[strutils]

type
  Color* = enum Black, White
  PieceKind* = enum pkP, pkL, pkN, pkS, pkG, pkB, pkR, pkK
  Cell* = object
    occ*: bool
    color*: Color
    kind*: PieceKind
    promoted*: bool
  Board* = array[10, array[10, Cell]]
  Hand* = array[PieceKind, int]
  Hands* = array[Color, Hand]

const handKinds* = [pkP, pkL, pkN, pkS, pkG, pkB, pkR]

proc emptyCell*(): Cell = Cell(occ: false)

proc kindFromChar*(c: char): PieceKind =
  case c.toUpperAscii
  of 'P': pkP
  of 'L': pkL
  of 'N': pkN
  of 'S': pkS
  of 'G': pkG
  of 'B': pkB
  of 'R': pkR
  else: pkK

proc clearBoard*(): Board =
  for f in 0..9:
    for r in 0..9:
      result[f][r] = emptyCell()

proc parseBoard*(text: string; ok: var bool): Board =
  ok = true
  result = clearBoard()
  let rows = text.split('/')
  if rows.len != 9:
    ok = false
    return
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
          return
        ch = row[i]
      if ch.isDigit:
        let n = ord(ch) - ord('0')
        if n <= 0 or f - n < 0:
          ok = false
          return
        f -= n
      else:
        if ch notin {'P', 'L', 'N', 'S', 'G', 'B', 'R', 'K', 'p', 'l', 'n', 's', 'g', 'b', 'r', 'k'}:
          ok = false
          return
        if promoted and ch in {'G', 'K', 'g', 'k'}:
          ok = false
          return
        let col = if ch.isUpperAscii: Black else: White
        if f >= 1 and f <= 9 and ri + 1 <= 9:
          result[f][ri + 1] = Cell(occ: true, color: col, kind: kindFromChar(ch), promoted: promoted)
        dec f
      inc i
    if f != 0:
      ok = false
      return

proc parseHands*(text: string; ok: var bool): Hands =
  ok = true
  if text == "-": return
  if text.len == 0:
    ok = false
    return
  var n = 0
  var haveDigits = false
  for ch in text:
    if ch.isDigit:
      if ch == '0' and not haveDigits:
        ok = false
        return
      n = n * 10 + ord(ch) - ord('0')
      haveDigits = true
    else:
      if ch notin {'P', 'L', 'N', 'S', 'G', 'B', 'R', 'p', 'l', 'n', 's', 'g', 'b', 'r'}:
        ok = false
        return
      let count = if n == 0: 1 else: n
      n = 0
      haveDigits = false
      let col = if ch.isUpperAscii: Black else: White
      result[col][kindFromChar(ch)] += count
  if haveDigits:
    ok = false

proc parseInitial*(text: string; b: var Board; side: var Color; h: var Hands): bool =
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
  blackKings == 1 and whiteKings == 1
