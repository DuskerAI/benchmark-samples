#!/bin/bash
set -euo pipefail
cd /app
python3 - <<'PY'
from pathlib import Path
path = Path("policy_gate.lua")
src = path.read_text()

def replace_once(old: str, new: str) -> None:
    global src
    if old not in src:
        raise SystemExit(f"expected source fragment not found: {old[:80]!r}")
    src = src.replace(old, new, 1)

# Normalize syscall identity for native/x32 syscall numbers and add a small
# integer bit-and helper so Lua 5.4 builds without bit libraries still work.
replace_once(
    "local NR_TO_NAME = {}\nfor k, v in pairs(SYSCALLS) do NR_TO_NAME[v] = k end\n",
    "local NR_TO_NAME = {}\nfor k, v in pairs(SYSCALLS) do NR_TO_NAME[v] = k end\n"
    "local X32_BIT = 1073741824\n"
    "local TERMINAL = { KILL = true, TRAP = true, ERRNO = true, TRACE = true, LOG = true, ALLOW = true }\n"
    "local function band(a,b) local r, bit = 0, 1; a = math.floor(a or 0); b = math.floor(b or 0); while a > 0 or b > 0 do local aa, bb = a % 2, b % 2; if aa == 1 and bb == 1 then r = r + bit end; a = math.floor(a / 2); b = math.floor(b / 2); bit = bit * 2 end; return r end\n",
)
replace_once(
    'local function syscall_name(v) if type(v) == "string" then return v end; return NR_TO_NAME[v] or tostring(v) end',
    'local function base_nr(v) local n = syscall_nr(v); if not n then return nil end; if n >= X32_BIT then return n - X32_BIT end; return n end\n'
    'local function has_x32(v) local n = syscall_nr(v); return n and n >= X32_BIT end\n'
    'local function syscall_name(v) if type(v) == "string" then return v end; return NR_TO_NAME[base_nr(v) or v] or tostring(v) end',
)

# Apply the policy predicate contract exactly: inclusive ranges and masked_eq.
replace_once(
    '  if rule.op == "masked_eq" then return (got % (rule.mask + 1)) == rule.value end\n'
    '  if rule.op == "range" then return got >= (rule.min or 0) or got <= (rule.max or 0) end',
    '  if rule.op == "masked_eq" then return band(got, rule.mask or 0) == (rule.value or 0) end\n'
    '  if rule.op == "range" then return got >= (rule.min or 0) and got <= (rule.max or 0) end',
)

# Compare native syscall numbers, enforce ABI/x32 kill decisions, and choose
# the strongest matching rule action according to the documented ordering.
replace_once(
    '''local function rule_action(policy, call)
  local best = policy.default_action or "KILL"
  for _, r in ipairs(policy.syscalls or {}) do
    if syscall_nr(r.name) == syscall_nr(call.nr) then
      local ok = true
      for _, p in ipairs(r.args or {}) do if not pred_ok(p, call.args or {}) then ok = false end end
      if ok and ACTION_RANK[r.action or "ALLOW"] < ACTION_RANK[best] then best = r.action or "ALLOW" end
    end
  end
  return best
end''',
    '''local function rule_action(policy, call)
  if call.arch ~= "AUDIT_ARCH_X86_64" or has_x32(call.nr) then return "KILL" end
  local best = nil
  local cnr = base_nr(call.nr)
  for _, r in ipairs(policy.syscalls or {}) do
    if base_nr(r.name) == cnr then
      local ok = true
      for _, p in ipairs(r.args or {}) do if not pred_ok(p, call.args or {}) then ok = false; break end end
      if ok and (not best or ACTION_RANK[r.action or "ALLOW"] > ACTION_RANK[best]) then best = r.action or "ALLOW" end
    end
  end
  return best or policy.default_action or "KILL"
end''',
)

# Validate instructions before executing them and implement BPF jump offsets as
# pc + 1 + offset; falling out of the program is malformed.
replace_once(
    '''local function run_program(policy, call)
  local ins = policy.program or {}
  local pc, a, steps = 1, 0, 0
  while ins[pc] and steps < 4096 do
    steps = steps + 1
    local op = ins[pc].op
    if op == "LD_ARCH" then a = call.arch; pc = pc + 1
    elseif op == "LD_NR" then a = call.nr; pc = pc + 1
    elseif op == "JEQ" then if a == ins[pc].k then pc = pc + (ins[pc].jt or 0) else pc = pc + (ins[pc].jf or 0) end
    elseif op == "JSET" then if (a % ((ins[pc].mask or 0) + 1)) ~= 0 then pc = pc + (ins[pc].jt or 0) else pc = pc + (ins[pc].jf or 0) end
    elseif op == "RET" then return ins[pc].action
    else return nil, "bad" end
  end
  return policy.default_action or "KILL"
end''',
    '''local function valid_ins(x)
  if type(x) ~= "table" then return false end
  if x.op == "LD_ARCH" or x.op == "LD_NR" then return true end
  if x.op == "LD_ARG" then return type(x.index) == "number" and x.index >= 0 end
  if x.op == "JA" then return type(x.k) == "number" end
  if x.op == "JEQ" then return x.k ~= nil and type(x.jt or 0) == "number" and type(x.jf or 0) == "number" end
  if x.op == "JGE" then return type(x.k) == "number" and type(x.jt or 0) == "number" and type(x.jf or 0) == "number" end
  if x.op == "JSET" then return type(x.mask) == "number" and type(x.jt or 0) == "number" and type(x.jf or 0) == "number" end
  if x.op == "RET" then return TERMINAL[x.action] == true end
  return false
end

local function run_program(policy, call)
  local ins = policy.program or {}
  local pc, a, steps = 1, 0, 0
  while ins[pc] do
    steps = steps + 1
    if steps > 4096 or not valid_ins(ins[pc]) then return nil, "bad" end
    local x, op = ins[pc], ins[pc].op
    if op == "LD_ARCH" then a = call.arch; pc = pc + 1
    elseif op == "LD_NR" then a = call.nr; pc = pc + 1
    elseif op == "LD_ARG" then a = (call.args or {})[(x.index or 0) + 1] or 0; pc = pc + 1
    elseif op == "JA" then pc = pc + 1 + x.k
    elseif op == "JEQ" then if a == x.k then pc = pc + 1 + (x.jt or 0) else pc = pc + 1 + (x.jf or 0) end
    elseif op == "JGE" then if a >= x.k then pc = pc + 1 + (x.jt or 0) else pc = pc + 1 + (x.jf or 0) end
    elseif op == "JSET" then if band(a, x.mask) ~= 0 then pc = pc + 1 + (x.jt or 0) else pc = pc + 1 + (x.jf or 0) end
    elseif op == "RET" then return x.action end
    if pc < 1 then return nil, "bad" end
  end
  return nil, "bad"
end''',
)

replace_once(
    'local c = { arch = call.arch, nr = call.nr, args = call.args or {} }\n    if type(c.nr) == "string" then c.nr = syscall_nr(c.nr) end',
    'local c = { arch = call.arch, nr = syscall_nr(call.nr), args = call.args or {} }',
)
replace_once(
    'entry.decisions[#entry.decisions+1] = { arch = tostring(call.arch), syscall = syscall_name(c.nr), action = action or "KILL" }',
    'entry.decisions[#entry.decisions+1] = { arch = tostring(call.arch), syscall = syscall_name(c.nr), action = action }',
)

# Sort findings before writing JSON as well as the line-oriented report.
replace_once(
    'table.sort(report.policies, function(a,b) return a.id < b.id end)\nlocal jf = assert(io.open(output_dir .. "/audit.json", "w")); jf:write(encode(report), "\\n"); jf:close()\nlocal ff = assert(io.open(output_dir .. "/findings.txt", "w"))\nfor _, p in ipairs(report.policies) do table.sort(p.findings, function(a,b) if a.code == b.code then return a.message < b.message end; return a.code < b.code end); for _, x in ipairs(p.findings) do ff:write(p.id, "|", x.severity, "|", x.code, "|", x.message, "\\n") end end',
    'table.sort(report.policies, function(a,b) return a.id < b.id end)\nfor _, p in ipairs(report.policies) do table.sort(p.findings, function(a,b) if a.code == b.code then return a.message < b.message end; return a.code < b.code end) end\nlocal jf = assert(io.open(output_dir .. "/audit.json", "w")); jf:write(encode(report), "\\n"); jf:close()\nlocal ff = assert(io.open(output_dir .. "/findings.txt", "w"))\nfor _, p in ipairs(report.policies) do for _, x in ipairs(p.findings) do ff:write(p.id, "|", x.severity, "|", x.code, "|", x.message, "\\n") end end',
)

path.write_text(src)
PY
