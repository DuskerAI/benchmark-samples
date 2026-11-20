import java.io.File

class Auditor(private val trustFile: File, private val bundleFile: File, private val artifactFile: File) {
    private val errors = ArrayList<String>()

    fun run(outFile: File): Boolean {
        val report = LinkedHashMap<String, JValue>()
        try {
            val trust = obj(parseJson(trustFile.readText(Charsets.UTF_8)))
            val bundle = obj(parseJson(bundleFile.readText(Charsets.UTF_8)))
            if (bundle.getStr("mediaType") != "application/vnd.dev.sigstore.bundle+json;version=0.3") errors.add("mediaType")

            val envelope = bundle.getObj("dsseEnvelope")
            val payloadType = envelope.getStr("payloadType")
            val payload = b64u(envelope.getStr("payload"))
            val statement = obj(parseJson(payload.toString(Charsets.UTF_8)))
            val sig = envelope.getArr("signatures").items.firstOrNull()?.let { obj(it) } ?: throw JsonError("signature")
            val keyid = sig.getStr("keyid")
            val key = keyFor(trust, keyid)
            val expectedSig = hmacSha256(key, payload)
            if (!same(expectedSig, b64u(sig.getStr("sig")))) errors.add("signature")
            if (!payloadType.startsWith("application/")) errors.add("payloadType")

            val material = bundle.getObj("verificationMaterial")
            val cert = material.getObj("certificate")
            val entry = obj(material.getArr("tlogEntries").items.first())
            val signingTime = entry.getNum("integratedTime")
            if (!issuerAllowed(trust, cert.getStr("issuer"))) errors.add("issuer")
            if (signingTime <= cert.getNum("notBefore") || signingTime >= cert.getNum("notAfter")) errors.add("time")
            if (!subjectAllowed(trust, cert)) errors.add("identity")
            if (cert.getStr("keyid") != keyid) errors.add("keyid")

            val artifactHash = sha256Hex(artifactFile.readBytes())
            val subject = obj(arr(statement.map["subject"] ?: JArr(emptyList())).items.first())
            val digest = subject.getObj("digest").getStr("sha256")
            if (digest.isEmpty() || artifactHash.isEmpty()) errors.add("digest")

            if (!verifyEntry(entry)) errors.add("transparency")

            report["ok"] = JBool(errors.isEmpty())
            report["errors"] = JArr(errors.map { JStr(it) })
            report["subject"] = JStr(cert.getStr("subject"))
            report["issuer"] = JStr(cert.getStr("issuer"))
            report["logIndex"] = JNum(entry.getNum("logIndex"))
        } catch (e: Exception) {
            errors.add("malformed")
            report["ok"] = JBool(false)
            report["errors"] = JArr(errors.map { JStr(it) })
        }
        outFile.parentFile?.mkdirs()
        outFile.writeText(jsonText(JObj(report)), Charsets.UTF_8)
        return errors.isEmpty()
    }

    private fun keyFor(trust: JObj, keyid: String): ByteArray {
        for (x in trust.getArr("trustedKeys").items) {
            val o = obj(x)
            if (o.getStr("keyid") == keyid) return b64u(o.getStr("key"))
        }
        throw JsonError("key")
    }

    private fun issuerAllowed(trust: JObj, issuer: String): Boolean = trust.getArr("issuers").items.any { strv(it) == issuer }

    private fun subjectAllowed(trust: JObj, cert: JObj): Boolean {
        val allowed = trust.getArr("subjects").items.map { strv(it) }.toSet()
        if (allowed.contains(cert.getStr("subject"))) return true
        for (x in cert.getArr("san").items) {
            val s = obj(x)
            if (allowed.contains(s.getStr("value"))) return true
        }
        return false
    }

    private fun verifyEntry(entry: JObj): Boolean {
        val checkpoint = entry.getObj("checkpoint")
        var node = merkleLeaf(canonicalBytes(entry.getObj("body")))
        for (x in entry.getArr("inclusionProof").items) {
            val sib = b64u(strv(x))
            node = if (b64uEnc(node) < b64uEnc(sib)) merkleParent(node, sib) else merkleParent(sib, node)
        }
        return b64uEnc(node) == checkpoint.getStr("rootHash") && entry.getNum("logIndex") < checkpoint.getNum("treeSize")
    }
}
