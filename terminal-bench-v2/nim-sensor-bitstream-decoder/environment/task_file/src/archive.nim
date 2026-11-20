## Sensor-archive container parsing: header, per-block Huffman tables, and the
## back-reference token stream, producing the reconstructed sample stream.

import bitreader
import huffman
import lz

const
  Magic = [byte('S'), byte('A'), byte('R'), byte('C')]
  SymEob = 256
  SymBackref = 257

type
  Archive* = object
    channelCounts*: seq[int]   ## declared sample count per channel
    samples*: seq[byte]        ## reconstructed samples, channel 0 first

proc be16(data: openArray[byte]; pos: int): int =
  (data[pos].int shl 8) or data[pos + 1].int

proc be32(data: openArray[byte]; pos: int): int =
  (data[pos].int shl 24) or (data[pos + 1].int shl 16) or
    (data[pos + 2].int shl 8) or data[pos + 3].int

proc parseHeader(data: seq[byte]; pos: var int): seq[int] =
  if data.len < 6:
    raise newException(ValueError, "archive too short")
  for i in 0 ..< 4:
    if data[i] != Magic[i]:
      raise newException(ValueError, "bad magic")
  let version = data[4].int
  if version != 1:
    raise newException(ValueError, "unsupported version")
  let channels = data[5].int
  pos = 6
  result = newSeq[int](channels)
  for c in 0 ..< channels:
    result[c] = be32(data, pos)
    pos += 4

proc decodeBlock(data: seq[byte]; pos: var int; w: var LzWindow;
                 remaining: int): bool =
  ## Decode one block starting at byte offset `pos`. Returns false when the
  ## block header cannot be fully read (truncated final block). `remaining` is
  ## how many more samples the whole archive still expects.
  if pos + 2 > data.len:
    return false
  let symCount = be16(data, pos)
  pos += 2
  var entries: seq[SymLen] = @[]
  for i in 0 ..< symCount:
    if pos + 3 > data.len:
      return false
    let sym = be16(data, pos)
    let length = data[pos + 2].int
    pos += 3
    entries.add(SymLen(sym: sym, length: length))
  let table = buildTable(entries)

  # Payload begins on this byte boundary. Slice the rest of the archive so the
  # bit reader can walk it; the block ends at its EOB symbol.
  var payload = data[pos ..< data.len]
  var r = initBitReader(payload)
  w.resetWindow()
  var produced = 0
  while true:
    if r.atEnd():
      # Ran out of payload before EOB: truncated final block, stop cleanly.
      pos = data.len
      return true
    let sym = decodeSymbol(table, r)
    if sym == SymEob:
      break
    elif sym == SymBackref:
      let distance = readBits(r, 16).int
      let length = readBits(r, 8).int
      copyReference(w, distance, length)
      produced += length
    else:
      appendLiteral(w, sym.byte)
      produced += 1
    if produced >= remaining and remaining >= 0:
      # Enough samples for the archive; consume the trailing EOB if present.
      discard
  # Advance pos past the payload bits consumed, rounding up to a whole byte.
  let bytesUsed = (bitsConsumed(r) + 7) div 8
  pos += bytesUsed
  true

proc decodeArchive*(data: seq[byte]): Archive =
  var pos = 0
  result.channelCounts = parseHeader(data, pos)
  var total = 0
  for c in result.channelCounts:
    total += c
  var w = newWindow()
  while w.output.len < total:
    let remaining = total - w.output.len
    if not decodeBlock(data, pos, w, remaining):
      break
    if pos >= data.len:
      break
  result.samples = w.output
