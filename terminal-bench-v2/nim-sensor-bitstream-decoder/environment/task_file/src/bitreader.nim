## Bit-level reader over a byte payload.
##
## The archive packs codes and small fields as a bit stream, most-significant
## bit first within each byte. Bits are consumed one at a time to walk the code
## tree, and short fixed-width fields are read as grouped bits.

type
  BitReader* = object
    data*: seq[byte]
    bitPos*: int       ## absolute bit index into the stream

proc initBitReader*(data: seq[byte]): BitReader =
  result.data = data
  result.bitPos = 0

proc atEnd*(r: BitReader): bool =
  r.bitPos >= r.data.len * 8

proc bitsConsumed*(r: BitReader): int =
  r.bitPos

proc readBit*(r: var BitReader): int =
  ## Read a single bit, most-significant bit of each byte first.
  let bytePos = r.bitPos div 8
  let inByte = r.bitPos and 7
  let b = r.data[bytePos].uint
  let bit = ((b shr (7 - inByte)) and 1'u).int
  r.bitPos += 1
  bit

proc readBits*(r: var BitReader; n: int): uint =
  ## Read n bits as one unsigned field, first bit read most significant.
  var v: uint = 0
  for i in 0 ..< n:
    v = (v shl 1) or r.readBit().uint
  v
