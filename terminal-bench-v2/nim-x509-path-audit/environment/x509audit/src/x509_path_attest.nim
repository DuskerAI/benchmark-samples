import std/[json, os, osproc, strutils, sequtils, algorithm]

proc quoteArg(s: string): string =
  result = "'"
  for ch in s:
    if ch == '\'':
      result.add("'\\''")
    else:
      result.add(ch)
  result.add("'")

proc run(cmd: string): tuple[code: int, outp: string] =
  let r = execCmdEx(cmd)
  (r.exitCode, r.output)

proc pemFiles(dir: string): seq[string] =
  if dirExists(dir):
    for kind, path in walkDir(dir):
      if kind == pcFile and path.toLowerAscii.endsWith(".pem"):
        result.add(path)
  result.sort()

proc bundle(files: seq[string], name: string): string =
  let p = getTempDir() / name
  var data = ""
  for f in files:
    data.add(readFile(f))
    if data.len == 0 or data[^1] != '\n': data.add('\n')
  writeFile(p, data)
  p

proc fingerprint(cert: string): string =
  let r = run("openssl x509 -in " & quoteArg(cert) & " -outform DER | openssl dgst -sha256 -r")
  if r.code != 0: return ""
  r.outp.splitWhitespace()[0].toLowerAscii()

proc writeReport(path, fp, verdict, reason: string) =
  writeFile(path, "{\"leaf_sha256\":\"" & fp & "\",\"reason\":\"" & reason & "\",\"verdict\":\"" & verdict & "\"}\n")

when isMainModule:
  if paramCount() != 2: quit(2)
  let evidence = paramStr(1)
  let outPath = paramStr(2)
  let req = parseFile(evidence / "request.json")
  let leaf = evidence / "leaf.pem"
  let roots = pemFiles(evidence / "roots")
  let inters = pemFiles(evidence / "intermediates")
  let fp = fingerprint(leaf)
  if roots.len == 0:
    createDir(parentDir(outPath))
    writeReport(outPath, fp, "rejected", "path")
    quit(0)
  let ca = bundle(roots, "x509audit_roots.pem")
  var cmd = "openssl verify -CAfile " & quoteArg(ca)
  if inters.len > 0:
    let first = bundle(@[inters[0]], "x509audit_inters.pem")
    cmd.add(" -untrusted " & quoteArg(first))
  cmd.add(" " & quoteArg(leaf))
  let checked = run(cmd)
  createDir(parentDir(outPath))
  if checked.code == 0:
    writeReport(outPath, fp, "trusted", "ok")
  else:
    writeReport(outPath, fp, "rejected", "path")
