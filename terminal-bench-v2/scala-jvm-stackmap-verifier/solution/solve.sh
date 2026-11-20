#!/bin/bash
set -euo pipefail

cd /app/jvmguard
python3 - <<'PY'
from pathlib import Path

p = Path('src/JvmGuard.scala')
s = p.read_text()
s = s.replace('case 5 | 6 => r.skip(8); cp(i) = CNum(5); i += 1', 'case 5 | 6 => r.skip(8); cp(i) = CNum(5); if (i + 1 < cpCount) cp(i + 1) = EmptyCp; i += 2')
s = s.replace('val pc = if (last < 0) delta else last + delta', 'val pc = if (last < 0) delta else last + delta + 1')
s = s.replace('else if (op == 0xc8 || op == 0xc9) out += pc + s2(code, pc + 1)', 'else if (op == 0xc8 || op == 0xc9) out += pc + s4(code, pc + 1)')
s = s.replace('case 0xb9 | 0xba | 0xc8 | 0xc9 => 3', 'case 0xb9 | 0xba => 5\n      case 0xc8 | 0xc9 => 5')
s = s.replace('case 0xc8 => return (Vector(pc + s2(code, pc + 1)), Frame(locals, stack))', 'case 0xc8 => return (Vector(pc + s4(code, pc + 1)), Frame(locals, stack))')
s = s.replace('case 0xb0 => val v = pop(); if (!isRef(v) || v.isInstanceOf[Uninit]) errors += "bad return"', 'case 0xb0 => val v = pop(); if (!isRef(v) || v.isInstanceOf[Uninit] || v == UninitThis) errors += "bad return"')
s = s.replace('''    val ok = results.forall(_.methods.forall(_.ok))
    if (output.getParent != null) Files.createDirectories(output.getParent)
    Files.write(output, json(ok, results).getBytes("UTF-8"))''', '''    val sortedResults = results.sortBy(_.name)
    val ok = sortedResults.forall(_.methods.forall(_.ok))
    if (output.getParent != null) Files.createDirectories(output.getParent)
    Files.write(output, json(ok, sortedResults).getBytes("UTF-8"))''')
old = '''        if (name == "<init>") recv match { case Uninit(_, _) | UninitThis => ; case _ => errors += "bad init" }
        else if (!isRef(recv)) errors += "bad invoke"'''
new = '''        if (name == "<init>") recv match {
          case u @ Uninit(_, _) => locals = locals.map(v => if (v == u) Obj(owner) else v); stack = stack.map(v => if (v == u) Obj(owner) else v)
          case UninitThis => locals = locals.map(v => if (v == UninitThis) Obj(cls.name) else v); stack = stack.map(v => if (v == UninitThis) Obj(cls.name) else v)
          case _ => errors += "bad init"
        }
        else if (!isRef(recv)) errors += "bad invoke"'''
s = s.replace(old, new)
p.write_text(s)
PY
./build.sh
