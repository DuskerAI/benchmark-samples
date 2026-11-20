using LinearAlgebra
using SparseArrays
using TOML
using SHA
using Printf

const MAGIC = UInt8[0x4a, 0x46, 0x43, 0x31]
const VERSION = Int32(1)

function getv(cfg, key, default)
    haskey(cfg, key) ? cfg[key] : default
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

function record_payload(step, t, u)
    io = IOBuffer()
    write_i32(io, VERSION)
    write_i32(io, step)
    write_i32(io, length(u))
    write_f64(io, t)
    for v in u
        write_f64(io, v)
    end
    return take!(io)
end

function append_checkpoint(path, step, t, u)
    open(path, "a") do io
        payload = record_payload(step, t, u)
        write(io, MAGIC)
        write_i32(io, length(payload))
        write(io, payload)
    end
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
    interval = Int(cfg["checkpoint_interval"])
    mesh_power = Float64(getv(cfg, "mesh_power", 1.0))
    nodes = [((i - 1) / (n - 1)) ^ mesh_power for i in 1:n]
    u = initial_state(nodes, cfg)
    residuals = Float64[]
    for step in 1:steps
        u .= u .+ dt .* (Float64(getv(cfg, "source", 0.0)) .+ Float64(getv(cfg, "source_slope", 0.0)) .* nodes)
        u[1] = Float64(cfg["left_bc"])
        push!(residuals, norm(u) / max(1.0, n))
        if interval > 0 && step % interval == 0
            append_checkpoint(checkpoint_path, step, step * dt, u)
        end
    end
    if interval <= 0 || steps % interval != 0
        append_checkpoint(checkpoint_path, steps, steps * dt, u)
    end
    write_json(output_path, steps * dt, steps, nodes, u, residuals)
end

if abspath(PROGRAM_FILE) == @__FILE__
    if length(ARGS) != 3
        error("usage: femdiff.jl <config.toml> <output.json> <checkpoint.bin>")
    end
    run(ARGS[1], ARGS[2], ARGS[3])
end
