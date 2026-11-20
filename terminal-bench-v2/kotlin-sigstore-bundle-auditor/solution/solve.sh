#!/bin/bash
set -euo pipefail
cd /app/sigcheck
python3 - <<'PY'
from pathlib import Path

auditor = Path("src/main/kotlin/Auditor.kt").read_text(encoding="utf-8")
json_parser = Path("src/main/kotlin/Json.kt").read_text(encoding="utf-8")
crypto = Path("src/main/kotlin/Crypto.kt").read_text(encoding="utf-8")

expected_weaknesses = {
    "hmac computed over raw payload instead of DSSE PAE": "hmacSha256(key, payload)" in auditor,
    "payloadType accepted by prefix rather than exact media type": "payloadType.startsWith(\"application/\")" in auditor,
    "artifact digest is not compared with statement digest": "digest.isEmpty() || artifactHash.isEmpty()" in auditor,
    "certificate time bounds are exclusive": "signingTime <= cert.getNum(\"notBefore\")" in auditor and "signingTime >= cert.getNum(\"notAfter\")" in auditor,
    "transparency proof folds by lexical hash order": "b64uEnc(node) < b64uEnc(sib)" in auditor,
    "checkpoint treeSize is only range-checked, not bound to proof depth": "entry.getNum(\"logIndex\") < checkpoint.getNum(\"treeSize\")" in auditor,
    "JSON object parser silently overwrites duplicate keys": "m[k] = value()" in json_parser and "containsKey" not in json_parser,
    "canonical JSON serializer escapes non-ASCII text": "c.toInt() > 126" in json_parser,
    "trust key selection accepts duplicate key identifiers": "fun keyFor" in auditor and "duplicate" not in auditor,
    "trust key strength and allowlist uniqueness are not validated": "key.size < 32" not in auditor and "validateStringArray" not in auditor,
    "base64url decoding accepts padding and standard decoder forms": "Base64.getUrlDecoder().decode(s + \"=\".repeat(pad))" in crypto,
    "certificate SAN validation accepts extra non-URI identities": "for (x in cert.getArr(\"san\").items)" in auditor,
    "statement predicate contract is not validated": "predicateType" not in auditor and "builder" not in auditor,
    "statement and checkpoint schemas are not closed over policy fields": "entryBodyMatches" not in auditor and "statement.map[\"subject\"]" in auditor,
}

missing = [name for name, present in expected_weaknesses.items() if not present]
if missing:
    raise SystemExit("unexpected starting source shape; missing inspected issues: " + "; ".join(missing))

print("inspected existing verifier weaknesses before applying patch")
PY
cat > src/main/kotlin/Json.kt <<'KOTLIN'
import java.lang.StringBuilder

sealed class JValue
data class JObj(val map: LinkedHashMap<String, JValue>) : JValue()
data class JArr(val items: List<JValue>) : JValue()
data class JStr(val value: String) : JValue()
data class JNum(val value: Long) : JValue()
data class JBool(val value: Boolean) : JValue()
object JNull : JValue()

class JsonError(message: String) : RuntimeException(message)

class JsonParser(private val text: String) {
    private var p = 0
    fun parse(): JValue { val v = value(); ws(); if (p != text.length) throw JsonError("trailing data"); return v }
    private fun value(): JValue {
        ws(); if (p >= text.length) throw JsonError("unexpected end")
        return when (text[p]) {
            '{' -> obj(); '[' -> arr(); '"' -> JStr(str())
            't' -> { lit("true"); JBool(true) }; 'f' -> { lit("false"); JBool(false) }; 'n' -> { lit("null"); JNull }
            else -> num()
        }
    }
    private fun obj(): JObj {
        p++; val m = LinkedHashMap<String, JValue>(); ws(); if (peek('}')) return JObj(m)
        while (true) {
            ws(); if (p >= text.length || text[p] != '"') throw JsonError("object key")
            val k = str(); if (m.containsKey(k)) throw JsonError("duplicate key")
            ws(); need(':'); m[k] = value(); ws(); if (peek('}')) break; need(',')
        }
        return JObj(m)
    }
    private fun arr(): JArr {
        p++; val xs = ArrayList<JValue>(); ws(); if (peek(']')) return JArr(xs)
        while (true) { xs.add(value()); ws(); if (peek(']')) break; need(',') }
        return JArr(xs)
    }
    private fun str(): String {
        need('"'); val b = StringBuilder()
        while (p < text.length) {
            val c = text[p++]; if (c == '"') return b.toString()
            if (c == '\\') {
                if (p >= text.length) throw JsonError("escape")
                when (val e = text[p++]) {
                    '"' -> b.append('"'); '\\' -> b.append('\\'); '/' -> b.append('/'); 'b' -> b.append('\b')
                    'f' -> b.append('\u000c'); 'n' -> b.append('\n'); 'r' -> b.append('\r'); 't' -> b.append('\t')
                    'u' -> { if (p + 4 > text.length) throw JsonError("unicode"); val h = text.substring(p, p + 4); p += 4; b.append(Integer.parseInt(h, 16).toChar()) }
                    else -> throw JsonError("escape $e")
                }
            } else { if (c < ' ') throw JsonError("control"); b.append(c) }
        }
        throw JsonError("string")
    }
    private fun num(): JNum {
        val s = p; if (p < text.length && text[p] == '-') p++; if (p >= text.length || !text[p].isDigit()) throw JsonError("number")
        if (text[p] == '0' && p + 1 < text.length && text[p + 1].isDigit()) throw JsonError("number")
        while (p < text.length && text[p].isDigit()) p++; return JNum(text.substring(s, p).toLong())
    }
    private fun lit(s: String) { if (!text.startsWith(s, p)) throw JsonError("literal"); p += s.length }
    private fun need(c: Char) { if (p >= text.length || text[p] != c) throw JsonError("expected $c"); p++ }
    private fun peek(c: Char): Boolean { if (p < text.length && text[p] == c) { p++; return true }; return false }
    private fun ws() { while (p < text.length && (text[p] == ' ' || text[p] == '\n' || text[p] == '\r' || text[p] == '\t')) p++ }
}

fun parseJson(s: String): JValue = JsonParser(s).parse()
fun obj(v: JValue): JObj = v as? JObj ?: throw JsonError("object expected")
fun arr(v: JValue): JArr = v as? JArr ?: throw JsonError("array expected")
fun strv(v: JValue): String = (v as? JStr)?.value ?: throw JsonError("string expected")
fun numv(v: JValue): Long = (v as? JNum)?.value ?: throw JsonError("number expected")
fun JObj.getObj(k: String) = obj(map[k] ?: throw JsonError("missing $k"))
fun JObj.getArr(k: String) = arr(map[k] ?: throw JsonError("missing $k"))
fun JObj.getStr(k: String) = strv(map[k] ?: throw JsonError("missing $k"))
fun JObj.getNum(k: String) = numv(map[k] ?: throw JsonError("missing $k"))

fun jsonText(v: JValue): String { val b = StringBuilder(); writeJson(v, b); return b.toString() }
fun canonicalBytes(v: JValue): ByteArray = jsonText(v).toByteArray(Charsets.UTF_8)

private fun writeJson(v: JValue, b: StringBuilder) {
    when (v) {
        is JObj -> { b.append('{'); var first = true; for (k in v.map.keys.sorted()) { if (!first) b.append(','); first = false; writeString(k, b); b.append(':'); writeJson(v.map[k]!!, b) }; b.append('}') }
        is JArr -> { b.append('['); for (i in v.items.indices) { if (i != 0) b.append(','); writeJson(v.items[i], b) }; b.append(']') }
        is JStr -> writeString(v.value, b); is JNum -> b.append(v.value); is JBool -> b.append(if (v.value) "true" else "false"); JNull -> b.append("null")
    }
}
private fun writeString(s: String, b: StringBuilder) {
    b.append('"')
    for (c in s) when (c) { '"' -> b.append("\\\""); '\\' -> b.append("\\\\"); '\b' -> b.append("\\b"); '\u000c' -> b.append("\\f"); '\n' -> b.append("\\n"); '\r' -> b.append("\\r"); '\t' -> b.append("\\t"); else -> if (c < ' ') b.append(String.format("\\u%04x", c.toInt())) else b.append(c) }
    b.append('"')
}
KOTLIN
cat > src/main/kotlin/Auditor.kt <<'KOTLIN'
import java.io.File

class Auditor(private val trustFile: File, private val bundleFile: File, private val artifactFile: File) {
    private val errors = ArrayList<String>()
    private val b64uRe = Regex("[A-Za-z0-9_-]+")
    private val hexRe = Regex("[0-9a-f]{64}")

    fun run(outFile: File): Boolean {
        val report = LinkedHashMap<String, JValue>()
        var subjectOut = ""
        var issuerOut = ""
        var indexOut = -1L
        try {
            val trust = obj(parseJson(trustFile.readText(Charsets.UTF_8)))
            validateTrust(trust)
            val bundle = obj(parseJson(bundleFile.readText(Charsets.UTF_8)))
            if (bundle.map.keys.toSet() != setOf("mediaType", "dsseEnvelope", "verificationMaterial")) errors.add("bundle")
            if (bundle.getStr("mediaType") != "application/vnd.dev.sigstore.bundle+json;version=0.3") errors.add("mediaType")

            val envelope = bundle.getObj("dsseEnvelope")
            if (envelope.map.keys.toSet() != setOf("payload", "payloadType", "signatures")) errors.add("envelope")
            val payloadType = envelope.getStr("payloadType")
            if (payloadType != "application/vnd.in-toto+json") errors.add("payloadType")
            val payload = b64uStrict(envelope.getStr("payload"))
            val statement = obj(parseJson(payload.toString(Charsets.UTF_8)))
            validateStatement(statement, artifactFile.readBytes())

            val sigs = envelope.getArr("signatures").items.map { obj(it) }
            if (sigs.size != 1) errors.add("signature")
            val sig = sigs.firstOrNull() ?: throw JsonError("signature")
            if (sig.map.keys.toSet() != setOf("keyid", "sig")) errors.add("signature")
            val keyid = sig.getStr("keyid")
            val key = keyFor(trust, keyid)
            if (!same(payload, canonicalBytes(statement))) errors.add("payload")
            val expectedSig = hmacSha256(key, dssePae(payloadType, payload))
            if (!same(expectedSig, b64uStrict(sig.getStr("sig")))) errors.add("signature")

            val material = bundle.getObj("verificationMaterial")
            if (material.map.keys.toSet() != setOf("certificate", "tlogEntries")) errors.add("verificationMaterial")
            val cert = material.getObj("certificate")
            if (cert.map.keys.toSet() != setOf("issuer", "keyid", "notAfter", "notBefore", "san", "subject")) errors.add("certificate")
            val entries = material.getArr("tlogEntries").items.map { obj(it) }
            if (entries.size != 1) errors.add("transparency")
            val entry = entries.firstOrNull() ?: throw JsonError("entry")
            if (entry.map.keys.toSet() != setOf("body", "checkpoint", "inclusionProof", "integratedTime", "logIndex")) errors.add("transparency")
            val checkpoint = entry.getObj("checkpoint")
            if (checkpoint.map.keys.toSet() != setOf("logId", "rootHash", "treeSize")) errors.add("checkpoint")
            if (!logAllowed(trust, checkpoint.getStr("logId"))) errors.add("log")

            issuerOut = cert.getStr("issuer")
            val identity = policyIdentity(cert)
            subjectOut = identity
            indexOut = entry.getNum("logIndex")
            val signingTime = entry.getNum("integratedTime")
            if (signingTime < 0 || cert.getNum("notBefore") < 0 || cert.getNum("notAfter") < 0) errors.add("time")
            if (!issuerAllowed(trust, issuerOut)) errors.add("issuer")
            if (signingTime < cert.getNum("notBefore") || signingTime > cert.getNum("notAfter")) errors.add("time")
            if (cert.getStr("keyid") != keyid) errors.add("keyid")
            if (!subjectAllowed(trust, identity)) errors.add("identity")

            val statementSubject = statement.getArr("subject").items.map { obj(it) }.singleOrNull() ?: throw JsonError("subject")
            val digest = statementSubject.getObj("digest").getStr("sha256")
            if (digest != sha256Hex(artifactFile.readBytes())) errors.add("digest")
            if (!entryBodyMatches(entry.getObj("body"), identity, issuerOut, signingTime, sha256Hex(payload))) errors.add("body")
            if (!verifyEntry(entry)) errors.add("transparency")
        } catch (e: Exception) {
            errors.add("malformed")
        }
        report["ok"] = JBool(errors.isEmpty())
        report["errors"] = JArr(errors.map { JStr(it) })
        report["subject"] = JStr(subjectOut)
        report["issuer"] = JStr(issuerOut)
        report["logIndex"] = JNum(indexOut)
        outFile.parentFile?.mkdirs()
        outFile.writeText(jsonText(JObj(report)), Charsets.UTF_8)
        return errors.isEmpty()
    }

    private fun validateTrust(trust: JObj) {
        if (trust.map.keys.toSet() != setOf("trustedKeys", "issuers", "subjects", "logs")) errors.add("trust")
        val seen = HashSet<String>()
        for (x in trust.getArr("trustedKeys").items) {
            val keyObj = obj(x)
            if (keyObj.map.keys.toSet() != setOf("keyid", "key")) errors.add("trust")
            val keyid = keyObj.getStr("keyid")
            if (keyid.isEmpty() || !seen.add(keyid)) errors.add("trust")
            if (b64uStrict(keyObj.getStr("key")).size < 32) errors.add("trust")
        }
        validateStringArray(trust, "issuers")
        validateStringArray(trust, "subjects")
        validateStringArray(trust, "logs")
    }

    private fun validateStringArray(trust: JObj, name: String) {
        val seen = HashSet<String>()
        val values = trust.getArr(name).items
        if (values.isEmpty()) errors.add("trust")
        for (x in values) {
            val value = strv(x)
            if (value.isEmpty() || !seen.add(value)) errors.add("trust")
        }
    }

    private fun validateStatement(statement: JObj, artifact: ByteArray) {
        if (statement.map.keys.toSet() != setOf("_type", "predicateType", "subject", "predicate")) errors.add("statement")
        if (statement.getStr("_type") != "https://in-toto.io/Statement/v1") errors.add("statement")
        if (statement.getStr("predicateType") != "https://slsa.dev/provenance/v1") errors.add("statement")
        val subjects = statement.getArr("subject").items.map { obj(it) }
        if (subjects.size != 1) errors.add("digest")
        val subject = subjects.firstOrNull() ?: throw JsonError("subject")
        if (subject.map.keys.toSet() != setOf("digest", "name")) errors.add("digest")
        if (subject.getStr("name").isEmpty()) errors.add("digest")
        val digestObj = subject.getObj("digest")
        if (digestObj.map.keys.toSet() != setOf("sha256")) errors.add("digest")
        val digest = digestObj.getStr("sha256")
        if (!hexRe.matches(digest) || digest != sha256Hex(artifact)) errors.add("digest")
        val predicate = obj(statement.map["predicate"] ?: throw JsonError("predicate"))
        if (predicate.map.keys.toSet() != setOf("builder", "source")) errors.add("statement")
        val builder = predicate.getObj("builder")
        if (builder.map.keys.toSet() != setOf("id") || builder.getStr("id").isEmpty()) errors.add("statement")
        val source = predicate.getObj("source")
        val sourceUri = source.getStr("uri")
        if (source.map.keys.toSet() != setOf("uri") || sourceUri.isEmpty() || !sourceUri.startsWith("https://")) errors.add("statement")
    }

    private fun dssePae(payloadType: String, payload: ByteArray): ByteArray = ("DSSEv1 " + payloadType.toByteArray(Charsets.UTF_8).size + " " + payloadType + " " + payload.size + " ").toByteArray(Charsets.UTF_8) + payload

    private fun b64uStrict(s: String): ByteArray {
        if (s.isEmpty() || s.contains("=") || !b64uRe.matches(s) || s.length % 4 == 1) throw JsonError("base64url")
        return b64u(s)
    }

    private fun keyFor(trust: JObj, keyid: String): ByteArray {
        for (x in trust.getArr("trustedKeys").items) {
            val o = obj(x)
            if (o.getStr("keyid") == keyid) return b64uStrict(o.getStr("key"))
        }
        throw JsonError("key")
    }
    private fun issuerAllowed(trust: JObj, issuer: String): Boolean = trust.getArr("issuers").items.any { strv(it) == issuer }
    private fun subjectAllowed(trust: JObj, subject: String): Boolean = trust.getArr("subjects").items.any { strv(it) == subject }
    private fun logAllowed(trust: JObj, logId: String): Boolean = trust.getArr("logs").items.any { strv(it) == logId }

    private fun policyIdentity(cert: JObj): String {
        val san = cert.getArr("san").items
        if (san.size != 1) throw JsonError("identity")
        val uri = obj(san[0])
        if (uri.map.keys.toSet() != setOf("type", "value") || uri.getStr("type") != "uri") throw JsonError("identity")
        return uri.getStr("value")
    }

    private fun entryBodyMatches(body: JObj, subject: String, issuer: String, integratedTime: Long, payloadHash: String): Boolean = try {
        body.map.keys.toSet() == setOf("subject", "issuer", "integratedTime", "payloadHash", "tag") &&
            body.getStr("payloadHash") == payloadHash &&
            body.getStr("subject") == subject &&
            body.getStr("issuer") == issuer &&
            body.getNum("integratedTime") == integratedTime &&
            body.getStr("tag").isNotEmpty()
    } catch (e: Exception) { false }

    private fun verifyEntry(entry: JObj): Boolean {
        val checkpoint = entry.getObj("checkpoint")
        var node = merkleLeaf(canonicalBytes(entry.getObj("body")))
        var idx = entry.getNum("logIndex")
        val size = checkpoint.getNum("treeSize")
        val root = b64uStrict(checkpoint.getStr("rootHash"))
        if (root.size != 32) return false
        if (idx < 0 || idx >= size || size <= 0) return false
        if ((size and (size - 1L)) != 0L) return false
        var depth = 0
        var span = size
        while (span > 1L) { depth++; span /= 2L }
        val proof = entry.getArr("inclusionProof").items
        if (proof.size != depth) return false
        for (x in proof) {
            val sib = b64uStrict(strv(x))
            if (sib.size != 32) return false
            node = if (idx % 2L == 0L) merkleParent(node, sib) else merkleParent(sib, node)
            idx /= 2
        }
        return same(node, root)
    }
}
KOTLIN
/app/sigcheck/bin/build.sh
