import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import java.util.Base64

fun sha256(data: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(data)

fun sha256Hex(data: ByteArray): String = sha256(data).joinToString("") { "%02x".format(it.toInt() and 0xff) }

fun hmacSha256(key: ByteArray, data: ByteArray): ByteArray {
    val mac = Mac.getInstance("HmacSHA256")
    mac.init(SecretKeySpec(key, "HmacSHA256"))
    return mac.doFinal(data)
}

fun b64u(s: String): ByteArray {
    val pad = (4 - s.length % 4) % 4
    return Base64.getUrlDecoder().decode(s + "=".repeat(pad))
}

fun b64uEnc(b: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(b)

fun merkleLeaf(body: ByteArray): ByteArray = sha256(byteArrayOf(0) + body)

fun merkleParent(left: ByteArray, right: ByteArray): ByteArray = sha256(byteArrayOf(1) + left + right)

fun same(a: ByteArray, b: ByteArray): Boolean = MessageDigest.isEqual(a, b)
