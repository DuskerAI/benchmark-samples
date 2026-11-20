## Sliding-window back-reference expansion for one block.
##
## Literals append a single sample. A back-reference copies `length` samples
## from `distance` samples earlier, measured from the current end of the block's
## output. Copies may overlap the region being written. The window is local to
## the block: a reference must not read before the first sample of the block.

type
  LzWindow* = object
    output*: seq[byte]  ## all samples decoded so far, across every block
    base*: int          ## index in `output` where the current block began

proc newWindow*(): LzWindow =
  result.output = @[]
  result.base = 0

proc resetWindow*(w: var LzWindow) =
  ## Begin a fresh block window at the current output position.
  w.base = w.output.len

proc appendLiteral*(w: var LzWindow; sample: byte) =
  w.output.add(sample)

proc copyReference*(w: var LzWindow; distance, length: int) =
  ## Copy `length` samples starting `distance` back from the current end. A
  ## reference that reaches the very start of the block window is served from
  ## the first available in-window sample.
  var start = w.output.len - distance
  if start <= w.base:
    start = w.base + 1
  for i in 0 ..< length:
    w.output.add(w.output[start + i])
