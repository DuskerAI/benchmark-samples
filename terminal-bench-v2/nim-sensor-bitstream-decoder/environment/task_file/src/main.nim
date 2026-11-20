## Command-line entry point for the sensor-archive decoder.
##
## Usage: sardecode <archive-path> <output-path>
##
## Reads the binary archive, reconstructs the sample stream, and writes one
## decimal sample value per line, grouped by channel. Each channel is preceded
## by a line "channel <index>" starting at index 0.

import os
import strutils
import archive

proc readFileBytes(path: string): seq[byte] =
  let content = readFile(path)
  result = newSeq[byte](content.len)
  for i in 0 ..< content.len:
    result[i] = content[i].byte

proc main() =
  if paramCount() != 2:
    stderr.writeLine("usage: sardecode <archive> <output>")
    quit(2)
  let archivePath = paramStr(1)
  let outPath = paramStr(2)
  let data = readFileBytes(archivePath)
  let arc = decodeArchive(data)

  var lines: seq[string] = @[]
  var idx = 0
  for c in 0 ..< arc.channelCounts.len:
    lines.add("channel " & $c)
    let want = arc.channelCounts[c]
    var got = 0
    while got < want and idx < arc.samples.len:
      lines.add($arc.samples[idx].int)
      idx += 1
      got += 1
  writeFile(outPath, lines.join("\n") & "\n")

main()
