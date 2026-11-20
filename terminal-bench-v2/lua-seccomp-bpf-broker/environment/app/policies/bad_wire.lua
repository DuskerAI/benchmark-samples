return {
  id = "bad-wire",
  default_action = "KILL",
  program = {
    { op = "LD_ARCH" },
    { op = "JA", k = 99 },
    { op = "RET", action = "ALLOW" }
  },
  syscalls = {},
  samples = {
    { arch = "AUDIT_ARCH_X86_64", nr = "close", args = { 3 } }
  }
}
