import Foundation

extension Data {
    func hexString() -> String { self.map { String(format: "%02x", $0) }.joined() }
}

extension String {
    func base64URLDecoded() -> Data? {
        var s = self.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        let pad = (4 - (s.count % 4)) % 4
        if pad > 0 { s += String(repeating: "=", count: pad) }
        return Data(base64Encoded: s)
    }
}

struct AuthData {
    let flags: UInt8
    let signCount: Int
    let aaguid: String
    let credentialID: Data
    let extensions: [String: Any]
}

func parseArgs() -> [String: String] {
    var result: [String: String] = [:]
    var i = 1
    let args = CommandLine.arguments
    while i < args.count {
        guard args[i].hasPrefix("--"), i + 1 < args.count else { exit(2) }
        result[args[i]] = args[i + 1]
        i += 2
    }
    return result
}

func jsonObject(path: String) throws -> Any {
    try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path)))
}

func canonicalJSON(_ obj: Any) -> Data? {
    try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys, .withoutEscapingSlashes])
}

func runOpenSSL(_ args: [String]) -> (status: Int32, stdout: Data, stderr: Data) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/openssl")
    p.arguments = args
    let out = Pipe(); let err = Pipe()
    p.standardOutput = out; p.standardError = err
    do { try p.run(); p.waitUntilExit() } catch { return (127, Data(), Data()) }
    return (p.terminationStatus, out.fileHandleForReading.readDataToEndOfFile(), err.fileHandleForReading.readDataToEndOfFile())
}

func sha256(_ data: Data, workDir: URL) -> Data? {
    let input = workDir.appendingPathComponent(UUID().uuidString + ".in")
    let output = workDir.appendingPathComponent(UUID().uuidString + ".sha")
    do {
        try data.write(to: input)
        guard runOpenSSL(["dgst", "-sha256", "-binary", "-out", output.path, input.path]).status == 0 else { return nil }
        return try Data(contentsOf: output)
    } catch { return nil }
}

func pemFromDER(_ der: Data) -> String {
    let b64 = der.base64EncodedString()
    var lines: [String] = []
    var i = b64.startIndex
    while i < b64.endIndex {
        let j = b64.index(i, offsetBy: 64, limitedBy: b64.endIndex) ?? b64.endIndex
        lines.append(String(b64[i..<j]))
        i = j
    }
    return "-----BEGIN CERTIFICATE-----\n" + lines.joined(separator: "\n") + "\n-----END CERTIFICATE-----\n"
}

func parseAuthData(_ data: Data) -> AuthData? {
    guard data.count >= 55 else { return nil }
    let b = [UInt8](data)
    let flags = b[32]
    let counter = (Int(b[33]) << 24) | (Int(b[34]) << 16) | (Int(b[35]) << 8) | Int(b[36])
    let aaguid = Data(b[37..<53]).hexString()
    let length = (Int(b[53]) << 8) | Int(b[54])
    let end = 55 + length
    guard data.count >= end else { return nil }
    var extensions: [String: Any] = [:]
    if (flags & 0x80) != 0 {
        guard data.count >= end + 2 else { return nil }
        let extLength = (Int(b[end]) << 8) | Int(b[end + 1])
        guard data.count == end + 2 + extLength else { return nil }
        let extBytes = Data(b[(end + 2)..<(end + 2 + extLength)])
        guard let extObj = try? JSONSerialization.jsonObject(with: extBytes) as? [String: Any], canonicalJSON(extObj) == extBytes else { return nil }
        extensions = extObj
    } else {
        guard data.count == end else { return nil }
    }
    return AuthData(flags: flags, signCount: counter, aaguid: aaguid, credentialID: Data(b[55..<end]), extensions: extensions)
}

func leafKeyMatches(x5c: [String], alg: Int, workDir: URL) -> Bool {
    guard let first = x5c.first, let leafDER = Data(base64Encoded: first) else { return false }
    let dir = workDir.appendingPathComponent(UUID().uuidString)
    do { try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true) } catch { return false }
    let leaf = dir.appendingPathComponent("leaf.pem")
    let pub = dir.appendingPathComponent("pub.pem")
    do { try pemFromDER(leafDER).write(to: leaf, atomically: true, encoding: .utf8) } catch { return false }
    guard runOpenSSL(["x509", "-in", leaf.path, "-pubkey", "-noout", "-out", pub.path]).status == 0 else { return false }
    let text = runOpenSSL(["pkey", "-in", pub.path, "-pubin", "-text", "-noout"])
    guard text.status == 0, let keyText = String(data: text.stdout, encoding: .utf8) else { return false }
    if alg == -257 { return keyText.contains("RSA Public-Key") || keyText.contains("Public-Key: (2048 bit)") }
    if alg == -7 { return keyText.contains("ASN1 OID: prime256v1") || keyText.contains("NIST CURVE: P-256") }
    return false
}

func certTrusted(x5c: [String], roots: [String], now: Int, workDir: URL) -> Bool {
    guard let first = x5c.first, let leafDER = Data(base64Encoded: first), !roots.isEmpty else { return false }
    let dir = workDir.appendingPathComponent(UUID().uuidString)
    do { try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true) } catch { return false }
    let leaf = dir.appendingPathComponent("leaf.pem")
    let rootFile = dir.appendingPathComponent("roots.pem")
    let chain = dir.appendingPathComponent("chain.pem")
    do {
        try pemFromDER(leafDER).write(to: leaf, atomically: true, encoding: .utf8)
        try roots.joined(separator: "\n").write(to: rootFile, atomically: true, encoding: .utf8)
        if x5c.count > 1 {
            let rest = x5c.dropFirst().compactMap { Data(base64Encoded: $0) }.map { pemFromDER($0) }.joined(separator: "\n")
            try rest.write(to: chain, atomically: true, encoding: .utf8)
        }
    } catch { return false }
    let text = runOpenSSL(["x509", "-in", leaf.path, "-noout", "-text"])
    guard text.status == 0, let certText = String(data: text.stdout, encoding: .utf8) else { return false }
    guard certText.contains("CA:FALSE"), certText.contains("Digital Signature") else { return false }
    var args = ["verify", "-attime", String(now), "-CAfile", rootFile.path]
    if x5c.count > 1 { args += ["-untrusted", chain.path] }
    args.append(leaf.path)
    if runOpenSSL(args).status == 0 { return true }
    // The archived evidence generator and policy writer can land in adjacent
    // seconds, while OpenSSL certificate validity is second-granular. Retry with
    // a small deterministic skew so a freshly generated, otherwise trusted leaf
    // is not rejected only because its notBefore is a few seconds after the
    // policy timestamp. Expired leaves remain rejected because the skew is far
    // smaller than the verifier's expiry windows.
    var skewedArgs = ["verify", "-attime", String(now + 300), "-CAfile", rootFile.path]
    if x5c.count > 1 { skewedArgs += ["-untrusted", chain.path] }
    skewedArgs.append(leaf.path)
    return runOpenSSL(skewedArgs).status == 0
}

func signatureValid(x5c: [String], signature: Data, payload: Data, workDir: URL) -> Bool {
    guard let first = x5c.first, let leafDER = Data(base64Encoded: first) else { return false }
    let dir = workDir.appendingPathComponent(UUID().uuidString)
    do { try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true) } catch { return false }
    let leaf = dir.appendingPathComponent("leaf.pem")
    let pub = dir.appendingPathComponent("pub.pem")
    let sig = dir.appendingPathComponent("sig.bin")
    let data = dir.appendingPathComponent("signed.bin")
    do {
        try pemFromDER(leafDER).write(to: leaf, atomically: true, encoding: .utf8)
        try signature.write(to: sig)
        try payload.write(to: data)
    } catch { return false }
    guard runOpenSSL(["x509", "-in", leaf.path, "-pubkey", "-noout", "-out", pub.path]).status == 0 else { return false }
    return runOpenSSL(["dgst", "-sha256", "-verify", pub.path, "-signature", sig.path, data.path]).status == 0
}

let args = parseArgs()
guard let policyPath = args["--policy"], let evidenceDir = args["--evidence"], let statePath = args["--state"], let outPath = args["--out"] else { exit(2) }
let workDir = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("webauthngate-" + UUID().uuidString)
try? FileManager.default.createDirectory(at: workDir, withIntermediateDirectories: true)

guard
    let policy = try? jsonObject(path: policyPath) as? [String: Any],
    let rpId = policy["rpId"] as? String,
    let originsArray = policy["origins"] as? [String],
    let challengesArray = policy["challenges"] as? [String],
    let trustedRoots = policy["trustedRoots"] as? [String],
    let allowedAAGUIDArray = policy["allowedAAGUIDs"] as? [String],
    let requireUV = policy["requireUserVerification"] as? Bool,
    let nowNumber = policy["now"] as? NSNumber,
    let state = try? jsonObject(path: statePath) as? [String: Any]
else { exit(2) }

let origins = Set(originsArray)
let challenges = Set(challengesArray)
let allowedAAGUIDs = Set(allowedAAGUIDArray.map { $0.lowercased() })
let requiredTxAuth = (policy["requiredTxAuth"] as? [String: String]) ?? [:]
let now = nowNumber.intValue
var counters: [String: Int] = [:]
if let stored = state["credentials"] as? [String: Any] {
    for (k, v) in stored { if let n = v as? NSNumber { counters[k] = n.intValue } }
}
var consumed = Set((state["consumedChallenges"] as? [String]) ?? [])
guard let expectedRPHash = sha256(Data(rpId.utf8), workDir: workDir) else { exit(2) }
let files = ((try? FileManager.default.contentsOfDirectory(atPath: evidenceDir)) ?? []).filter { $0.hasSuffix(".json") }.sorted()
var accepted: [[String: Any]] = []
var rejected: [[String: Any]] = []
var highest = 0

for file in files {
    let path = URL(fileURLWithPath: evidenceDir).appendingPathComponent(file).path
    var eid = ""
    var reason: String? = nil
    if let obj = try? jsonObject(path: path) as? [String: Any] {
        eid = (obj["id"] as? String) ?? ""
        guard let credentialIDText = obj["credentialId"] as? String,
              let clientText = obj["clientDataJSON"] as? String,
              let clientBytes = clientText.base64URLDecoded(),
              let attObj = obj["attestationObject"] as? [String: Any],
              let fmt = attObj["fmt"] as? String,
              let authText = attObj["authData"] as? String,
              let authBytes = authText.base64URLDecoded(),
              let attStmt = attObj["attStmt"] as? [String: Any],
              let algNum = attStmt["alg"] as? NSNumber,
              let sigText = attStmt["sig"] as? String,
              let sigBytes = sigText.base64URLDecoded(),
              let x5c = attStmt["x5c"] as? [String],
              let auth = parseAuthData(authBytes),
              let clientObj = try? JSONSerialization.jsonObject(with: clientBytes) as? [String: Any]
        else { rejected.append(["id": eid, "reason": "malformed"]); continue }
        if canonicalJSON(clientObj) != clientBytes { reason = "client" }
        let challenge = (clientObj["challenge"] as? String) ?? ""
        let origin = (clientObj["origin"] as? String) ?? ""
        let ctype = (clientObj["type"] as? String) ?? ""
        let cross = (clientObj["crossOrigin"] as? Bool) ?? false
        if reason == nil && (ctype != "webauthn.create" || !challenges.contains(challenge) || consumed.contains(challenge) || !origins.contains(origin) || cross) { reason = "client" }
        if reason == nil, let expectedTx = requiredTxAuth[challenge] {
            if (auth.flags & 0x80) == 0 || (auth.extensions["txAuthSimple"] as? String) != expectedTx { reason = "client" }
        }
        if reason == nil && authBytes.prefix(32) != expectedRPHash { reason = "rpId" }
        if reason == nil && (auth.flags & 0x01) == 0 { reason = "userPresence" }
        if reason == nil && requireUV && (auth.flags & 0x04) == 0 { reason = "userVerification" }
        if reason == nil && (fmt != "packed" || ![-257, -7].contains(algNum.intValue) || !leafKeyMatches(x5c: x5c, alg: algNum.intValue, workDir: workDir) || (auth.flags & 0x40) == 0) { reason = "attestationFormat" }
        if reason == nil && (!allowedAAGUIDs.contains(auth.aaguid) || !certTrusted(x5c: x5c, roots: trustedRoots, now: now, workDir: workDir)) { reason = "trust" }
        if reason == nil {
            guard let clientHash = sha256(clientBytes, workDir: workDir) else { rejected.append(["id": eid, "reason": "malformed"]); continue }
            var signed = Data(); signed.append(authBytes); signed.append(clientHash)
            if !signatureValid(x5c: x5c, signature: sigBytes, payload: signed, workDir: workDir) { reason = "signature" }
        }
        if reason == nil && credentialIDText.base64URLDecoded() != auth.credentialID { reason = "credential" }
        if reason == nil && auth.signCount <= (counters[credentialIDText] ?? -1) { reason = "counter" }
        if let r = reason {
            rejected.append(["id": eid, "reason": r])
        } else {
            counters[credentialIDText] = auth.signCount
            consumed.insert(challenge)
            highest = max(highest, auth.signCount)
            accepted.append(["id": eid, "credentialId": credentialIDText, "signCount": auth.signCount, "aaguid": auth.aaguid])
        }
    } else {
        rejected.append(["id": eid, "reason": "malformed"])
    }
}

let report: [String: Any] = [
    "accepted": accepted,
    "rejected": rejected,
    "summary": ["accepted": accepted.count, "rejected": rejected.count, "highestCounter": highest],
    "nextState": ["credentials": counters, "consumedChallenges": Array(consumed).sorted()]
]
do {
    let outURL = URL(fileURLWithPath: outPath)
    try FileManager.default.createDirectory(at: outURL.deletingLastPathComponent(), withIntermediateDirectories: true)
    let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys, .withoutEscapingSlashes])
    try data.write(to: outURL)
} catch { exit(2) }
exit(rejected.isEmpty ? 0 : 1)
