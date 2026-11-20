#!/bin/bash
set -euo pipefail

cd /app

cat > /opt/x509audit/src/x509_path_attest.nim <<'NIM'
import std/[json, os, osproc, strutils, sequtils, algorithm, tables]

proc quoteArg(s: string): string =
  result = "'"
  for ch in s:
    if ch == '\'': result.add("'\\''") else: result.add(ch)
  result.add("'")

proc run(cmd: string): tuple[code: int, outp: string] =
  let r = execCmdEx(cmd & " 2>&1")
  (r.exitCode, r.output)

proc pemFiles(dir: string): seq[string] =
  if dirExists(dir):
    for kind, path in walkDir(dir):
      if kind == pcFile and path.toLowerAscii.endsWith(".pem"):
        result.add(path)
  result.sort()

proc bundle(files: seq[string], name: string): string =
  let p = getTempDir() / (name & "_" & $getCurrentProcessId() & ".pem")
  var data = ""
  for f in files:
    data.add(readFile(f))
    if data.len > 0 and data[^1] != '\n': data.add('\n')
  writeFile(p, data)
  p

proc fingerprint(cert: string): string =
  let r = run("openssl x509 -in " & quoteArg(cert) & " -outform DER | openssl dgst -sha256 -r")
  if r.code != 0 or r.outp.splitWhitespace().len == 0: return ""
  r.outp.splitWhitespace()[0].toLowerAscii()

proc epoch(iso: string): string =
  let r = run("date -u -d " & quoteArg(iso) & " +%s")
  if r.code != 0: return "0"
  r.outp.strip()

proc extText(cert, ext: string): string =
  run("openssl x509 -in " & quoteArg(cert) & " -noout -ext " & quoteArg(ext)).outp

proc certField(cert, field: string): string =
  let r = run("openssl x509 -in " & quoteArg(cert) & " -noout -" & field & " -nameopt oneline")
  if r.code != 0: return ""
  let line = r.outp.strip()
  let pos = line.find('=')
  if pos < 0: line else: line[pos + 1 .. ^1].strip()

proc certSerial(cert: string): string =
  let r = run("openssl x509 -in " & quoteArg(cert) & " -noout -serial")
  if r.code != 0: return ""
  let parts = r.outp.strip().split("=")
  if parts.len < 2: return ""
  parts[1].replace(":", "").toLowerAscii().strip(chars = {'0'})

proc crlIssuer(crl: string): string =
  let r = run("openssl crl -in " & quoteArg(crl) & " -noout -issuer -nameopt oneline")
  if r.code != 0: return ""
  let line = r.outp.strip()
  let pos = line.find('=')
  if pos < 0: line else: line[pos + 1 .. ^1].strip()

proc revokedSerials(crl: string): seq[string] =
  let r = run("openssl crl -in " & quoteArg(crl) & " -noout -text")
  if r.code != 0: return @[]
  for line in r.outp.splitLines():
    let stripped = line.strip()
    if stripped.startsWith("Serial Number:"):
      result.add(stripped.split(":", 1)[1].replace(":", "").toLowerAscii().strip().strip(chars = {'0'}))

proc isRevoked(cert: string, crls: seq[string]): bool =
  let issuer = certField(cert, "issuer")
  let serial = certSerial(cert)
  if issuer.len == 0 or serial.len == 0: return false
  for crl in crls:
    if crlIssuer(crl) == issuer:
      for revoked in revokedSerials(crl):
        if revoked == serial: return true

proc usageOk(cert, usage: string): bool =
  if usage.len == 0: return true
  let text = extText(cert, "extendedKeyUsage")
  case usage
  of "serverAuth":
    result = text.contains("TLS Web Server Authentication") or text.contains("1.3.6.1.5.5.7.3.1") or text.contains("serverAuth")
  of "clientAuth":
    result = text.contains("TLS Web Client Authentication") or text.contains("1.3.6.1.5.5.7.3.2") or text.contains("clientAuth")
  else:
    result = false

proc policyOk(cert, oid: string): bool =
  if oid.len == 0: return true
  extText(cert, "certificatePolicies").contains(oid)

proc writeReport(path, fp, verdict, reason: string) =
  let parent = parentDir(path)
  if parent.len > 0: createDir(parent)
  writeFile(path, "{\"leaf_sha256\":\"" & fp & "\",\"reason\":\"" & reason & "\",\"verdict\":\"" & verdict & "\"}\n")

when isMainModule:
  if paramCount() != 2: quit(2)
  let evidence = paramStr(1)
  let outPath = paramStr(2)
  let req = parseFile(evidence / "request.json")
  let leaf = evidence / "leaf.pem"
  let roots = pemFiles(evidence / "roots")
  let inters = pemFiles(evidence / "intermediates")
  let crls = pemFiles(evidence / "crls")
  let fp = fingerprint(leaf)
  var pathCode = 1
  var pathOutput = ""
  if roots.len > 0:
    let ca = bundle(roots, "x509audit_roots")
    var cmd = "openssl verify -CAfile " & quoteArg(ca)
    if inters.len > 0:
      cmd.add(" -untrusted " & quoteArg(bundle(inters, "x509audit_inters")))
    cmd.add(" -attime " & quoteArg(epoch(req["now"].getStr())))
    cmd.add(" -verify_hostname " & quoteArg(req["dns_name"].getStr()))
    cmd.add(" " & quoteArg(leaf))
    let checked = run(cmd)
    pathCode = checked.code
    pathOutput = checked.outp.toLowerAscii()
  let revoked = isRevoked(leaf, crls) or inters.anyIt(isRevoked(it, crls))
  let ekuGood = usageOk(leaf, req["extended_key_usage"].getStr())
  let policyGood = policyOk(leaf, req["required_policy"].getStr())
  var reason = "ok"
  if pathCode != 0 and (pathOutput.contains("expired") or pathOutput.contains("not yet valid")):
    reason = "time"
  elif revoked:
    reason = "revocation"
  elif pathCode != 0 and (pathOutput.contains("hostname mismatch") or pathOutput.contains("permitted subtree") or pathOutput.contains("excluded subtree") or pathOutput.contains("name constraint")):
    reason = "name"
  elif not ekuGood:
    reason = "usage"
  elif not policyGood:
    reason = "policy"
  elif pathCode != 0:
    reason = "path"
  if reason == "ok": writeReport(outPath, fp, "trusted", reason) else: writeReport(outPath, fp, "rejected", reason)
NIM

make -C /opt/x509audit install
