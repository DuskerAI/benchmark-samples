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

    fun parse(): JValue {
        val v = value()
        ws()
        if (p != text.length) throw JsonError("trailing data")
        return v
    }

    private fun value(): JValue {
        ws()
        if (p >= text.length) throw JsonError("unexpected end")
        return when (text[p]) {
            '{' -> obj()
            '[' -> arr()
            '"' -> JStr(str())
            't' -> { lit("true"); JBool(true) }
            'f' -> { lit("false"); JBool(false) }
            'n' -> { lit("null"); JNull }
            else -> num()
        }
    }

    private fun obj(): JObj {
        p++
        val m = LinkedHashMap<String, JValue>()
        ws()
        if (peek('}')) return JObj(m)
        while (true) {
            ws()
            if (p >= text.length || text[p] != '"') throw JsonError("object key")
            val k = str()
            ws()
            need(':')
            m[k] = value()
            ws()
            if (peek('}')) break
            need(',')
        }
        return JObj(m)
    }

    private fun arr(): JArr {
        p++
        val xs = ArrayList<JValue>()
        ws()
        if (peek(']')) return JArr(xs)
        while (true) {
            xs.add(value())
            ws()
            if (peek(']')) break
            need(',')
        }
        return JArr(xs)
    }

    private fun str(): String {
        need('"')
        val b = StringBuilder()
        while (p < text.length) {
            val c = text[p++]
            if (c == '"') return b.toString()
            if (c == '\\') {
                if (p >= text.length) throw JsonError("escape")
                when (val e = text[p++]) {
                    '"' -> b.append('"')
                    '\\' -> b.append('\\')
                    '/' -> b.append('/')
                    'b' -> b.append('\b')
                    'f' -> b.append('\u000c')
                    'n' -> b.append('\n')
                    'r' -> b.append('\r')
                    't' -> b.append('\t')
                    'u' -> {
                        if (p + 4 > text.length) throw JsonError("unicode")
                        val h = text.substring(p, p + 4)
                        p += 4
                        b.append(Integer.parseInt(h, 16).toChar())
                    }
                    else -> throw JsonError("escape $e")
                }
            } else {
                if (c < ' ') throw JsonError("control")
                b.append(c)
            }
        }
        throw JsonError("string")
    }

    private fun num(): JNum {
        val s = p
        if (p < text.length && text[p] == '-') p++
        if (p >= text.length || !text[p].isDigit()) throw JsonError("number")
        while (p < text.length && text[p].isDigit()) p++
        return JNum(text.substring(s, p).toLong())
    }

    private fun lit(s: String) {
        if (!text.startsWith(s, p)) throw JsonError("literal")
        p += s.length
    }

    private fun need(c: Char) {
        if (p >= text.length || text[p] != c) throw JsonError("expected $c")
        p++
    }

    private fun peek(c: Char): Boolean {
        if (p < text.length && text[p] == c) { p++; return true }
        return false
    }

    private fun ws() {
        while (p < text.length && (text[p] == ' ' || text[p] == '\n' || text[p] == '\r' || text[p] == '\t')) p++
    }
}

fun parseJson(s: String): JValue = JsonParser(s).parse()

fun obj(v: JValue): JObj = v as? JObj ?: throw JsonError("object expected")
fun arr(v: JValue): JArr = v as? JArr ?: throw JsonError("array expected")
fun strv(v: JValue): String = (v as? JStr)?.value ?: throw JsonError("string expected")
fun numv(v: JValue): Long = (v as? JNum)?.value ?: throw JsonError("number expected")
fun boolv(v: JValue): Boolean = (v as? JBool)?.value ?: throw JsonError("boolean expected")
fun JObj.getObj(k: String) = obj(map[k] ?: throw JsonError("missing $k"))
fun JObj.getArr(k: String) = arr(map[k] ?: throw JsonError("missing $k"))
fun JObj.getStr(k: String) = strv(map[k] ?: throw JsonError("missing $k"))
fun JObj.getNum(k: String) = numv(map[k] ?: throw JsonError("missing $k"))

fun jsonText(v: JValue): String {
    val b = StringBuilder()
    writeJson(v, b)
    return b.toString()
}

fun canonicalBytes(v: JValue): ByteArray = jsonText(v).toByteArray(Charsets.UTF_8)

private fun writeJson(v: JValue, b: StringBuilder) {
    when (v) {
        is JObj -> {
            b.append('{')
            var first = true
            for ((k, x) in v.map) {
                if (!first) b.append(',')
                first = false
                writeString(k, b)
                b.append(':')
                writeJson(x, b)
            }
            b.append('}')
        }
        is JArr -> {
            b.append('[')
            for (i in v.items.indices) {
                if (i != 0) b.append(',')
                writeJson(v.items[i], b)
            }
            b.append(']')
        }
        is JStr -> writeString(v.value, b)
        is JNum -> b.append(v.value)
        is JBool -> b.append(if (v.value) "true" else "false")
        JNull -> b.append("null")
    }
}

private fun writeString(s: String, b: StringBuilder) {
    b.append('"')
    for (c in s) {
        when (c) {
            '"' -> b.append("\\\"")
            '\\' -> b.append("\\\\")
            '\n' -> b.append("\\n")
            '\r' -> b.append("\\r")
            '\t' -> b.append("\\t")
            else -> if (c < ' ' || c.toInt() > 126) b.append(String.format("\\u%04x", c.toInt())) else b.append(c)
        }
    }
    b.append('"')
}
