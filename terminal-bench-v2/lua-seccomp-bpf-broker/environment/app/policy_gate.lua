local input_dir = arg[1] or "/app/policies"
local output_dir = arg[2] or "/app/audit-out"

local ACTION_RANK = { ALLOW = 1, LOG = 2, TRACE = 3, ERRNO = 4, TRAP = 5, KILL = 6 }
local SYSCALLS = { read = 0, write = 1, open = 2, close = 3, stat = 4, fstat = 5, mmap = 9, mprotect = 10, munmap = 11, brk = 12, rt_sigaction = 13, ioctl = 16, pread64 = 17, access = 21, pipe = 22, sched_yield = 24, dup = 32, nanosleep = 35, socket = 41, connect = 42, accept = 43, sendto = 44, recvfrom = 45, bind = 49, listen = 50, clone = 56, execve = 59, exit = 60, wait4 = 61, kill = 62, uname = 63, fcntl = 72, fsync = 74, getcwd = 79, chdir = 80, mkdir = 83, unlink = 87, chmod = 90, gettimeofday = 96, getrlimit = 97, getuid = 102, getgid = 104, geteuid = 107, getegid = 108, getppid = 110, getpid = 39, arch_prctl = 158, setrlimit = 160, prctl = 157, seccomp = 317, getrandom = 318, memfd_create = 319, openat = 257, newfstatat = 262, unlinkat = 263, renameat = 264, clock_gettime = 228, exit_group = 231, epoll_create1 = 291, accept4 = 288, dup3 = 292 }
local NR_TO_NAME = {}
for k, v in pairs(SYSCALLS) do NR_TO_NAME[v] = k end

local function shell_quote(s) return "'" .. tostring(s):gsub("'", "'\\''") .. "'" end
local function mkdir_p(path) os.execute("mkdir -p " .. shell_quote(path)) end
local function sorted_keys(t) local r = {}; for k in pairs(t) do r[#r+1] = k end; table.sort(r); return r end
local function read_policy(path) local chunk, err = loadfile(path, "t", {}); if not chunk then return nil, err end; local ok, data = pcall(chunk); if not ok then return nil, data end; return data end
local function json_escape(s) return tostring(s):gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('\n', '\\n') end
local function is_array(t) local n = 0; for k in pairs(t) do if type(k) ~= "number" then return false end; if k > n then n = k end end; for i=1,n do if t[i] == nil then return false end end; return true end
local function encode(v)
  if type(v) == "nil" then return "null" end
  if type(v) == "boolean" or type(v) == "number" then return tostring(v) end
  if type(v) == "string" then return '"' .. json_escape(v) .. '"' end
  local out = {}
  if is_array(v) then for i=1,#v do out[#out+1] = encode(v[i]) end; return "[" .. table.concat(out, ",") .. "]" end
  for _, k in ipairs(sorted_keys(v)) do out[#out+1] = encode(k) .. ":" .. encode(v[k]) end
  return "{" .. table.concat(out, ",") .. "}"
end

local function syscall_nr(v) if type(v) == "number" then return v end; return SYSCALLS[v] end
local function syscall_name(v) if type(v) == "string" then return v end; return NR_TO_NAME[v] or tostring(v) end
local function add_finding(policy, severity, code, message) policy.findings[#policy.findings+1] = { severity = severity, code = code, message = message } end

local function pred_ok(rule, args)
  local got = args[(rule.index or 0) + 1] or 0
  if rule.op == "eq" then return got == rule.value end
  if rule.op == "masked_eq" then return (got % (rule.mask + 1)) == rule.value end
  if rule.op == "range" then return got >= (rule.min or 0) or got <= (rule.max or 0) end
  return false
end

local function rule_action(policy, call)
  local best = policy.default_action or "KILL"
  for _, r in ipairs(policy.syscalls or {}) do
    if syscall_nr(r.name) == syscall_nr(call.nr) then
      local ok = true
      for _, p in ipairs(r.args or {}) do if not pred_ok(p, call.args or {}) then ok = false end end
      if ok and ACTION_RANK[r.action or "ALLOW"] < ACTION_RANK[best] then best = r.action or "ALLOW" end
    end
  end
  return best
end

local function run_program(policy, call)
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
end

local function audit_policy(policy)
  policy.findings = {}
  local entry = { id = policy.id or "unknown", status = "ok", stats = { allow = 0, deny = 0, trap = 0 }, decisions = {}, findings = policy.findings }
  for _, call in ipairs(policy.samples or {}) do
    local c = { arch = call.arch, nr = call.nr, args = call.args or {} }
    if type(c.nr) == "string" then c.nr = syscall_nr(c.nr) end
    local action, err = run_program(policy, c)
    if err then add_finding(policy, "critical", "MALFORMED_PROGRAM", "program could not be evaluated"); entry.status = "invalid"; action = policy.default_action or "KILL" end
    local wanted = rule_action(policy, c)
    if action ~= wanted then add_finding(policy, "high", "DECISION_MISMATCH", syscall_name(c.nr) .. " resolved to " .. tostring(action) .. " not " .. tostring(wanted)) end
    if action == "ALLOW" or action == "LOG" then entry.stats.allow = entry.stats.allow + 1 elseif action == "TRAP" then entry.stats.trap = entry.stats.trap + 1 else entry.stats.deny = entry.stats.deny + 1 end
    entry.decisions[#entry.decisions+1] = { arch = tostring(call.arch), syscall = syscall_name(c.nr), action = action or "KILL" }
  end
  return entry
end

local function files(dir)
  local p = io.popen("find " .. shell_quote(dir) .. " -maxdepth 1 -type f -name '*.lua' -printf '%f\\n'")
  local r = {}; for f in p:lines() do r[#r+1] = f end; p:close(); table.sort(r); return r
end

mkdir_p(output_dir)
local report = { policies = {} }
for _, f in ipairs(files(input_dir)) do
  local pol, err = read_policy(input_dir .. "/" .. f)
  if pol then report.policies[#report.policies+1] = audit_policy(pol) else report.policies[#report.policies+1] = { id = f, status = "invalid", stats = { allow = 0, deny = 0, trap = 0 }, decisions = {}, findings = { { severity = "critical", code = "MALFORMED_POLICY", message = tostring(err) } } } end
end
table.sort(report.policies, function(a,b) return a.id < b.id end)
local jf = assert(io.open(output_dir .. "/audit.json", "w")); jf:write(encode(report), "\n"); jf:close()
local ff = assert(io.open(output_dir .. "/findings.txt", "w"))
for _, p in ipairs(report.policies) do table.sort(p.findings, function(a,b) if a.code == b.code then return a.message < b.message end; return a.code < b.code end); for _, x in ipairs(p.findings) do ff:write(p.id, "|", x.severity, "|", x.code, "|", x.message, "\n") end end
ff:close()
