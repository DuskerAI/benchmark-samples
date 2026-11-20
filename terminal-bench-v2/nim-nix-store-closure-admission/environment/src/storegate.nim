import std/[algorithm, json, os, sequtils, strutils, tables]

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

proc strings(n: JsonNode, key: string): seq[string] =
  if not n.hasKey(key): return @[]
  for item in n[key].items: result.add(item.getStr())

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
    result[d.id] = d

proc readSubstitutes(dir: string): seq[Substitute] =
  let file = dir / "substitutes.ndjson"
  if not fileExists(file): return @[]
  for line in lines(file):
    if line.strip().len == 0: continue
    let j = parseJson(line)
    result.add(Substitute(path: j["path"].getStr(), hash: j["hash"].getStr(), source: j["source"].getStr(), priority: j["priority"].getInt(), platforms: strings(j, "platforms"), features: strings(j, "features")))

proc includes(xs: seq[string], x: string): bool = xs.anyIt(it == x)

proc selectorOf(id, outputName: string): string = id & ":" & outputName

proc main() =
  let args = commandLineParams()
  let caseDir = argValue(args, "--case")
  let platform = argValue(args, "--platform")
  let features = splitCsv(argValue(args, "--features"))
  let roots = splitCsv(argValue(args, "--roots"))
  let outDir = argValue(args, "--out")

  var derivs = readDerivations(caseDir)
  let subs = readSubstitutes(caseDir)
  var pathOwner: Table[string, string]
  var selectorOwner: Table[string, string]
  for id, d in derivs:
    for o in d.outputs:
      pathOwner[o.path] = id
      selectorOwner[selectorOf(id, o.name)] = id
  createDir(outDir)
  var audit: seq[JsonNode]
  var seen: Table[string, bool]
  var order: seq[string]

  proc visit(id: string) =
    if seen.hasKey(id): return
    if not derivs.hasKey(id):
      audit.add(%* {"event":"deny", "id":id, "reason":"unknown"})
      return
    seen[id] = true
    let d = derivs[id]
    if d.platforms.len > 0 and not d.platforms.includes(platform):
      audit.add(%* {"event":"deny", "id":id, "reason":"platform"})
    if d.features.len > 0:
      var ok = false
      for f in d.features:
        if features.includes(f): ok = true
      if not ok: audit.add(%* {"event":"deny", "id":id, "reason":"feature"})
    for dep in d.inputs:
      if derivs.hasKey(dep):
        visit(dep)
      elif pathOwner.hasKey(dep):
        visit(pathOwner[dep])
      elif selectorOwner.hasKey(dep):
        visit(selectorOwner[dep])
      else:
        visit(dep)
    order.add(id)

  for r in roots:
    audit.add(%* {"event":"root", "id":r, "path":""})
    visit(r)

  var steps: seq[Step]
  for id in order:
    let d = derivs[id]
    for o in d.outputs:
      var best = Substitute(priority: high(int), source: "local")
      var action = "build"
      for s in subs:
        if s.path == o.path:
          if s.priority < best.priority:
            best = s
            action = "substitute"
      let src = if action == "substitute": best.source else: "local"
      steps.add(Step(path: o.path, derivation: id, output: o.name, hash: o.hash, action: action, source: src))
      audit.add(%* {"event":"admit", "id":id, "path":o.path})
      if action == "substitute": audit.add(%* {"event":"substitute", "id":id, "path":o.path})

  var arr = newJArray()
  for s in steps:
    arr.add(%* {"path":s.path, "derivation":s.derivation, "output":s.output, "hash":s.hash, "method":s.action, "source":s.source})
  writeFile(outDir / "plan.json", arr.pretty())
  var linesOut: seq[string]
  for e in audit: linesOut.add($e)
  writeFile(outDir / "audit.ndjson", linesOut.join("\n") & (if linesOut.len > 0: "\n" else: ""))

when isMainModule:
  main()
