return {
  id = "broker-core",
  default_action = "KILL",
  program = {
    { op = "LD_ARCH" },
    { op = "JEQ", k = "AUDIT_ARCH_X86_64", jt = 1, jf = 0 },
    { op = "RET", action = "KILL" },
    { op = "LD_NR" },
    { op = "JSET", mask = 1073741824, jt = 0, jf = 1 },
    { op = "RET", action = "KILL" },
    { op = "JEQ", k = 257, jt = 0, jf = 1 },
    { op = "RET", action = "ALLOW" },
    { op = "JEQ", k = 59, jt = 0, jf = 1 },
    { op = "RET", action = "TRAP" },
    { op = "RET", action = "KILL" }
  },
  syscalls = {
    { name = "openat", action = "ALLOW", args = { { index = 2, op = "masked_eq", mask = 2097155, value = 0 } } },
    { name = "execve", action = "TRAP", args = {} }
  },
  samples = {
    { arch = "AUDIT_ARCH_X86_64", nr = "openat", args = { -100, 0, 0 } },
    { arch = "AUDIT_ARCH_X86_64", nr = 1073742081, args = { -100, 0, 0 } },
    { arch = "AUDIT_ARCH_X86_64", nr = "execve", args = { 0 } },
    { arch = "AUDIT_ARCH_I386", nr = "openat", args = { -100, 0, 0 } }
  }
}
