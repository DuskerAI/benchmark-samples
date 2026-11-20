import java.io.File
import kotlin.system.exitProcess

fun main(args: Array<String>) {
    val m = LinkedHashMap<String, String>()
    var i = 0
    while (i < args.size) {
        val k = args[i]
        if (!k.startsWith("--") || i + 1 >= args.size) usage()
        m[k] = args[i + 1]
        i += 2
    }
    val trust = m["--trust"] ?: usage()
    val bundle = m["--bundle"] ?: usage()
    val artifact = m["--artifact"] ?: usage()
    val out = m["--out"] ?: usage()
    val ok = Auditor(File(trust), File(bundle), File(artifact)).run(File(out))
    exitProcess(if (ok) 0 else 1)
}

fun usage(): Nothing {
    System.err.println("usage: sigcheck --trust TRUST --bundle BUNDLE --artifact ARTIFACT --out REPORT")
    exitProcess(2)
}
