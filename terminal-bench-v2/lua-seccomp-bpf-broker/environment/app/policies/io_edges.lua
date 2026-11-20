return {
  id = "io-edges",
  default_action = "ERRNO",
  program = {
    { op = "LD_ARCH" },
    { op = "JEQ", k = "AUDIT_ARCH_X86_64", jt = 1, jf = 0 },
    { op = "RET", action = "KILL" },
    { op = "LD_NR" },
    { op = "JEQ", k = 1, jt = 0, jf = 1 },
    { op = "RET", action = "ALLOW" },
    { op = "JEQ", k = 44, jt = 0, jf = 1 },
    { op = "RET", action = "TRACE" },
    { op = "RET", action = "ERRNO" }
  },
  syscalls = {
    { name = "write", action = "ALLOW", args = { { index = 0, op = "range", min = 1, max = 2 }, { index = 2, op = "range", min = 0, max = 4096 } } },
    { name = "sendto", action = "TRACE", args = { { index = 5, op = "eq", value = 0 } } }
  },
  samples = {
    { arch = "AUDIT_ARCH_X86_64", nr = "write", args = { 1, 8192, 128 } },
    { arch = "AUDIT_ARCH_X86_64", nr = "write", args = { 7, 8192, 128 } },
    { arch = "AUDIT_ARCH_X86_64", nr = "sendto", args = { 3, 8192, 32, 0, 0, 0 } }
  }
}
