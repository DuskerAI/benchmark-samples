import java.io.{ByteArrayOutputStream, File}
import java.nio.file.{Files, Path, Paths}
import scala.collection.mutable
import scala.collection.JavaConverters._

object JvmGuard {
  sealed trait Cp
  case object EmptyCp extends Cp
  case class Utf8(v: String) extends Cp
  case class CClass(nameIndex: Int) extends Cp
  case class CString(index: Int) extends Cp
  case class CNameAndType(name: Int, desc: Int) extends Cp
  case class CRef(tag: Int, owner: Int, nt: Int) extends Cp
  case class CNum(tag: Int) extends Cp

  final class R(val data: Array[Byte]) {
    var pos: Int = 0
    def u1(): Int = { val v = data(pos) & 0xff; pos += 1; v }
    def u2(): Int = (u1() << 8) | u1()
    def u4(): Long = ((u1().toLong << 24) | (u1().toLong << 16) | (u1().toLong << 8) | u1().toLong) & 0xffffffffL
    def s2At(p: Int): Int = { val v = ((data(p) & 0xff) << 8) | (data(p + 1) & 0xff); if ((v & 0x8000) != 0) v - 65536 else v }
    def s4At(p: Int): Int = ((data(p) & 0xff) << 24) | ((data(p + 1) & 0xff) << 16) | ((data(p + 2) & 0xff) << 8) | (data(p + 3) & 0xff)
    def bytes(n: Int): Array[Byte] = { val b = data.slice(pos, pos + n); pos += n; b }
    def skip(n: Int): Unit = { pos += n }
  }

  sealed trait V { def show: String }
  case object Top extends V { val show = "top" }
  case object IntV extends V { val show = "int" }
  case object NullV extends V { val show = "null" }
  case class Obj(name: String) extends V { def show = "L" + name + ";" }
  case class Uninit(pc: Int, name: String) extends V { def show = "U" + pc + ":" + name }
  case object UninitThis extends V { val show = "uninit_this" }

  case class Frame(locals: Vector[V], stack: Vector[V])
  case class MethodInfo(name: String, desc: String, flags: Int, code: Option[Code])
  case class Code(maxStack: Int, maxLocals: Int, code: Array[Byte], frames: Map[Int, Frame], handlers: Vector[Int])
  case class ClassResult(name: String, methods: Vector[MethodResult])
  case class MethodResult(name: String, desc: String, ok: Boolean, errors: Vector[String])

  final class ParsedClass(val name: String, val superName: String, val major: Int, val cp: Array[Cp], val methods: Vector[MethodInfo]) {
    def utf(i: Int): String = cp(i).asInstanceOf[Utf8].v
    def cls(i: Int): String = utf(cp(i).asInstanceOf[CClass].nameIndex)
    def nt(i: Int): (String, String) = { val n = cp(i).asInstanceOf[CNameAndType]; (utf(n.name), utf(n.desc)) }
    def ref(i: Int): (String, String, String) = { val r = cp(i).asInstanceOf[CRef]; val (n,d) = nt(r.nt); (cls(r.owner), n, d) }
  }

  def main(args: Array[String]): Unit = {
    val parsed = parseArgs(args.toList)
    val input = Paths.get(parsed.getOrElse("--input", ""))
    val output = Paths.get(parsed.getOrElse("--output", ""))
    if (!Files.isDirectory(input) || output.toString.isEmpty) sys.error("usage: --input <dir> --output <file>")
    val files = Files.walk(input).iterator().asScala.filter(p => Files.isRegularFile(p) && p.toString.endsWith(".class")).toVector.sortBy(_.toString)
    val results = files.map { p =>
      try {
        val cls = parseClass(Files.readAllBytes(p))
        ClassResult(cls.name, cls.methods.map(verifyMethod(cls, _)))
      } catch { case e: Throwable => ClassResult(input.relativize(p).toString, Vector(MethodResult("<class>", "", false, Vector(short(e.getMessage))))) }
    }
    val ok = results.forall(_.methods.forall(_.ok))
    if (output.getParent != null) Files.createDirectories(output.getParent)
    Files.write(output, json(ok, results).getBytes("UTF-8"))
  }

  def parseArgs(xs: List[String]): Map[String,String] = xs match {
    case k :: v :: tail if k.startsWith("--") => parseArgs(tail) + (k -> v)
    case Nil => Map.empty
    case _ => Map.empty
  }

  def parseClass(bytes: Array[Byte]): ParsedClass = {
    val r = new R(bytes)
    if (r.u4() != 0xcafebabeL) throw new RuntimeException("not a class file")
    r.u2(); val major = r.u2()
    val cpCount = r.u2()
    val cp = Array.fill[Cp](cpCount)(EmptyCp)
    var i = 1
    while (i < cpCount) {
      r.u1() match {
        case 1 => cp(i) = Utf8(new String(r.bytes(r.u2()), "UTF-8")); i += 1
        case 7 => cp(i) = CClass(r.u2()); i += 1
        case 8 => cp(i) = CString(r.u2()); i += 1
        case 9 | 10 | 11 => val tag = bytes(r.pos - 1) & 0xff; cp(i) = CRef(tag, r.u2(), r.u2()); i += 1
        case 12 => cp(i) = CNameAndType(r.u2(), r.u2()); i += 1
        case 3 | 4 => r.skip(4); cp(i) = CNum(3); i += 1
        case 5 | 6 => r.skip(8); cp(i) = CNum(5); i += 1
        case 15 => r.skip(3); cp(i) = CNum(15); i += 1
        case 16 => r.skip(2); cp(i) = CNum(16); i += 1
        case 18 => r.skip(4); cp(i) = CNum(18); i += 1
        case x => throw new RuntimeException("constant pool tag " + x)
      }
    }
    r.u2(); val thisClass = r.u2(); val superClass = r.u2()
    val ifaceCount = r.u2(); r.skip(2 * ifaceCount)
    skipMembers(r, cp, r.u2())
    val methodCount = r.u2()
    val methods = (0 until methodCount).map(_ => readMethod(r, cp)).toVector
    skipAttrs(r, cp, r.u2())
    val name = cp(thisClass).asInstanceOf[CClass].nameIndex
    val superName = if (superClass == 0) "" else cp(cp(superClass).asInstanceOf[CClass].nameIndex).asInstanceOf[Utf8].v
    new ParsedClass(cp(name).asInstanceOf[Utf8].v, superName, major, cp, methods)
  }

  def skipMembers(r: R, cp: Array[Cp], n: Int): Unit = for (_ <- 0 until n) { r.skip(6); skipAttrs(r, cp, r.u2()) }
  def skipAttrs(r: R, cp: Array[Cp], n: Int): Unit = for (_ <- 0 until n) { r.u2(); r.skip(r.u4().toInt) }

  def readMethod(r: R, cp: Array[Cp]): MethodInfo = {
    val flags = r.u2(); val name = utf(cp, r.u2()); val desc = utf(cp, r.u2())
    var code: Option[Code] = None
    val ac = r.u2()
    for (_ <- 0 until ac) {
      val an = utf(cp, r.u2()); val len = r.u4().toInt; val end = r.pos + len
      if (an == "Code") {
        val maxStack = r.u2(); val maxLocals = r.u2(); val clen = r.u4().toInt; val codeBytes = r.bytes(clen)
        val handlers = (0 until r.u2()).map { _ => r.u2(); r.u2(); val h = r.u2(); r.u2(); h }.toVector
        var frames = Map.empty[Int, Frame]
        val nested = r.u2()
        for (_ <- 0 until nested) {
          val n = utf(cp, r.u2()); val l = r.u4().toInt; val b = r.bytes(l)
          if (n == "StackMapTable") frames = parseStackMap(cp, b, initialLocals(flags, name, desc, ""))
        }
        code = Some(Code(maxStack, maxLocals, codeBytes, frames, handlers))
      }
      r.pos = end
    }
    MethodInfo(name, desc, flags, code)
  }

  def utf(cp: Array[Cp], i: Int): String = cp(i).asInstanceOf[Utf8].v

  def parseStackMap(cp: Array[Cp], b: Array[Byte], startLocals: Vector[V]): Map[Int, Frame] = {
    val r = new R(b); val n = r.u2(); var last = -1; var locals = startLocals; var out = Map.empty[Int, Frame]
    for (_ <- 0 until n) {
      val ft = r.u1()
      val (delta, newLocals, stack) = ft match {
        case x if x <= 63 => (x, locals, Vector.empty[V])
        case x if x <= 127 => (x - 64, locals, Vector(readV(cp, r)))
        case 247 => val d = r.u2(); (d, locals, Vector(readV(cp, r)))
        case x if x >= 248 && x <= 250 => val d = r.u2(); (d, locals.dropRight(251 - x), Vector.empty[V])
        case 251 => val d = r.u2(); (d, locals, Vector.empty[V])
        case x if x >= 252 && x <= 254 => val d = r.u2(); val add = (0 until (x - 251)).map(_ => readV(cp, r)).toVector; (d, locals ++ add, Vector.empty[V])
        case 255 => val d = r.u2(); val nl = (0 until r.u2()).map(_ => readV(cp, r)).toVector; val ns = (0 until r.u2()).map(_ => readV(cp, r)).toVector; (d, nl, ns)
      }
      val pc = if (last < 0) delta else last + delta
      locals = newLocals; last = pc; out += pc -> Frame(locals, stack)
    }
    out
  }

  def readV(cp: Array[Cp], r: R): V = r.u1() match {
    case 0 => Top
    case 1 => IntV
    case 5 => NullV
    case 6 => UninitThis
    case 7 => Obj(utf(cp, cp(r.u2()).asInstanceOf[CClass].nameIndex))
    case 8 => Uninit(r.u2(), "")
    case x => throw new RuntimeException("verification type " + x)
  }

  def verifyMethod(cls: ParsedClass, m: MethodInfo): MethodResult = {
    val errors = mutable.ArrayBuffer[String]()
    m.code.foreach { c =>
      val starts = instructionStarts(c.code, errors)
      val branchTargets = collectBranches(c.code, errors)
      for (t <- branchTargets if t < 0 || t >= c.code.length || !starts(t)) errors += "bad control target"
      for (t <- branchTargets if cls.major >= 50 && !c.frames.contains(t) && t != c.code.length) errors += "missing frame"
      for (h <- c.handlers if h < 0 || h >= c.code.length || !starts(h)) errors += "bad handler"
      runDataflow(cls, m, c, starts, errors)
    }
    MethodResult(m.name, m.desc, errors.isEmpty, errors.toVector.distinct)
  }

  def instructionStarts(code: Array[Byte], errors: mutable.ArrayBuffer[String]): Array[Boolean] = {
    val starts = Array.fill(code.length)(false); var pc = 0
    while (pc < code.length) { starts(pc) = true; val l = lengthAt(code, pc, errors); if (l <= 0) return starts else pc += l }
    if (pc != code.length) errors += "truncated instruction"
    starts
  }

  def collectBranches(code: Array[Byte], errors: mutable.ArrayBuffer[String]): Vector[Int] = {
    val out = mutable.ArrayBuffer[Int](); var pc = 0
    while (pc < code.length) {
      val op = code(pc) & 0xff
      if ((op >= 0x99 && op <= 0xa8) || op == 0xc6 || op == 0xc7) out += pc + s2(code, pc + 1)
      else if (op == 0xc8 || op == 0xc9) out += pc + s2(code, pc + 1)
      pc += math.max(1, lengthAt(code, pc, errors))
    }
    out.toVector
  }

  def s2(code: Array[Byte], p: Int): Int = { val v = ((code(p) & 0xff) << 8) | (code(p + 1) & 0xff); if ((v & 0x8000) != 0) v - 65536 else v }
  def s4(code: Array[Byte], p: Int): Int = ((code(p) & 0xff) << 24) | ((code(p + 1) & 0xff) << 16) | ((code(p + 2) & 0xff) << 8) | (code(p + 3) & 0xff)

  def lengthAt(code: Array[Byte], pc: Int, errors: mutable.ArrayBuffer[String]): Int = {
    if (pc >= code.length) return -1
    val op = code(pc) & 0xff
    val len = op match {
      case 0x10 | 0x12 | 0x15 | 0x19 | 0x36 | 0x3a | 0xbc => 2
      case 0x11 | 0x13 | 0x14 | 0x99 | 0x9a | 0x9b | 0x9c | 0x9d | 0x9e | 0x9f | 0xa0 | 0xa1 | 0xa2 | 0xa3 | 0xa4 | 0xa5 | 0xa6 | 0xa7 | 0xa8 | 0xb2 | 0xb4 | 0xb5 | 0xb6 | 0xb7 | 0xb8 | 0xbb | 0xbd | 0xc0 | 0xc1 | 0xc6 | 0xc7 => 3
      case 0xb9 | 0xba | 0xc8 | 0xc9 => 3
      case _ => 1
    }
    if (pc + len > code.length) { errors += "truncated instruction"; -1 } else len
  }

  def runDataflow(cls: ParsedClass, m: MethodInfo, c: Code, starts: Array[Boolean], errors: mutable.ArrayBuffer[String]): Unit = {
    val init = Frame(initialLocals(m.flags, m.name, m.desc, cls.name).padTo(c.maxLocals, Top), Vector.empty)
    val seen = mutable.Map[Int, Frame](); val q = mutable.Queue[(Int, Frame)]((0, init))
    var steps = 0
    while (q.nonEmpty && steps < 10000) {
      steps += 1
      val (pc, in0) = q.dequeue()
      if (pc < c.code.length && starts(pc)) {
        val in = c.frames.getOrElse(pc, in0)
        if (!seen.contains(pc) || seen(pc) != in) {
          seen(pc) = in
          val (nexts, out) = step(cls, m, c, pc, in, errors)
          nexts.foreach { n => if (n >= 0 && n <= c.code.length) q.enqueue((n, c.frames.getOrElse(n, out))) }
        }
      }
    }
    if (steps >= 10000) errors += "unstable flow"
  }

  def initialLocals(flags: Int, name: String, desc: String, owner: String): Vector[V] = {
    val base = if ((flags & 0x0008) == 0) Vector(if (name == "<init>") UninitThis else Obj(owner)) else Vector.empty[V]
    base ++ descriptorArgs(desc)
  }

  def descriptorArgs(desc: String): Vector[V] = {
    val out = mutable.ArrayBuffer[V](); var i = 1
    while (i < desc.length && desc.charAt(i) != ')') desc.charAt(i) match {
      case 'I' | 'Z' | 'B' | 'C' | 'S' => out += IntV; i += 1
      case 'J' | 'D' => out += Top; out += Top; i += 1
      case 'L' => val j = desc.indexOf(';', i); out += Obj(desc.substring(i + 1, j)); i = j + 1
      case '[' => while (desc.charAt(i) == '[') i += 1; if (desc.charAt(i) == 'L') i = desc.indexOf(';', i) + 1 else i += 1; out += Obj("array")
      case _ => i += 1
    }
    out.toVector
  }

  def returnType(desc: String): V = {
    val i = desc.indexOf(')') + 1
    desc.charAt(i) match {
      case 'V' => Top
      case 'I' | 'Z' | 'B' | 'C' | 'S' => IntV
      case 'L' => Obj(desc.substring(i + 1, desc.indexOf(';', i)))
      case '[' => Obj("array")
      case _ => Top
    }
  }

  def step(cls: ParsedClass, m: MethodInfo, c: Code, pc: Int, f: Frame, errors: mutable.ArrayBuffer[String]): (Vector[Int], Frame) = {
    val code = c.code; val op = code(pc) & 0xff; var stack = f.stack; var locals = f.locals
    def pop(): V = { if (stack.isEmpty) { errors += "stack underflow"; Top } else { val v = stack.last; stack = stack.dropRight(1); v } }
    def push(v: V): Unit = stack = stack :+ v
    def load(n: Int): Unit = push(locals.lift(n).getOrElse(Top))
    def store(n: Int): Unit = { val v = pop(); locals = locals.padTo(n + 1, Top).updated(n, v) }
    def next = pc + lengthAt(code, pc, errors)
    op match {
      case 0x00 =>
      case 0x01 => push(NullV)
      case x if x >= 0x02 && x <= 0x08 => push(IntV)
      case 0x10 | 0x11 => push(IntV)
      case 0x15 => load(code(pc + 1) & 0xff)
      case 0x1a | 0x1b | 0x1c | 0x1d => load(op - 0x1a)
      case 0x19 => load(code(pc + 1) & 0xff)
      case 0x2a | 0x2b | 0x2c | 0x2d => load(op - 0x2a)
      case 0x36 => store(code(pc + 1) & 0xff)
      case 0x3b | 0x3c | 0x3d | 0x3e => store(op - 0x3b)
      case 0x3a => store(code(pc + 1) & 0xff)
      case 0x4b | 0x4c | 0x4d | 0x4e => store(op - 0x4b)
      case 0x57 => pop()
      case 0x59 => if (stack.nonEmpty) push(stack.last) else errors += "stack underflow"
      case 0x60 | 0x64 | 0x68 => pop(); pop(); push(IntV)
      case 0x99 | 0x9a | 0xc6 | 0xc7 => pop(); return (Vector(next, pc + s2(code, pc + 1)), Frame(locals, stack))
      case x if x >= 0x9f && x <= 0xa4 => pop(); pop(); return (Vector(next, pc + s2(code, pc + 1)), Frame(locals, stack))
      case 0xa7 => return (Vector(pc + s2(code, pc + 1)), Frame(locals, stack))
      case 0xc8 => return (Vector(pc + s2(code, pc + 1)), Frame(locals, stack))
      case 0xac => val v = pop(); if (v != IntV) errors += "bad return"
      case 0xb0 => val v = pop(); if (!isRef(v) || v.isInstanceOf[Uninit]) errors += "bad return"
      case 0xb1 =>
      case 0xb7 =>
        val (owner, name, desc) = cls.ref(((code(pc + 1) & 0xff) << 8) | (code(pc + 2) & 0xff)); descriptorArgs(desc).reverse.foreach(_ => pop()); val recv = pop()
        if (name == "<init>") recv match { case Uninit(_, _) | UninitThis => ; case _ => errors += "bad init" }
        else if (!isRef(recv)) errors += "bad invoke"
      case 0xb6 | 0xb8 =>
        val (_, _, desc) = cls.ref(((code(pc + 1) & 0xff) << 8) | (code(pc + 2) & 0xff)); descriptorArgs(desc).reverse.foreach(_ => pop()); if (op == 0xb6) pop(); val rt = returnType(desc); if (rt != Top) push(rt)
      case 0xbb => val idx = ((code(pc + 1) & 0xff) << 8) | (code(pc + 2) & 0xff); push(Uninit(pc, cls.cls(idx)))
      case _ =>
    }
    val out = Frame(locals, stack)
    op match { case 0xac | 0xb0 | 0xb1 => (Vector.empty, out); case _ => (Vector(next), out) }
  }

  def isRef(v: V): Boolean = v match { case Obj(_) | NullV | UninitThis | Uninit(_, _) => true; case _ => false }

  def short(s: String): String = if (s == null) "error" else s.take(80)
  def esc(s: String): String = s.flatMap { case '\\' => "\\\\"; case '"' => "\\\""; case c if c < ' ' => " "; case c => c.toString }
  def json(ok: Boolean, classes: Vector[ClassResult]): String = {
    val cs = classes.map { c =>
      val ms = c.methods.map { m => "{\"name\":\""+esc(m.name)+"\",\"descriptor\":\""+esc(m.desc)+"\",\"ok\":"+m.ok+",\"errors\":["+m.errors.map(e => "\""+esc(e)+"\"").mkString(",")+"]}" }.mkString(",")
      "{\"name\":\""+esc(c.name)+"\",\"methods\":["+ms+"]}"
    }.mkString(",")
    "{\"ok\":" + ok + ",\"class_count\":" + classes.size + ",\"classes\":[" + cs + "]}"
  }
}
