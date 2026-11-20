#!/bin/bash
set -euo pipefail
target=/app/task_file/femdiff.jl
test -f "$target"
{
  wc -l "$target"
  sed -n '1,120p' "$target"
} >/tmp/femdiff-existing-inspection.txt
cat > "$target" <<'JULIA'
using LinearAlgebra
using SparseArrays
using TOML
using SHA
using Printf

const MAGIC = UInt8[0x4a, 0x46, 0x43, 0x31]
const VERSION = Int32(4)
const ZERO_HASH = zeros(UInt8, 32)

function getv(cfg, key, default)
    haskey(cfg, key) ? cfg[key] : default
end

function assemble(nodes, diffusion, diffusion_slope, diffusion_time, interface_x, interface_contrast, reaction, reaction_slope, reaction_time, source, source_slope, source_quadratic, source_wave, source_time, right_flux, right_flux_slope, right_transfer, right_ambient, right_ambient_slope, probe_x, probe_transfer, probe_ambient, probe_ambient_slope, mass_lump, t)
    n = length(nodes)
    rows = Int[]
    cols = Int[]
    mvals = Float64[]
    kvals = Float64[]
    rvals = Float64[]
    fbase = zeros(Float64, n)
    for e in 1:(n - 1)
        h = nodes[e + 1] - nodes[e]
        lm = (h / 6.0) .* [2.0 1.0; 1.0 2.0]
        lk = zeros(Float64, 2, 2)
        lr = zeros(Float64, 2, 2)
        lfbase = zeros(Float64, 2)
        for (xi, w) in zip((-sqrt(3.0 / 5.0), 0.0, sqrt(3.0 / 5.0)), (5.0 / 9.0, 8.0 / 9.0, 5.0 / 9.0))
            x = 0.5 * (nodes[e] + nodes[e + 1]) + 0.5 * h * xi
            phi = [(nodes[e + 1] - x) / h, (x - nodes[e]) / h]
            grad = [-1.0 / h, 1.0 / h]
            coeff = diffusion * (1.0 + diffusion_slope * x) * (1.0 + diffusion_time * t)
            if x >= min(max(interface_x, 0.0), 1.0)
                coeff *= interface_contrast
            end
            react_coeff = reaction * (1.0 + reaction_slope * x) * (1.0 + reaction_time * t)
            q = source + source_slope * x + source_quadratic * x^2 + source_wave * sin(2.0 * pi * x) + source_time * t
            lk .+= w * 0.5 * h * coeff .* (grad * grad')
            lr .+= w * 0.5 * h * react_coeff .* (phi * phi')
            lfbase .+= w * 0.5 * h * q .* phi
        end
        ids = (e, e + 1)
        for a in 1:2
            fbase[ids[a]] += lfbase[a]
            for b in 1:2
                push!(rows, ids[a])
                push!(cols, ids[b])
                push!(mvals, lm[a, b])
                push!(kvals, lk[a, b])
                push!(rvals, lr[a, b])
            end
        end
    end
    right_coeff = diffusion * (1.0 + diffusion_slope) * (1.0 + diffusion_time * t)
    fbase[end] += right_coeff * (right_flux + right_flux_slope * t)
    fbase[end] += right_transfer * (right_ambient + right_ambient_slope * t)
    M = sparse(rows, cols, mvals, n, n)
    K = sparse(rows, cols, kvals, n, n)
    R = sparse(rows, cols, rvals, n, n)
    K[end, end] += right_transfer
    if probe_transfer != 0.0
        xp = min(max(probe_x, nodes[1]), nodes[end])
        e = searchsortedlast(nodes, xp)
        if e >= n
            e = n - 1
        elseif e < 1
            e = 1
        end
        h = nodes[e + 1] - nodes[e]
        w2 = (xp - nodes[e]) / h
        phi = [1.0 - w2, w2]
        ids = (e, e + 1)
        ambient = probe_ambient + probe_ambient_slope * t
        for a in 1:2
            fbase[ids[a]] += probe_transfer * ambient * phi[a]
            for b in 1:2
                K[ids[a], ids[b]] += probe_transfer * phi[a] * phi[b]
            end
        end
    end
    if mass_lump
        lumped = vec(sum(M, dims = 2))
        M = spdiagm(0 => lumped)
    end
    return M, K, R, fbase
end

function apply_left(A, b, value)
    B = Matrix(A)
    y = copy(b)
    y .-= B[:, 1] .* value
    B[1, :] .= 0.0
    B[:, 1] .= 0.0
    B[1, 1] = 1.0
    y[1] = value
    return sparse(B), y
end

function ordering_perm(n, name)
    name == "rcm" ? collect(n:-1:1) : collect(1:n)
end

function pcg(A, b; tol = 1e-12)
    n = length(b)
    x = zeros(Float64, n)
    r = b - A * x
    d = diag(A)
    z = r ./ d
    p = copy(z)
    rz = dot(r, z)
    target = tol * max(1.0, norm(b))
    if norm(r) <= target
        return x, norm(r)
    end
    for _ in 1:max(200, 20 * n)
        Ap = A * p
        denom = dot(p, Ap)
        alpha = rz / denom
        x .+= alpha .* p
        r .-= alpha .* Ap
        if norm(r) <= target
            return x, norm(A * x - b)
        end
        z = r ./ d
        rz_new = dot(r, z)
        if rz_new <= eps(Float64)
            return x, norm(A * x - b)
        end
        p .= z .+ (rz_new / rz) .* p
        rz = rz_new
    end
    return x, norm(A * x - b)
end

function solve_step(A, b, ordering)
    p = ordering_perm(length(b), ordering)
    Ap = A[p, p]
    bp = b[p]
    xp = Ap \ bp
    x = similar(xp)
    x[p] = xp
    return x, norm(A * x - b) / max(1.0, norm(b))
end

function initial_state(nodes, cfg)
    offset = Float64(getv(cfg, "initial_offset", 0.0))
    slope = Float64(getv(cfg, "initial_slope", 0.0))
    wave = Float64(getv(cfg, "initial_wave", 0.0))
    left = Float64(cfg["left_bc"])
    u = [offset + slope * x + wave * sin(pi * x) for x in nodes]
    u[1] = left
    return u
end

function write_i32(io, x)
    write(io, Int32(x))
end

function write_f64(io, x)
    write(io, Float64(x))
end

const SIGNATURE_KEYS = [
    "dt", "dt_growth", "diffusion", "diffusion_slope", "diffusion_time", "interface_x", "interface_contrast",
    "reaction", "reaction_slope", "reaction_time", "left_bc", "left_bc_slope",
    "right_flux", "right_flux_slope", "right_transfer", "right_ambient", "right_ambient_slope",
    "source", "source_slope", "source_quadratic", "source_wave", "source_time",
    "theta", "mass_lump", "probe_x", "probe_transfer", "probe_ambient", "probe_ambient_slope",
]

function signature_default(key)
    (key == "theta" || key == "interface_x" || key == "interface_contrast") ? 1.0 : 0.0
end

function restart_signature_hash(cfg)
    parts = String[]
    for key in SIGNATURE_KEYS
        raw = getv(cfg, key, signature_default(key))
        value = raw isa Bool ? (raw ? 1.0 : 0.0) : Float64(raw)
        push!(parts, string(key, "=", @sprintf("%.17e", value)))
    end
    return sha256(join(parts, "\n"))
end

function record_payload(step, t, nodes, u, problem_hash, previous_hash)
    io = IOBuffer()
    write_i32(io, VERSION)
    write_i32(io, step)
    write_i32(io, length(u))
    write_f64(io, t)
    for x in nodes
        write_f64(io, x)
    end
    for v in u
        write_f64(io, v)
    end
    write(io, problem_hash)
    write(io, previous_hash)
    core = take!(io)
    return vcat(core, sha256(core))
end

function append_checkpoint(path, step, t, nodes, u, problem_hash, previous_hash)
    payload = record_payload(step, t, nodes, u, problem_hash, previous_hash)
    open(path, "a") do io
        write(io, MAGIC)
        write_i32(io, length(payload))
        write(io, payload)
    end
    return payload[(end - 31):end]
end

function time_at_step(step, dt, dt_growth)
    return dt * (step + dt_growth * step * (step - 1) / 2.0)
end

function same_nodes(a, b)
    length(a) == length(b) && all(abs.(a .- b) .<= 1e-14)
end

function p1_interpolate(old_nodes, old_values, new_nodes)
    out = similar(new_nodes)
    j = 1
    for (i, x) in enumerate(new_nodes)
        while j < length(old_nodes) - 1 && old_nodes[j + 1] < x
            j += 1
        end
        if x <= old_nodes[1]
            out[i] = old_values[1]
        elseif x >= old_nodes[end]
            out[i] = old_values[end]
        else
            x0 = old_nodes[j]
            x1 = old_nodes[j + 1]
            w = (x - x0) / (x1 - x0)
            out[i] = (1.0 - w) * old_values[j] + w * old_values[j + 1]
        end
    end
    return out
end

function parse_payload(payload, current_nodes, maxstep, dt, dt_growth, expected_previous_hash, previous_step, allow_remesh, left_base, left_slope, current_problem_hash)
    if length(payload) < 148
        return nothing
    end
    core = payload[1:(end - 32)]
    digest = payload[(end - 31):end]
    if sha256(core) != digest
        return nothing
    end
    rec = IOBuffer(core)
    version = read(rec, Int32)
    step = read(rec, Int32)
    rn = read(rec, Int32)
    t = read(rec, Float64)
    if version != VERSION || step <= previous_step || step < 0 || step > maxstep || rn < 2 || length(core) != 84 + 16 * rn
        return nothing
    end
    expected_time = time_at_step(Int(step), dt, dt_growth)
    time_tol = 64.0 * eps(Float64) * max(1.0, abs(expected_time))
    if !isfinite(t) || abs(t - expected_time) > time_tol
        return nothing
    end
    stored_nodes = [read(rec, Float64) for _ in 1:rn]
    if any(!isfinite, stored_nodes) || stored_nodes[1] != 0.0 || stored_nodes[end] != 1.0 || any(diff(stored_nodes) .<= 0.0)
        return nothing
    end
    vals = [read(rec, Float64) for _ in 1:rn]
    if any(!isfinite, vals)
        return nothing
    end
    boundary = left_base + left_slope * t
    boundary_tol = 64.0 * eps(Float64) * max(1.0, abs(boundary))
    if abs(vals[1] - boundary) > boundary_tol
        return nothing
    end
    problem_hash = read(rec, 32)
    if problem_hash != current_problem_hash
        return nothing
    end
    previous_hash = read(rec, 32)
    if previous_hash != expected_previous_hash
        return nothing
    end
    if !same_nodes(stored_nodes, current_nodes)
        if !allow_remesh
            return nothing
        end
        vals = p1_interpolate(stored_nodes, vals, current_nodes)
    end
    return (step = Int(step), time = t, values = vals, digest = digest)
end

function read_checkpoint(path, current_nodes, maxstep, dt, dt_growth, allow_remesh, left_base, left_slope, current_problem_hash)
    if !isfile(path)
        return nothing
    end
    last = nothing
    valid_end = 0
    expected_previous_hash = copy(ZERO_HASH)
    previous_step = -1
    open(path, "r") do io
        while true
            start = position(io)
            magic = read(io, 4)
            if length(magic) < 4 || magic != MAGIC
                break
            end
            lenbytes = read(io, 4)
            if length(lenbytes) < 4
                break
            end
            len = Int(read(IOBuffer(lenbytes), Int32))
            if len < 148 || len > 1024 * 1024
                break
            end
            payload = read(io, len)
            if length(payload) < len
                break
            end
            parsed = try
                parse_payload(payload, current_nodes, maxstep, dt, dt_growth, expected_previous_hash, previous_step, allow_remesh, left_base, left_slope, current_problem_hash)
            catch
                nothing
            end
            if parsed === nothing
                break
            end
            expected_previous_hash = parsed.digest
            previous_step = parsed.step
            last = (step = parsed.step, time = parsed.time, values = parsed.values, digest = parsed.digest, endpos = position(io))
            valid_end = position(io)
            if position(io) <= start
                break
            end
        end
    end
    return (record = last, valid_end = valid_end, next_previous_hash = expected_previous_hash)
end

function checksum(values)
    text = join((@sprintf("%.17e", v) for v in values), ",")
    return bytes2hex(sha256(text))
end

function write_json(path, t, steps, nodes, values, residuals)
    open(path, "w") do io
        println(io, "{")
        println(io, "  \"time\": ", @sprintf("%.17e", t), ",")
        println(io, "  \"steps\": ", steps, ",")
        println(io, "  \"nodes\": [", join((@sprintf("%.17e", x) for x in nodes), ", "), "],")
        println(io, "  \"values\": [", join((@sprintf("%.17e", x) for x in values), ", "), "],")
        println(io, "  \"residuals\": [", join((@sprintf("%.17e", x) for x in residuals), ", "), "],")
        println(io, "  \"checksum\": \"", checksum(values), "\"")
        println(io, "}")
    end
end

function run(config_path, output_path, checkpoint_path)
    cfg = TOML.parsefile(config_path)
    n = Int(cfg["nodes"])
    steps = Int(cfg["steps"])
    dt = Float64(cfg["dt"])
    dt_growth = Float64(getv(cfg, "dt_growth", 0.0))
    theta = Float64(getv(cfg, "theta", 1.0))
    diffusion = Float64(cfg["diffusion"])
    diffusion_slope = Float64(getv(cfg, "diffusion_slope", 0.0))
    diffusion_time = Float64(getv(cfg, "diffusion_time", 0.0))
    interface_x = Float64(getv(cfg, "interface_x", 1.0))
    interface_contrast = Float64(getv(cfg, "interface_contrast", 1.0))
    left = Float64(cfg["left_bc"])
    left_slope = Float64(getv(cfg, "left_bc_slope", 0.0))
    right_flux = Float64(cfg["right_flux"])
    right_flux_slope = Float64(getv(cfg, "right_flux_slope", 0.0))
    right_transfer = Float64(getv(cfg, "right_transfer", 0.0))
    right_ambient = Float64(getv(cfg, "right_ambient", 0.0))
    right_ambient_slope = Float64(getv(cfg, "right_ambient_slope", 0.0))
    probe_x = Float64(getv(cfg, "probe_x", 0.0))
    probe_transfer = Float64(getv(cfg, "probe_transfer", 0.0))
    probe_ambient = Float64(getv(cfg, "probe_ambient", 0.0))
    probe_ambient_slope = Float64(getv(cfg, "probe_ambient_slope", 0.0))
    source = Float64(cfg["source"])
    source_slope = Float64(getv(cfg, "source_slope", 0.0))
    source_quadratic = Float64(getv(cfg, "source_quadratic", 0.0))
    source_wave = Float64(getv(cfg, "source_wave", 0.0))
    source_time = Float64(getv(cfg, "source_time", 0.0))
    reaction = Float64(getv(cfg, "reaction", 0.0))
    reaction_slope = Float64(getv(cfg, "reaction_slope", 0.0))
    reaction_time = Float64(getv(cfg, "reaction_time", 0.0))
    interval = Int(cfg["checkpoint_interval"])
    restart = Bool(cfg["restart"])
    restart_remesh = Bool(getv(cfg, "restart_remesh", false))
    mass_lump = Bool(getv(cfg, "mass_lump", false))
    ordering = String(getv(cfg, "ordering", "natural"))
    mesh_power = Float64(getv(cfg, "mesh_power", 1.0))
    nodes = [((i - 1) / (n - 1)) ^ mesh_power for i in 1:n]
    problem_hash = restart_signature_hash(cfg)
    u = initial_state(nodes, cfg)
    start_step = 0
    previous_hash = copy(ZERO_HASH)
    if restart
        scan = read_checkpoint(checkpoint_path, nodes, steps, dt, dt_growth, restart_remesh, left, left_slope, problem_hash)
        if scan !== nothing
            if scan.record !== nothing
                start_step = scan.record.step
                u = scan.record.values
            end
            previous_hash = scan.next_previous_hash
            open(checkpoint_path, "r+") do io
                truncate(io, scan.valid_end)
            end
        end
    end
    residuals = Float64[]
    for step in (start_step + 1):steps
        h = dt * (1.0 + dt_growth * (step - 1))
        tprev = time_at_step(step - 1, dt, dt_growth)
        tnext = time_at_step(step, dt, dt_growth)
        Mprev, Kprev, Rprev, fprev = assemble(nodes, diffusion, diffusion_slope, diffusion_time, interface_x, interface_contrast, reaction, reaction_slope, reaction_time, source, source_slope, source_quadratic, source_wave, source_time, right_flux, right_flux_slope, right_transfer, right_ambient, right_ambient_slope, probe_x, probe_transfer, probe_ambient, probe_ambient_slope, mass_lump, tprev)
        Mnext, Knext, Rnext, fnext = assemble(nodes, diffusion, diffusion_slope, diffusion_time, interface_x, interface_contrast, reaction, reaction_slope, reaction_time, source, source_slope, source_quadratic, source_wave, source_time, right_flux, right_flux_slope, right_transfer, right_ambient, right_ambient_slope, probe_x, probe_transfer, probe_ambient, probe_ambient_slope, mass_lump, tnext)
        Lprev = Kprev + Rprev
        Lnext = Knext + Rnext
        A = Mnext + theta * h * Lnext
        b = (Mprev - (1.0 - theta) * h * Lprev) * u
        b .+= h .* ((1.0 - theta) .* fprev + theta .* fnext)
        B, y = apply_left(A, b, left + left_slope * tnext)
        u, res = solve_step(B, y, ordering)
        push!(residuals, res)
        if interval > 0 && step % interval == 0
            previous_hash = append_checkpoint(checkpoint_path, step, tnext, nodes, u, problem_hash, previous_hash)
        end
    end
    if start_step < steps && (interval <= 0 || steps % interval != 0)
        previous_hash = append_checkpoint(checkpoint_path, steps, time_at_step(steps, dt, dt_growth), nodes, u, problem_hash, previous_hash)
    end
    write_json(output_path, time_at_step(steps, dt, dt_growth), steps, nodes, u, residuals)
end

if abspath(PROGRAM_FILE) == @__FILE__
    if length(ARGS) != 3
        error("usage: femdiff.jl <config.toml> <output.json> <checkpoint.bin>")
    end
    run(ARGS[1], ARGS[2], ARGS[3])
end
JULIA
