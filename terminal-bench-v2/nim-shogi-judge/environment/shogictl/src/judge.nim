import std/[json, os, strutils, tables]
import sfen

type
  MoveText = object
    drop: bool
    kind: PieceKind
    ff, fr, tf, tr: int
    promote: bool

const handOrder = [pkP, pkL, pkN, pkS, pkG, pkB, pkR]

proc opponent(c: Color): Color =
  if c == Black: White else: Black

proc fileNum(c: char): int = ord(c) - ord('0')
proc rankNum(c: char): int = ord(c) - ord('a') + 1
proc inBoard(f, r: int): bool = f >= 1 and f <= 9 and r >= 1 and r <= 9

proc parseMove(text: string; mv: var MoveText): bool =
  if text.len == 4 and text[1] == '*':
    if text[0] notin {'P', 'L', 'N', 'S', 'G', 'B', 'R'}: return false
    mv = MoveText(drop: true, kind: kindFromChar(text[0]), tf: fileNum(text[2]), tr: rankNum(text[3]))
    return inBoard(mv.tf, mv.tr)
  if text.len == 4 or (text.len == 5 and text[4] == '+'):
    mv = MoveText(drop: false, ff: fileNum(text[0]), fr: rankNum(text[1]), tf: fileNum(text[2]), tr: rankNum(text[3]), promote: text.len == 5)
    return inBoard(mv.ff, mv.fr) and inBoard(mv.tf, mv.tr)
  false

proc boardToken(c: Cell): string =
  if not c.occ: return "1"
  var label = case c.kind
    of pkP: 'P'
    of pkL: 'L'
    of pkN: 'N'
    of pkS: 'S'
    of pkG: 'G'
    of pkB: 'B'
    of pkR: 'R'
    of pkK: 'K'
  if c.color == White: label = label.toLowerAscii
  if c.promoted: "+" & $label else: $label

proc handToken(k: PieceKind): char =
  case k
  of pkP: 'P'
  of pkL: 'L'
  of pkN: 'N'
  of pkS: 'S'
  of pkG: 'G'
  of pkB: 'B'
  of pkR: 'R'
  of pkK: 'K'

proc compactPosition(b: Board; side: Color; h: Hands): string =
  var parts: seq[string] = @[]
  for r in 1..9:
    for f in countdown(9, 1):
      parts.add(boardToken(b[f][r]))
    parts.add("/")
  parts.add(if side == Black: " b" else: " w")
  for color in [Black, White]:
    parts.add("|")
    for k in handOrder:
      parts.add($h[color][k])
      parts.add($handToken(k))
  parts.join("")

proc countHandMaterial(h: Hands; side: Color): int =
  for k in handOrder:
    let value = if k in {pkB, pkR}: 5 else: 1
    result += h[side][k] * value

proc applyBookkeeping(b: var Board; h: var Hands; side: Color; mv: MoveText): bool =
  if mv.drop:
    if h[side][mv.kind] <= 0 or b[mv.tf][mv.tr].occ: return false
    b[mv.tf][mv.tr] = Cell(occ: true, color: side, kind: mv.kind, promoted: false)
    dec h[side][mv.kind]
    return true
  if not b[mv.ff][mv.fr].occ: return false
  var piece = b[mv.ff][mv.fr]
  if piece.color != side: return false
  if b[mv.tf][mv.tr].occ and b[mv.tf][mv.tr].color == side: return false
  if b[mv.tf][mv.tr].occ:
    inc h[side][b[mv.tf][mv.tr].kind]
  b[mv.ff][mv.fr] = emptyCell()
  piece.promoted = piece.promoted or mv.promote
  b[mv.tf][mv.tr] = piece
  true

proc enteringByHands(h: Hands): string =
  let black = countHandMaterial(h, Black) >= 24
  let white = countHandMaterial(h, White) >= 24
  if black and white: return "jishogi"
  if black: return "black_entering_king"
  if white: return "white_entering_king"
  "ok"

proc judgeGame(g: JsonNode): string =
  if not g.hasKey("initial") or not g.hasKey("moves"):
    return "illegal:0"
  var board: Board
  var side: Color
  var hands: Hands
  if not parseInitial(g["initial"].getStr, board, side, hands):
    return "illegal:0"
  var seen = initTable[string, int]()
  seen[compactPosition(board, side, hands)] = 1
  var ply = 0
  for node in g["moves"].items:
    inc ply
    if node.kind != JString: return "illegal:" & $ply
    var mv: MoveText
    if not parseMove(node.getStr, mv): return "illegal:" & $ply
    if not applyBookkeeping(board, hands, side, mv): return "illegal:" & $ply
    side = opponent(side)
    let key = compactPosition(board, side, hands)
    seen[key] = seen.getOrDefault(key, 0) + 1
    if seen[key] >= 4:
      return "draw"
  enteringByHands(hands)

when isMainModule:
  if paramCount() != 2:
    quit(2)
  let games = parseFile(paramStr(1))
  var outJson = newJObject()
  for g in games.items:
    outJson[g["id"].getStr] = %judgeGame(g)
  writeFile(paramStr(2), $outJson)
