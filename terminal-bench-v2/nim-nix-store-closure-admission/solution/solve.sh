#!/bin/bash
set -euo pipefail

cat > /app/storegate/src/storegate.nim <<'NIM'
import std/[algorithm, json, os, sets, sequtils, strutils, tables]

type
  Output = object
    name, path, hash: string
    fixed: bool
  Derivation = object
    id: string
    platforms, features, inputs: seq[string]
    outputs: seq[Output]
  Substitute = object
    path, hash, source: string
    priority: int
    platforms, features: seq[string]
  Step = object
    path, derivation, output, hash, action, source: string

proc die(msg: string): void =
  stderr.writeLine(msg)
  quit(2)

proc argValue(args: seq[string], name: string): string =
  for i in 0 ..< args.len - 1:
    if args[i] == name: return args[i + 1]
  die("missing " & name)

proc splitCsv(s: string): seq[string] =
  for p in s.split(','):
    let v = p.strip()
    if v.len > 0: result.add(v)
  result.sort(system.cmp[string])

proc strings(n: JsonNode, key: string): seq[string] =
  if not n.hasKey(key): return @[]
  for item in n[key].items: result.add(item.getStr())
  result.sort(system.cmp[string])

proc readDerivations(dir: string): Table[string, Derivation] =
  for line in lines(dir / "derivations.ndjson"):
    if line.strip().len == 0: continue
    let j = parseJson(line)
    var d: Derivation
    d.id = j["id"].getStr()
    d.platforms = strings(j, "platforms")
    d.features = strings(j, "features")
    d.inputs = strings(j, "inputs")
    for outj in j["outputs"].items:
      d.outputs.add(Output(name: outj["name"].getStr(), path: outj["path"].getStr(), hash: outj["hash"].getStr(), fixed: outj["fixed"].getBool()))
    d.outputs.sort(proc(a, b: Output): int = cmp(a.path, b.path))
    result[d.id] = d

proc readSubstitutes(dir: string): seq[Substitute] =
  let file = dir / "substitutes.ndjson"
  if not fileExists(file): return @[]
  for line in lines(file):
    if line.strip().len == 0: continue
    let j = parseJson(line)
    result.add(Substitute(path: j["path"].getStr(), hash: j["hash"].getStr(), source: j["source"].getStr(), priority: j["priority"].getInt(), platforms: strings(j, "platforms"), features: strings(j, "features")))

proc hasValue(xs: seq[string], x: string): bool = xs.anyIt(it == x)

proc cleanOut(dir: string) =
  if dirExists(dir): removeDir(dir)
  createDir(dir)

proc compatible(s: Substitute, platform: string, features: seq[string]): bool =
  if s.platforms.len > 0 and not s.platforms.hasValue(platform): return false
  for f in s.features:
    if not features.hasValue(f): return false
  true

proc bestSubstitute(subs: seq[Substitute], o: Output, platform: string, features: seq[string]): tuple[found: bool, source: string] =
  var found = false
  var best: Substitute
  for s in subs:
    if s.compatible(platform, features) and s.hash == o.hash and (s.path == o.path or o.fixed):
      if not found or s.priority > best.priority or (s.priority == best.priority and (s.source < best.source or (s.source == best.source and s.path < best.path))):
        best = s
        found = true
  if found: return (true, best.source)
  (false, "local")

proc selectorOf(id, outputName: string): string = id & ":" & outputName

proc orderedSteps(steps: seq[Step], derivs: Table[string, Derivation], canonicalOwner: Table[string, string], pathOwner: Table[string, string], outputByPath: Table[string, Output], selectorOwner: Table[string, string], outputBySelector: Table[string, Output]): seq[Step] =
  var groups: Table[string, seq[Step]]
  var present: Table[string, bool]
  for s in steps:
    groups.mgetOrPut(s.derivation, @[]).add(s)
    present[s.derivation] = true
  for d, ss in groups.mpairs:
    ss.sort(proc(a, b: Step): int = cmp(a.path, b.path))
  var indeg: Table[string, int]
  var edges: Table[string, HashSet[string]]
  for d in groups.keys:
    indeg[d] = indeg.getOrDefault(d, 0)
    var depset = initHashSet[string]()
    for dep in derivs[d].inputs:
      if present.hasKey(dep):
        depset.incl(dep)
      elif derivs.hasKey(dep):
        for o in derivs[dep].outputs:
          if o.fixed and canonicalOwner.hasKey(o.hash) and present.hasKey(canonicalOwner[o.hash]):
            depset.incl(canonicalOwner[o.hash])
      elif pathOwner.hasKey(dep):
        let owner = pathOwner[dep]
        let o = outputByPath[dep]
        if o.fixed and canonicalOwner.hasKey(o.hash) and present.hasKey(canonicalOwner[o.hash]):
          depset.incl(canonicalOwner[o.hash])
        elif present.hasKey(owner):
          depset.incl(owner)
      elif selectorOwner.hasKey(dep):
        let owner = selectorOwner[dep]
        let o = outputBySelector[dep]
        if o.fixed and canonicalOwner.hasKey(o.hash) and present.hasKey(canonicalOwner[o.hash]):
          depset.incl(canonicalOwner[o.hash])
        elif present.hasKey(owner):
          depset.incl(owner)
    for dep in depset.items:
      if dep != d:
        edges.mgetOrPut(dep, initHashSet[string]()).incl(d)
        indeg[d] = indeg.getOrDefault(d, 0) + 1
  proc groupPath(d: string): string = groups[d][0].path
  var ready: seq[string]
  for d in groups.keys:
    if indeg.getOrDefault(d, 0) == 0: ready.add(d)
  while ready.len > 0:
    ready.sort(proc(a, b: string): int =
      result = cmp(groupPath(a), groupPath(b))
      if result == 0: result = cmp(a, b))
    let d = ready[0]
    ready.delete(0)
    for s in groups[d]: result.add(s)
    if edges.hasKey(d):
      var nexts: seq[string]
      for n in edges[d].items: nexts.add(n)
      nexts.sort(system.cmp[string])
      for n in nexts:
        indeg[n] = indeg[n] - 1
        if indeg[n] == 0: ready.add(n)

proc main() =
  let args = commandLineParams()
  let caseDir = argValue(args, "--case")
  let platform = argValue(args, "--platform")
  let features = splitCsv(argValue(args, "--features"))
  let roots = splitCsv(argValue(args, "--roots"))
  let outDir = argValue(args, "--out")
  cleanOut(outDir)

  let derivs = readDerivations(caseDir)
  let subs = readSubstitutes(caseDir)
  var pathOwner: Table[string, string]
  var outputByPath: Table[string, Output]
  var selectorOwner: Table[string, string]
  var outputBySelector: Table[string, Output]
  for id, d in derivs:
    for o in d.outputs:
      pathOwner[o.path] = id
      outputByPath[o.path] = o
      selectorOwner[selectorOf(id, o.name)] = id
      outputBySelector[selectorOf(id, o.name)] = o
  var audit: seq[JsonNode]
  var state: Table[string, int]
  var admitted: Table[string, bool]
  var ids: seq[string]
  var denied = false

  proc deny(id, reason: string) =
    denied = true
    audit.add(%* {"event":"deny", "id":id, "reason":reason})

  proc visit(id: string) =
    if state.getOrDefault(id, 0) == 2: return
    if state.getOrDefault(id, 0) == 1:
      deny(id, "cycle")
      return
    if not derivs.hasKey(id):
      deny(id, "unknown")
      return
    state[id] = 1
    let d = derivs[id]
    if d.platforms.len > 0 and not d.platforms.hasValue(platform): deny(id, "platform")
    for f in d.features:
      if not features.hasValue(f): deny(id, "feature")
    for dep in d.inputs:
      if derivs.hasKey(dep):
        visit(dep)
      elif pathOwner.hasKey(dep):
        visit(pathOwner[dep])
      elif selectorOwner.hasKey(dep):
        visit(selectorOwner[dep])
      else:
        deny(dep, "unknown")
    state[id] = 2
    if not admitted.hasKey(id):
      admitted[id] = true
      ids.add(id)

  for r in roots:
    audit.add(%* {"event":"root", "id":r, "path":""})
    visit(r)

  if denied:
    writeFile(outDir / "plan.json", "[]\n")
    audit.sort(proc(a, b: JsonNode): int = cmp($a, $b))
    var linesOut: seq[string]
    for e in audit: linesOut.add($e)
    writeFile(outDir / "audit.ndjson", linesOut.join("\n") & (if linesOut.len > 0: "\n" else: ""))
    quit(1)

  var canonicalFixed: Table[string, Output]
  var canonicalOwner: Table[string, string]
  for id in ids:
    for o in derivs[id].outputs:
      if o.fixed:
        if not canonicalFixed.hasKey(o.hash) or o.path < canonicalFixed[o.hash].path:
          canonicalFixed[o.hash] = o
          canonicalOwner[o.hash] = id

  var steps: seq[Step]
  var usedPaths: HashSet[string]
  for id in ids:
    for original in derivs[id].outputs:
      var o = original
      var owner = id
      if original.fixed and canonicalFixed.hasKey(original.hash):
        o = canonicalFixed[original.hash]
        owner = canonicalOwner[original.hash]
      if owner != id or usedPaths.contains(o.path): continue
      usedPaths.incl(o.path)
      let chosen = bestSubstitute(subs, o, platform, features)
      let action = if chosen.found: "substitute" else: "build"
      steps.add(Step(path: o.path, derivation: id, output: o.name, hash: o.hash, action: action, source: chosen.source))

  steps = orderedSteps(steps, derivs, canonicalOwner, pathOwner, outputByPath, selectorOwner, outputBySelector)
  var arr = newJArray()
  for s in steps:
    arr.add(%* {"path":s.path, "derivation":s.derivation, "output":s.output, "hash":s.hash, "method":s.action, "source":s.source})
    audit.add(%* {"event":"admit", "id":s.derivation, "path":s.path})
    if s.action == "substitute": audit.add(%* {"event":"substitute", "id":s.derivation, "path":s.path})
  writeFile(outDir / "plan.json", $arr & "\n")
  audit.sort(proc(a, b: JsonNode): int = cmp($a, $b))
  var linesOut: seq[string]
  for e in audit: linesOut.add($e)
  writeFile(outDir / "audit.ndjson", linesOut.join("\n") & (if linesOut.len > 0: "\n" else: ""))

when isMainModule:
  main()
NIM

/app/storegate/scripts/build.sh
