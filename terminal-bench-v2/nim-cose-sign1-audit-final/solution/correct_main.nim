import std/[algorithm, json, os, osproc, streams, strutils, tables]

type
  CborKind = enum
    ckInt, ckBytes, ckText, ckArray, ckMap, ckTag, ckBool, ckNull
  CborVal = ref object
    kind: CborKind
    indefBytes: bool
    i: int64
    bytes: seq[byte]
    text: string
    items: seq[CborVal]
    pairs: seq[tuple[k: CborVal, v: CborVal]]
    tag: uint64
    tagged: CborVal
    b: bool
  ParseError = object of CatchableError

proc enc(v: CborVal): seq[byte]

proc fail(msg: string) =
  raise newException(ParseError, msg)

proc strBytes(s: string): seq[byte] =
  for c in s: result.add(byte(c))

proc hexToBytes(s: string): seq[byte] =
  if (s.len mod 2) != 0: fail("hex")
  for i in countup(0, s.len - 1, 2):
    result.add(parseHexInt(s[i..i+1]).byte)

proc asString(data: seq[byte]): string =
  result = newString(data.len)
  for i, b in data: result[i] = char(b)

proc cmpBytes(a, b: seq[byte]): int =
  let m = min(a.len, b.len)
  for i in 0 ..< m:
    if a[i] < b[i]: return -1
    if a[i] > b[i]: return 1
  cmp(a.len, b.len)

proc readLen(data: seq[byte], pos: var int, ai: int): uint64 =
  if ai < 24: return uint64(ai)
  if ai == 24:
    if pos >= data.len: fail("eof")
    result = data[pos].uint64
    inc pos
  elif ai == 25:
    if pos + 1 >= data.len: fail("eof")
    result = (data[pos].uint64 shl 8) or data[pos+1].uint64
    pos += 2
  elif ai == 26:
    if pos + 3 >= data.len: fail("eof")
    result = 0
    for _ in 0..3:
      result = (result shl 8) or data[pos].uint64
      inc pos
  elif ai == 27:
    if pos + 7 >= data.len: fail("eof")
    result = 0
    for _ in 0..7:
      result = (result shl 8) or data[pos].uint64
      inc pos
  else:
    fail("len")

proc readVal(data: seq[byte], pos: var int): CborVal =
  if pos >= data.len: fail("eof")
  let ib = data[pos]
  inc pos
  let major = int(ib shr 5)
  let ai = int(ib and 31)
  case major
  of 0:
    result = CborVal(kind: ckInt, i: int64(readLen(data, pos, ai)))
  of 1:
    result = CborVal(kind: ckInt, i: -1'i64 - int64(readLen(data, pos, ai)))
  of 2:
    result = CborVal(kind: ckBytes)
    if ai == 31:
      result.indefBytes = true
      while pos < data.len and data[pos] != 0xff'u8:
        let chunk = readVal(data, pos)
        if chunk.kind != ckBytes: fail("chunk")
        result.bytes &= chunk.bytes
      if pos >= data.len: fail("eof")
      inc pos
    else:
      let n = int(readLen(data, pos, ai))
      if pos + n > data.len: fail("eof")
      result.bytes = data[pos ..< pos+n]
      pos += n
  of 3:
    result = CborVal(kind: ckText)
    if ai == 31:
      while pos < data.len and data[pos] != 0xff'u8:
        let chunk = readVal(data, pos)
        if chunk.kind != ckText: fail("chunk")
        result.text.add(chunk.text)
      if pos >= data.len: fail("eof")
      inc pos
    else:
      let n = int(readLen(data, pos, ai))
      if pos + n > data.len: fail("eof")
      result.text = asString(data[pos ..< pos+n])
      pos += n
  of 4:
    result = CborVal(kind: ckArray)
    if ai == 31:
      while pos < data.len and data[pos] != 0xff'u8:
        result.items.add(readVal(data, pos))
      if pos >= data.len: fail("eof")
      inc pos
    else:
      for _ in 0 ..< int(readLen(data, pos, ai)):
        result.items.add(readVal(data, pos))
  of 5:
    result = CborVal(kind: ckMap)
    var n = 0
    var indefinite = false
    if ai == 31: indefinite = true else: n = int(readLen(data, pos, ai))
    var prev: seq[byte] = @[]
    var havePrev = false
    var readCount = 0
    while true:
      if indefinite:
        if pos < data.len and data[pos] == 0xff'u8:
          inc pos
          break
        if pos >= data.len: fail("eof")
      else:
        if readCount >= n: break
      let k = readVal(data, pos)
      let kb = enc(k)
      if havePrev and cmpBytes(kb, prev) <= 0: fail("map")
      prev = kb
      havePrev = true
      let v = readVal(data, pos)
      result.pairs.add((k, v))
      inc readCount
  of 6:
    let t = readLen(data, pos, ai)
    result = CborVal(kind: ckTag, tag: t, tagged: readVal(data, pos))
  of 7:
    if ai == 20: result = CborVal(kind: ckBool, b: false)
    elif ai == 21: result = CborVal(kind: ckBool, b: true)
    elif ai == 22: result = CborVal(kind: ckNull)
    else: fail("simple")
  else:
    fail("major")

proc parseCbor(data: seq[byte]): CborVal =
  var p = 0
  result = readVal(data, p)
  if p != data.len: fail("trailing")

proc encLen(major: int, n: uint64): seq[byte] =
  if n < 24: return @[byte((major shl 5) or int(n))]
  if n <= 0xff: return @[byte((major shl 5) or 24), byte(n)]
  if n <= 0xffff: return @[byte((major shl 5) or 25), byte(n shr 8), byte(n)]
  if n <= 0xffffffff'u64:
    result = @[byte((major shl 5) or 26)]
    for s in [24, 16, 8, 0]: result.add(byte(n shr s))
  else:
    result = @[byte((major shl 5) or 27)]
    for s in [56, 48, 40, 32, 24, 16, 8, 0]: result.add(byte(n shr s))

proc enc(v: CborVal): seq[byte] =
  case v.kind
  of ckInt:
    if v.i >= 0: result = encLen(0, uint64(v.i))
    else: result = encLen(1, uint64(-1'i64 - v.i))
  of ckBytes:
    result = encLen(2, uint64(v.bytes.len)) & v.bytes
  of ckText:
    let bs = strBytes(v.text)
    result = encLen(3, uint64(bs.len)) & bs
  of ckArray:
    result = encLen(4, uint64(v.items.len))
    for it in v.items: result &= enc(it)
  of ckMap:
    var ep: seq[tuple[kb: seq[byte], vb: seq[byte]]] = @[]
    for p in v.pairs: ep.add((enc(p.k), enc(p.v)))
    ep.sort(proc(a, b: tuple[kb: seq[byte], vb: seq[byte]]): int = cmpBytes(a.kb, b.kb))
    result = encLen(5, uint64(ep.len))
    for p in ep: result &= p.kb & p.vb
  of ckTag:
    result = encLen(6, v.tag) & enc(v.tagged)
  of ckBool:
    result = @[if v.b: 0xf5'u8 else: 0xf4'u8]
  of ckNull:
    result = @[0xf6'u8]

proc mapGet(m: CborVal, key: CborVal): CborVal =
  if m.kind != ckMap: return nil
  let kb = enc(key)
  for p in m.pairs:
    if enc(p.k) == kb: return p.v
  nil

proc intKey(n: int64): CborVal = CborVal(kind: ckInt, i: n)
proc textKey(s: string): CborVal = CborVal(kind: ckText, text: s)

proc hmacSha256(hexKey: string, msg: seq[byte]): seq[byte] =
  let p = startProcess("openssl", args = ["dgst", "-sha256", "-mac", "HMAC", "-macopt", "hexkey:" & hexKey], options = {poUsePath})
  p.inputStream.write(asString(msg))
  p.inputStream.close()
  let outp = p.outputStream.readAll()
  let code = p.waitForExit()
  p.close()
  if code != 0: fail("hmac")
  result = hexToBytes(outp.strip.splitWhitespace()[^1])

proc sigStructure(protectedBytes, payloadBytes: seq[byte]): seq[byte] =
  enc(CborVal(kind: ckArray, items: @[
    CborVal(kind: ckText, text: "Signature1"),
    CborVal(kind: ckBytes, bytes: protectedBytes),
    CborVal(kind: ckBytes, bytes: @[]),
    CborVal(kind: ckBytes, bytes: payloadBytes)
  ]))

proc jesc(s: string): string =
  result = "\""
  for c in s:
    case c
    of '\\': result.add("\\\\")
    of '"': result.add("\\\"")
    of '\n': result.add("\\n")
    else: result.add(c)
  result.add('"')

proc getClaimText(m: CborVal, k: string): string =
  let v = mapGet(m, textKey(k))
  if v != nil and v.kind == ckText: v.text else: ""

proc getClaimInt(m: CborVal, k: string, ok: var bool, fallback: int64 = 0): int64 =
  let v = mapGet(m, textKey(k))
  if v == nil: return fallback
  if v.kind == ckInt: return v.i
  ok = false
  fallback

proc scopesOf(m: CborVal, ok: var bool): seq[string] =
  let v = mapGet(m, textKey("scope"))
  if v == nil or v.kind != ckArray or v.items.len == 0:
    ok = false
    return
  for it in v.items:
    if it.kind != ckText or it.text.len == 0:
      ok = false
    elif it.text notin result:
      result.add(it.text)
  result.sort()

type Rec = object
  id, status, issuer, subject, audience: string
  expires: int64
  scopes: seq[string]

proc knownCrit(label: CborVal): bool =
  if label.kind == ckInt: return label.i in [1'i64, 2'i64, 4'i64]
  false

proc supportedHeaderKey(label: CborVal): bool =
  if label.kind == ckInt: return label.i in [1'i64, 2'i64, 4'i64]
  false

proc auditOne(id: string, tokBytes: seq[byte], keys: JsonNode, now: int64): Rec =
  result = Rec(id: id, status: "malformed", expires: 0, scopes: @[])
  try:
    var tok = parseCbor(tokBytes)
    if tok.kind == ckTag:
      if tok.tag != 18: return
      tok = tok.tagged
    if tok.kind != ckArray or tok.items.len != 4: return
    if tok.items[0].kind != ckBytes or tok.items[1].kind != ckMap or tok.items[2].kind != ckBytes or tok.items[3].kind != ckBytes: return
    if tok.items[0].indefBytes or tok.items[3].indefBytes: return
    let protectedBytes = tok.items[0].bytes
    let payloadBytes = tok.items[2].bytes
    let sigBytes = tok.items[3].bytes
    let protectedMap = parseCbor(protectedBytes)
    if protectedMap.kind != ckMap or enc(protectedMap) != protectedBytes: return
    let claims = parseCbor(payloadBytes)
    if claims.kind != ckMap or enc(claims) != payloadBytes: return
    if tok.items[1].pairs.len > 0:
      result.status = "unsupported"
      return
    for p in protectedMap.pairs:
      if not supportedHeaderKey(p.k):
        result.status = "unsupported"
        return
    let crit = mapGet(protectedMap, intKey(2))
    if crit != nil:
      if crit.kind != ckArray:
        result.status = "unsupported"
        return
      var seenCrit: seq[int64] = @[]
      for label in crit.items:
        if not knownCrit(label) or mapGet(protectedMap, label) == nil or label.i in seenCrit:
          result.status = "unsupported"
          return
        seenCrit.add(label.i)
    let alg = mapGet(protectedMap, intKey(1))
    let kid = mapGet(protectedMap, intKey(4))
    if alg == nil or alg.kind != ckInt or alg.i != -16 or kid == nil or kid.kind != ckBytes:
      result.status = "unsupported"
      return
    let kidText = asString(kid.bytes)
    if not keys.hasKey(kidText):
      result.status = "unsupported"
      return
    let calc = hmacSha256(keys[kidText].getStr(), sigStructure(protectedBytes, payloadBytes))
    if calc != sigBytes:
      result.status = "bad-signature"
      return
    result.issuer = getClaimText(claims, "iss")
    result.subject = getClaimText(claims, "sub")
    result.audience = getClaimText(claims, "aud")
    var ok = true
    let exp = getClaimInt(claims, "exp", ok)
    let hasIat = mapGet(claims, textKey("iat")) != nil
    let iat = getClaimInt(claims, "iat", ok)
    let nbf = getClaimInt(claims, "nbf", ok, now)
    result.expires = exp
    result.scopes = scopesOf(claims, ok)
    if nbf > now:
      result.status = "not-yet-valid"
    elif exp <= now:
      result.status = "expired"
    elif result.issuer.len == 0 or result.subject.len == 0 or result.audience != "terminal-bench" or not hasIat or not ok or iat > now + 60:
      result.status = "bad-claims"
    else:
      result.status = "valid"
  except CatchableError:
    result.status = "malformed"

proc usage() =
  quit("usage: coseaudit --tokens FILE --keys FILE --out FILE --time UNIX", 2)

var tokensPath, keysPath, outPath = ""
var now: int64 = 0
var i = 1
while i <= paramCount():
  let a = paramStr(i)
  if a == "--tokens" and i < paramCount(): inc i; tokensPath = paramStr(i)
  elif a == "--keys" and i < paramCount(): inc i; keysPath = paramStr(i)
  elif a == "--out" and i < paramCount(): inc i; outPath = paramStr(i)
  elif a == "--time" and i < paramCount(): inc i; now = parseInt(paramStr(i))
  else: usage()
  inc i
if tokensPath.len == 0 or keysPath.len == 0 or outPath.len == 0 or now == 0: usage()

let keys = parseJson(readFile(keysPath))
var recs: seq[Rec] = @[]
for line in lines(tokensPath):
  try:
    let j = parseJson(line)
    if j.kind != JObject or not j.hasKey("id") or j["id"].kind != JString or not j.hasKey("cose") or j["cose"].kind != JString:
      var rid = ""
      if j.kind == JObject and j.hasKey("id") and j["id"].kind == JString:
        rid = j["id"].getStr()
      recs.add(Rec(id: rid, status: "malformed", issuer: "", subject: "", audience: "", expires: 0, scopes: @[]))
      continue
    recs.add(auditOne(j["id"].getStr(), hexToBytes(j["cose"].getStr()), keys, now))
  except CatchableError:
    var rid = ""
    try:
      let j = parseJson(line)
      if j.kind == JObject and j.hasKey("id") and j["id"].kind == JString:
        rid = j["id"].getStr()
    except CatchableError:
      discard
    recs.add(Rec(id: rid, status: "malformed", issuer: "", subject: "", audience: "", expires: 0, scopes: @[]))
recs.sort(proc(a, b: Rec): int = cmp(a.id, b.id))

var counts = initTable[string, int]()
for r in recs: counts[r.status] = counts.getOrDefault(r.status) + 1

var report = "{\n  \"generated_at\": " & $now & ",\n  \"tokens\": [\n"
for idx, r in recs:
  report.add("    {\"id\": " & jesc(r.id) & ", \"status\": " & jesc(r.status) & ", \"issuer\": " & jesc(r.issuer) & ", \"subject\": " & jesc(r.subject) & ", \"audience\": " & jesc(r.audience) & ", \"expires\": " & $r.expires & ", \"scopes\": [")
  for si, sc in r.scopes:
    if si > 0: report.add(", ")
    report.add(jesc(sc))
  report.add("]}")
  if idx + 1 < recs.len: report.add(",")
  report.add("\n")
report.add("  ],\n  \"summary\": {\"total\": " & $recs.len & ", \"valid\": " & $counts.getOrDefault("valid") & ", \"invalid\": " & $(recs.len - counts.getOrDefault("valid")) & ", \"by_status\": {")
let statuses = @["bad-claims", "bad-signature", "expired", "malformed", "not-yet-valid", "unsupported", "valid"]
for si, st in statuses:
  if si > 0: report.add(", ")
  report.add(jesc(st) & ": " & $counts.getOrDefault(st))
report.add("}}\n}\n")
writeFile(outPath, report)
