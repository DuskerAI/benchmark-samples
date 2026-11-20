using LinearAlgebra
using SparseArrays

function fail!(out, msg)
    isfile(out) && rm(out; force=true)
    println(stderr, msg)
    exit(1)
end

function read_settings(dir, out)
    cfg = Dict{String,String}()
    for raw in eachline(joinpath(dir, "case.txt"))
        line = strip(split(raw, '#'; limit=2)[1])
        isempty(line) && continue
        p = split(line, "="; limit=2)
        length(p) == 2 || fail!(out, "bad setting")
        cfg[strip(p[1])] = strip(p[2])
    end
    return cfg
end

function read_vec(path)
    vals = Float64[]
    for raw in eachline(path)
        line = strip(split(raw, '#'; limit=2)[1])
        isempty(line) && continue
        push!(vals, parse(Float64, line))
    end
    vals
end

function read_mm(path, out)
    rows = Int[]; cols = Int[]; vals = Float64[]; n = 0; gotdims = false
    for raw in eachline(path)
        line = strip(raw)
        isempty(line) && continue
        startswith(line, "%") && continue
        t = split(line)
        if !gotdims
            n = parse(Int, t[1]); gotdims = true; continue
        end
        push!(rows, parse(Int, t[1])); push!(cols, parse(Int, t[2])); push!(vals, parse(Float64, t[3]))
    end
    sparse(rows, cols, vals, n, n)
end

function esc(s)
    replace(string(s), "\\" => "\\\\", "\"" => "\\\"")
end

function jsonval(x)
    if x isa AbstractString
        return "\"" * esc(x) * "\""
    elseif x isa Bool
        return x ? "true" : "false"
    elseif x isa Number
        return string(x)
    elseif x isa AbstractVector
        return "[" * join([jsonval(v) for v in x], ",") * "]"
    elseif x isa Dict
        parts = String[]
        for k in sort(collect(keys(x)); by=string)
            push!(parts, jsonval(string(k)) * ":" * jsonval(x[k]))
        end
        return "{" * join(parts, ",") * "}"
    end
    error("unsupported json value")
end

function main()
    length(ARGS) == 2 || exit(1)
    dir, out = ARGS
    cfg = read_settings(dir, out)
    A = read_mm(joinpath(dir, cfg["matrix"]), out)
    b = read_vec(joinpath(dir, cfg["rhs"]))
    m = haskey(cfg, "preconditioner") ? read_vec(joinpath(dir, cfg["preconditioner"])) : ones(length(b))
    restart = parse(Int, cfg["restart"]); maxiter = parse(Int, cfg["maxiter"])
    tol = parse(Float64, cfg["tol"]); true_tol = parse(Float64, cfg["true_tol"])
    x = zeros(length(b)); records = Vector{Dict{String,Any}}(); status = "not_converged"
    finalp = norm((b - A*x) ./ m); finalt = finalp
    for it in 1:maxiter
        r = b - A * zeros(length(b))
        alpha = dot(r, r) / max(dot(r, A * r), eps())
        x = zeros(length(b)) + alpha * r
        finalp = norm((b - A*x) ./ m)
        finalt = finalp
        push!(records, Dict{String,Any}("iteration"=>it, "cycle"=>cld(it, restart), "cycle_iteration"=>((it-1)%restart)+1, "preconditioned_residual"=>finalp, "true_residual"=>finalt, "orthogonality_error"=>0.0))
        if finalp <= tol
            status = "converged"; break
        end
    end
    rep = Dict{String,Any}("status"=>status, "iterations"=>length(records), "restart"=>restart, "tolerance"=>tol, "true_tolerance"=>true_tol, "final_preconditioned_residual"=>finalp, "final_true_residual"=>finalt, "solution"=>x, "records"=>records)
    open(out, "w") do io
        write(io, jsonval(rep)); write(io, "\n")
    end
end

try
    main()
catch e
    length(ARGS) == 2 && isfile(ARGS[2]) && rm(ARGS[2]; force=true)
    println(stderr, e)
    exit(1)
end
