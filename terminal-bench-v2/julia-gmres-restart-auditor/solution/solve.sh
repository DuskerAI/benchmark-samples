#!/bin/bash
set -euo pipefail

cat > /app/gmresaudit/audit.jl <<'JULIA'
using LinearAlgebra
using SparseArrays

function fail!(out, msg)
    isfile(out) && rm(out; force=true)
    println(stderr, msg)
    exit(1)
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
        return isfinite(float(x)) ? string(x) : error("nonfinite json")
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

function read_case(path, out)
    cfg = Dict{String,String}()
    cfgpath = joinpath(path, "case.txt")
    isfile(cfgpath) || fail!(out, "missing case.txt")
    for raw in eachline(cfgpath)
        line = strip(split(raw, '#'; limit=2)[1])
        isempty(line) && continue
        parts = split(line, "="; limit=2)
        length(parts) == 2 || fail!(out, "bad case setting")
        cfg[strip(parts[1])] = strip(parts[2])
    end
    required = ["matrix", "rhs", "restart", "maxiter", "tol", "true_tol"]
    all(k -> haskey(cfg, k), required) || fail!(out, "missing case setting")
    restart = parse(Int, cfg["restart"]); maxiter = parse(Int, cfg["maxiter"])
    tol = parse(Float64, cfg["tol"]); true_tol = parse(Float64, cfg["true_tol"])
    (restart > 0 && maxiter >= 0 && isfinite(tol) && isfinite(true_tol) && tol > 0 && true_tol > 0) || fail!(out, "invalid numeric setting")
    return cfg, restart, maxiter, tol, true_tol
end

function read_vector(file, n, out)
    vals = Float64[]
    isfile(file) || fail!(out, "missing vector")
    for raw in eachline(file)
        line = strip(split(raw, '#'; limit=2)[1])
        isempty(line) && continue
        v = parse(Float64, line)
        isfinite(v) || fail!(out, "nonfinite vector")
        push!(vals, v)
    end
    length(vals) == n || fail!(out, "vector length mismatch")
    return vals
end

function read_mm(file, out)
    isfile(file) || fail!(out, "missing matrix")
    header_seen = false; symmetric = false; dims = nothing
    rows = Int[]; cols = Int[]; vals = Float64[]
    for raw in eachline(file)
        line = strip(raw)
        isempty(line) && continue
        if startswith(line, "%")
            if !header_seen && startswith(lowercase(line), "%%matrixmarket")
                toks = split(lowercase(line))
                length(toks) >= 5 || fail!(out, "bad matrix header")
                (toks[2] == "matrix" && toks[3] == "coordinate" && toks[4] == "real") || fail!(out, "unsupported matrix header")
                toks[5] in ("general", "symmetric") || fail!(out, "unsupported matrix symmetry")
                symmetric = toks[5] == "symmetric"
                header_seen = true
            end
            continue
        end
        header_seen || fail!(out, "missing matrix header")
        toks = split(line)
        if dims === nothing
            length(toks) == 3 || fail!(out, "bad matrix size")
            dims = (parse(Int, toks[1]), parse(Int, toks[2]), parse(Int, toks[3]))
            (dims[1] == dims[2] && dims[1] > 0 && dims[3] >= 0) || fail!(out, "invalid matrix size")
            continue
        end
        length(toks) == 3 || fail!(out, "bad matrix entry")
        i = parse(Int, toks[1]); j = parse(Int, toks[2]); v = parse(Float64, toks[3])
        (1 <= i <= dims[1] && 1 <= j <= dims[2] && isfinite(v)) || fail!(out, "invalid matrix entry")
        push!(rows, i); push!(cols, j); push!(vals, v)
        if symmetric && i != j
            push!(rows, j); push!(cols, i); push!(vals, v)
        end
    end
    dims === nothing && fail!(out, "missing matrix size")
    return sparse(rows, cols, vals, dims[1], dims[2])
end

function arnoldi(A, Mdiag, r, restart)
    beta = norm(r ./ Mdiag)
    n = length(r); V = zeros(Float64, n, restart + 1); H = zeros(Float64, restart + 1, restart)
    beta == 0 && return V, H, beta, 0
    V[:, 1] = (r ./ Mdiag) / beta
    used = 0
    for j in 1:restart
        w = (A * V[:, j]) ./ Mdiag
        for i in 1:j
            H[i, j] = dot(V[:, i], w)
            w .-= H[i, j] .* V[:, i]
        end
        for i in 1:j
            hcorr = dot(V[:, i], w)
            H[i, j] += hcorr
            w .-= hcorr .* V[:, i]
        end
        H[j + 1, j] = norm(w)
        used = j
        if H[j + 1, j] <= 1e-13
            break
        end
        V[:, j + 1] = w / H[j + 1, j]
    end
    return V, H, beta, used
end

function run_gmres(A, b, Mdiag, restart, maxiter, tol, true_tol)
    n = length(b); x = zeros(Float64, n); records = Vector{Dict{String,Any}}(); converged = false
    total = 0; true_res = norm(b - A * x); prec_res = norm((b - A * x) ./ Mdiag)
    while total < maxiter
        r = b - A * x
        V, H, beta, used = arnoldi(A, Mdiag, r, min(restart, maxiter - total))
        if used == 0
            converged = prec_res <= tol && true_res <= true_tol
            break
        end
        accepted = x
        for k in 1:used
            e = zeros(Float64, k + 1); e[1] = beta
            y = H[1:k+1, 1:k] \ e
            xt = x + V[:, 1:k] * y
            total += 1
            rt = b - A * xt
            pr = norm(rt ./ Mdiag); tr = norm(rt)
            orth = k <= 1 ? 0.0 : maximum(abs.(V[:, 1:k]' * V[:, 1:k] - Matrix{Float64}(I, k, k)))
            push!(records, Dict{String,Any}("iteration"=>total, "cycle"=>cld(total, restart), "cycle_iteration"=>k, "preconditioned_residual"=>pr, "true_residual"=>tr, "orthogonality_error"=>orth))
            accepted = xt; prec_res = pr; true_res = tr
            if pr <= tol && tr <= true_tol
                return xt, records, true, pr, tr
            end
            total >= maxiter && return xt, records, false, pr, tr
        end
        x = accepted
    end
    return x, records, converged, prec_res, true_res
end

function main()
    length(ARGS) == 2 || (println(stderr, "usage: audit.jl <case-dir> <report.json>"); exit(1))
    casedir, out = ARGS
    cfg, restart, maxiter, tol, true_tol = read_case(casedir, out)
    A = read_mm(joinpath(casedir, cfg["matrix"]), out)
    b = read_vector(joinpath(casedir, cfg["rhs"]), size(A, 1), out)
    Mdiag = haskey(cfg, "preconditioner") ? read_vector(joinpath(casedir, cfg["preconditioner"]), size(A,1), out) : ones(Float64, size(A,1))
    any(x -> abs(x) <= 0.0, Mdiag) && fail!(out, "zero preconditioner")
    x, rec, conv, pres, tres = run_gmres(A, b, Mdiag, restart, maxiter, tol, true_tol)
    report = Dict{String,Any}("status" => (conv ? "converged" : "not_converged"), "iterations" => length(rec), "restart" => restart, "tolerance" => tol, "true_tolerance" => true_tol, "final_preconditioned_residual" => pres, "final_true_residual" => tres, "solution" => x, "records" => rec)
    open(out, "w") do io
        write(io, jsonval(report)); write(io, '\n')
    end
end

try
    main()
catch e
    length(ARGS) == 2 && isfile(ARGS[2]) && rm(ARGS[2]; force=true)
    println(stderr, e)
    exit(1)
end
JULIA
