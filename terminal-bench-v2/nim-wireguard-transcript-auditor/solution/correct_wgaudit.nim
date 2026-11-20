import std/[algorithm, json, os, strutils, tables]

type
  Rec = object
    id: string
    kind: int
    payload: seq[uint8]
    forcedReject: bool
    reason: string
  Session = object
    sid: uint32
    currentKey: uint32
    previousKey: uint32
    previousOpen: bool
    usedKeys: seq[uint32]
    seen: Table[uint32, seq[uint64]]

const LimitCounter = 20'u64

proc bytes(s: string): seq[uint8] =
  for ch in s:
    result.add(uint8(ord(ch)))

proc be32(x: uint32): seq[uint8] =
  @[uint8((x shr 24) and 255), uint8((x shr 16) and 255), uint8((x shr 8) and 255), uint8(x and 255)]

proc u32(p: seq[uint8], off: int): uint32 =
  (uint32(p[off]) shl 24) or (uint32(p[off + 1]) shl 16) or (uint32(p[off + 2]) shl 8) or uint32(p[off + 3])

proc u64(p: seq[uint8], off: int): uint64 =
  var v = 0'u64
  for i in 0 ..< 8:
    v = (v shl 8) or uint64(p[off + i])
  v

proc readLen(p: seq[uint8], off: int): int =
  (int(p[off]) shl 8) or int(p[off + 1])

proc fnvTag(data: seq[uint8]): string =
  var h = 1469598103934665603'u64
  for b in data:
    h = h xor uint64(b)
    h = h * 1099511628211'u64
  result = toLowerAscii(toHex(h, 16))[8 .. 15]

proc tag(context, core: seq[uint8]): string =
  var d = bytes("wg-offline")
  d.add(0'u8)
  d.add(context)
  d.add(core)
  fnvTag(d)

proc textTag(p: seq[uint8]): string =
  for b in p:
    result.add(char(b))

proc valid(kind: int, context: seq[uint8], payload: seq[uint8]): bool =
  if payload.len < 8:
    return false
  let body = payload[0 ..< payload.len - 8]
  let given = textTag(payload[payload.len - 8 ..< payload.len])
  var core = @[uint8(kind)]
  core.add(body)
  result = given == tag(context, core)

proc kindName(k: int): string =
  case k
  of 1: "initiation"
  of 2: "cookie"
  of 3: "response"
  of 4: "transport"
  of 5: "rekey"
  of 6: "checkpoint"
  of 7: "seal"
  of 8: "anchor"
  else: "unknown"

proc addAccepted(a: JsonNode, id: string, kind: int) =
  var n = newJObject()
  n["id"] = %id
  n["kind"] = %kindName(kind)
  a.add(n)

proc addRejected(a: JsonNode, id: string, kind: int, reason: string) =
  var n = newJObject()
  n["id"] = %id
  n["kind"] = %kindName(kind)
  n["reason"] = %reason
  a.add(n)

proc loadRecords(root: string): seq[Rec] =
  var files: seq[string]
  for p in walkDirRec(root):
    if p.endsWith(".wgt"):
      files.add(p)
  files.sort()
  for f in files:
    let rel = relativePath(f, root).replace("\\", "/")
    let s = readFile(f)
    var data: seq[uint8]
    for ch in s:
      data.add(uint8(ord(ch)))
    if data.len < 4 or data[0 .. 3] != bytes("WGT1"):
      result.add(Rec(id: rel & "#0", kind: 0, forcedReject: true, reason: "format"))
      continue
    var pos = 4
    var idx = 0
    while pos < data.len:
      if pos + 3 > data.len:
        result.add(Rec(id: rel & "#" & $idx, kind: 0, forcedReject: true, reason: "format"))
        break
      let k = int(data[pos])
      let ln = readLen(data, pos + 1)
      pos += 3
      if pos + ln > data.len:
        result.add(Rec(id: rel & "#" & $idx, kind: k, forcedReject: true, reason: "format"))
        break
      result.add(Rec(id: rel & "#" & $idx, kind: k, payload: data[pos ..< pos + ln]))
      pos += ln
      inc idx

proc hasCounterConflict(s: Session, key: uint32, ctr: uint64): bool =
  if not s.seen.hasKey(key):
    return false
  for x in s.seen[key]:
    if ctr <= x:
      return true
  false

proc remember(s: var Session, key: uint32, ctr: uint64) =
  var xs = s.seen.getOrDefault(key, @[])
  xs.add(ctr)
  s.seen[key] = xs

proc usable(s: Session, key: uint32): bool =
  key == s.currentKey or (s.previousOpen and key == s.previousKey)

proc storeSession(sessions: var Table[uint32, Session], s: Session) =
  sessions[s.currentKey] = s
  if s.previousKey != 0'u32:
    sessions[s.previousKey] = s

proc activeKeyExists(sessions: Table[uint32, Session], key: uint32): bool =
  if not sessions.hasKey(key):
    return false
  let s = sessions[key]
  result = key == s.currentKey or (s.previousOpen and key == s.previousKey)

proc usedBySession(s: Session, key: uint32): bool =
  for k in s.usedKeys:
    if k == key:
      return true
  false

proc highestCounter(s: Session, key: uint32): uint64 =
  if not s.seen.hasKey(key):
    return 0
  for x in s.seen[key]:
    if x > result:
      result = x

proc activeKeyXor(sessions: Table[uint32, Session]): uint32 =
  var included: Table[uint32, bool]
  for key, s in sessions:
    if key == s.currentKey or (s.previousOpen and key == s.previousKey):
      if not included.getOrDefault(key, false):
        result = result xor key
        included[key] = true

proc run(captures, outPath: string) =
  let accepted = newJArray()
  let rejected = newJArray()
  var pending: Table[uint32, bool]
  var lastStamp: Table[uint32, uint32]
  var cookies: Table[(uint32, uint32), uint32]
  var cookieStamp: Table[(uint32, uint32), uint32]
  var lastCookieNonce: Table[(uint32, uint32), uint32]
  var sessionIds: Table[uint32, bool]
  var retiredKeys: Table[uint32, bool]
  var sessions: Table[uint32, Session]
  let records = loadRecords(captures)
  for r in records:
    if r.forcedReject:
      addRejected(rejected, r.id, r.kind, r.reason)
      continue
    let p = r.payload
    case r.kind
    of 1:
      if p.len != 16 or not valid(1, bytes("init"), p):
        addRejected(rejected, r.id, r.kind, "auth")
      else:
        let sender = u32(p, 0)
        let stamp = u32(p, 4)
        if lastStamp.hasKey(sender) and stamp <= lastStamp[sender]:
          addRejected(rejected, r.id, r.kind, "state")
        else:
          lastStamp[sender] = stamp
          pending[sender] = true
          addAccepted(accepted, r.id, r.kind)
    of 2:
      if p.len != 20 or not valid(2, bytes("cookie"), p):
        addRejected(rejected, r.id, r.kind, "auth")
      else:
        let sender = u32(p, 0)
        let receiver = u32(p, 4)
        let nonce = u32(p, 8)
        let pair = (sender, receiver)
        if not pending.getOrDefault(sender, false):
          addRejected(rejected, r.id, r.kind, "state")
        elif lastCookieNonce.hasKey(pair) and nonce <= lastCookieNonce[pair]:
          addRejected(rejected, r.id, r.kind, "state")
        else:
          cookies[pair] = nonce
          cookieStamp[pair] = lastStamp[sender]
          lastCookieNonce[pair] = nonce
          addAccepted(accepted, r.id, r.kind)
    of 3:
      if p.len != 24:
        addRejected(rejected, r.id, r.kind, "format")
      else:
        let sender = u32(p, 0)
        let receiver = u32(p, 4)
        let pair = (receiver, sender)
        if not pending.getOrDefault(receiver, false) or not cookies.hasKey(pair) or cookieStamp.getOrDefault(pair, 0'u32) != lastStamp.getOrDefault(receiver, 0'u32):
          addRejected(rejected, r.id, r.kind, "state")
        else:
          let nonce = cookies[pair]
          let sid = u32(p, 8)
          let key = u32(p, 12)
          var ctx = bytes("response")
          ctx.add(be32(nonce))
          if sessionIds.getOrDefault(sid, false) or retiredKeys.getOrDefault(key, false) or activeKeyExists(sessions, key):
            addRejected(rejected, r.id, r.kind, "state")
          elif not valid(3, ctx, p):
            addRejected(rejected, r.id, r.kind, "auth")
          else:
            var s = Session(sid: sid, currentKey: key, previousKey: 0'u32, previousOpen: false)
            s.usedKeys = @[key]
            s.seen = initTable[uint32, seq[uint64]]()
            sessions[key] = s
            sessionIds[sid] = true
            cookies.del(pair)
            cookieStamp.del(pair)
            pending[receiver] = false
            addAccepted(accepted, r.id, r.kind)
    of 4:
      if p.len < 22:
        addRejected(rejected, r.id, r.kind, "format")
      else:
        let key = u32(p, 0)
        let ctr = u64(p, 4)
        let blen = (int(p[12]) shl 8) or int(p[13])
        if p.len != 14 + blen + 8 or not sessions.hasKey(key):
          addRejected(rejected, r.id, r.kind, "state")
        else:
          var s = sessions[key]
          var ctx = bytes("transport")
          ctx.add(be32(s.sid))
          if not usable(s, key) or ctr == 0 or ctr > LimitCounter or hasCounterConflict(s, key, ctr) or not valid(4, ctx, p):
            addRejected(rejected, r.id, r.kind, "auth")
          else:
            remember(s, key, ctr)
            if key == s.currentKey and s.previousOpen:
              retiredKeys[s.previousKey] = true
              s.previousOpen = false
            storeSession(sessions, s)
            addAccepted(accepted, r.id, r.kind)
    of 5:
      if p.len != 24:
        addRejected(rejected, r.id, r.kind, "format")
      else:
        let key = u32(p, 0)
        let newKey = u32(p, 4)
        let ctr = u64(p, 8)
        if not sessions.hasKey(key):
          addRejected(rejected, r.id, r.kind, "state")
        else:
          var s = sessions[key]
          var ctx = bytes("rekey")
          ctx.add(be32(s.sid))
          if key != s.currentKey or retiredKeys.getOrDefault(newKey, false) or activeKeyExists(sessions, newKey) or usedBySession(s, newKey) or ctr == 0 or ctr > LimitCounter or hasCounterConflict(s, key, ctr) or not valid(5, ctx, p):
            addRejected(rejected, r.id, r.kind, "auth")
          else:
            remember(s, key, ctr)
            s.previousKey = s.currentKey
            s.currentKey = newKey
            s.previousOpen = true
            s.usedKeys.add(newKey)
            storeSession(sessions, s)
            addAccepted(accepted, r.id, r.kind)
    of 6:
      if p.len != 28:
        addRejected(rejected, r.id, r.kind, "format")
      else:
        let sid = u32(p, 0)
        let key = u32(p, 4)
        let observed = u64(p, 8)
        let prev = u32(p, 16)
        if not sessions.hasKey(key):
          addRejected(rejected, r.id, r.kind, "state")
        else:
          let s = sessions[key]
          var ctx = bytes("checkpoint")
          ctx.add(be32(sid))
          let expectedPrev = if s.previousOpen: s.previousKey else: 0'u32
          if sid != s.sid or key != s.currentKey or observed != highestCounter(s, key) or prev != expectedPrev or not valid(6, ctx, p):
            addRejected(rejected, r.id, r.kind, "auth")
          else:
            addAccepted(accepted, r.id, r.kind)
    of 7:
      if p.len != 28:
        addRejected(rejected, r.id, r.kind, "format")
      else:
        let sid = u32(p, 0)
        let key = u32(p, 4)
        let nextCtr = u64(p, 8)
        let prev = u32(p, 16)
        if not sessions.hasKey(key):
          addRejected(rejected, r.id, r.kind, "state")
        else:
          let s = sessions[key]
          var ctx = bytes("seal")
          ctx.add(be32(sid))
          let expectedPrev = if s.previousOpen: s.previousKey else: 0'u32
          let expectedNext = highestCounter(s, key) + 1'u64
          if sid != s.sid or key != s.currentKey or nextCtr != expectedNext or prev != expectedPrev or not valid(7, ctx, p):
            addRejected(rejected, r.id, r.kind, "auth")
          else:
            addAccepted(accepted, r.id, r.kind)
    of 8:
      if p.len != 28:
        addRejected(rejected, r.id, r.kind, "format")
      else:
        let sid = u32(p, 0)
        let key = u32(p, 4)
        let acc = u32(p, 8)
        let rej = u32(p, 12)
        let ax = u32(p, 16)
        if not sessions.hasKey(key):
          addRejected(rejected, r.id, r.kind, "state")
        else:
          let s = sessions[key]
          var ctx = bytes("anchor")
          ctx.add(be32(sid))
          if sid != s.sid or key != s.currentKey or acc != uint32(accepted.len) or rej != uint32(rejected.len) or ax != activeKeyXor(sessions) or not valid(8, ctx, p):
            addRejected(rejected, r.id, r.kind, "auth")
          else:
            addAccepted(accepted, r.id, r.kind)
    else:
      addRejected(rejected, r.id, r.kind, "format")
  var report = newJObject()
  report["accepted"] = accepted
  report["rejected"] = rejected
  var summary = newJObject()
  summary["accepted"] = %accepted.len
  summary["rejected"] = %rejected.len
  report["summary"] = summary
  writeFile(outPath, pretty(report))

when isMainModule:
  var captures = ""
  var outPath = ""
  var i = 1
  while i <= paramCount():
    case paramStr(i)
    of "--captures":
      inc i
      if i <= paramCount(): captures = paramStr(i)
    of "--out":
      inc i
      if i <= paramCount(): outPath = paramStr(i)
    else:
      discard
    inc i
  if captures.len == 0 or outPath.len == 0:
    quit(2)
  run(captures, outPath)
