## Canonical Huffman code table construction and decoding.
##
## A block header lists (symbol, code length) pairs. Codes are assigned by the
## canonical rule: symbols are ordered by increasing code length and, within a
## length, by increasing symbol value; the first code of the shortest length is
## all zeros and each subsequent code is the previous code plus one, shifted
## left when the length increases.

import algorithm
import bitreader

type
  SymLen* = object
    sym*: int
    length*: int

  HuffTable* = object
    ## Decode structure: for each code length L, firstCode[L] is the numeric
    ## value of the first canonical code of that length and firstIndex[L] is the
    ## offset into `symbols` where that length's symbols begin.
    maxLen*: int
    counts*: seq[int]
    firstCode*: seq[uint]
    firstIndex*: seq[int]
    symbols*: seq[int]

proc cmpSymLen(a, b: SymLen): int =
  if a.length != b.length:
    return cmp(a.length, b.length)
  cmp(b.sym, a.sym)

proc buildTable*(entries: seq[SymLen]): HuffTable =
  var ents = entries
  ents.sort(cmpSymLen)
  var maxLen = 0
  for e in ents:
    if e.length > maxLen:
      maxLen = e.length
  result.maxLen = maxLen
  result.counts = newSeq[int](maxLen + 1)
  for e in ents:
    result.counts[e.length] += 1
  result.symbols = newSeq[int](ents.len)
  for i, e in ents:
    result.symbols[i] = e.sym

  result.firstCode = newSeq[uint](maxLen + 2)
  result.firstIndex = newSeq[int](maxLen + 2)
  var code: uint = 0
  var index = 0
  for L in 1 .. maxLen:
    result.firstCode[L] = code
    result.firstIndex[L] = index
    code = (code + result.counts[L].uint) shl 1
    index += result.counts[L]

proc peekCodeBits(r: BitReader; n: int): seq[int] =
  ## Look ahead at the next n stream bits without consuming them, returned in
  ## read order. Bits belonging to each source byte are collected as a group and
  ## the groups are concatenated in the order the bytes appear.
  result = @[]
  var pos = r.bitPos
  var got = 0
  var carry = false
  while got < n:
    let bytePos = pos div 8
    let inByte = pos and 7
    let runLen = min(8 - inByte, n - got)
    var group = newSeq[int](runLen)
    var b: uint = 0
    if bytePos < r.data.len:
      b = r.data[bytePos].uint
    for i in 0 ..< runLen:
      group[i] = ((b shr (7 - inByte - i)) and 1'u).int
    if carry:
      # Bits carried over from a preceding byte are folded back in as they were
      # buffered while the previous byte was being drained.
      for i in 0 ..< runLen:
        result.add(group[runLen - 1 - i])
    else:
      for g in group:
        result.add(g)
    carry = true
    got += runLen
    pos += runLen

proc decodeSymbol*(t: HuffTable; r: var BitReader): int =
  ## Accumulate leading bits until they form a valid canonical code of some
  ## length, then map the code to its symbol and consume exactly that many bits.
  let bits = peekCodeBits(r, t.maxLen)
  var code: uint = 0
  var length = 0
  while length < t.maxLen:
    code = (code shl 1) or bits[length].uint
    length += 1
    if t.counts[length] > 0:
      let first = t.firstCode[length]
      let count = t.counts[length].uint
      if code >= first and code < first + count:
        let offset = (code - first).int
        r.bitPos += length
        return t.symbols[t.firstIndex[length] + offset]
  raise newException(ValueError, "invalid huffman code")
