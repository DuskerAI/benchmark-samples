# FEM diffusion restart contract

The executable is `/app/task_file/femdiff.jl`. It reads a TOML config and writes the requested JSON output and checkpoint file. Required fields are `nodes`, `steps`, `dt`, `diffusion`, `left_bc`, `right_flux`, `source`, `initial_offset`, `initial_slope`, `initial_wave`, `checkpoint_interval`, and `restart`. Optional numeric fields default to zero except `mesh_power`, `theta`, `interface_x`, and `interface_contrast`, which default to `1`, and `ordering`, which defaults to `natural`. Boolean `restart_remesh` and `mass_lump` default to `false`.

## Mesh and initial state

Use `nodes` coordinates `x_j=(j/(nodes-1))^mesh_power` for `j=0..nodes-1`. The initial state is `initial_offset + initial_slope*x + initial_wave*sin(pi*x)`, with the left endpoint set to `left_bc`.

## Assembly and timestepping

Use exact P1 mass `h/6*[2 1;1 2]` on each element and 3-point Gauss-Legendre quadrature for the stiffness, reaction, and load terms; when `mass_lump=true`, replace the assembled mass matrix by its diagonal row-sum lumped form before every theta step. The diffusion coefficient is `diffusion*(1+diffusion_slope*x)*(1+diffusion_time*t)`, multiplied by `interface_contrast` at quadrature points where `x >= interface_x`; `interface_x` is clamped to `[0,1]`. The reaction coefficient is `reaction*(1+reaction_slope*x)*(1+reaction_time*t)`, and the source is `source + source_slope*x + source_quadratic*x^2 + source_wave*sin(2*pi*x) + t*source_time`.

The spatial operator is `L(t)=K(t)+R(t)+right_transfer*e_N*e_N'`. The right-boundary load contribution is `d_right(t)*(right_flux+right_flux_slope*t) + right_transfer*(right_ambient+right_ambient_slope*t)`, where `d_right(t) = diffusion*(1+diffusion_slope*1.0)*(1+diffusion_time*t)` is the diffusion coefficient evaluated at `x=1`. If `probe_transfer` is nonzero, add an interior trace exchange at `probe_x`: for the element containing `probe_x`, with P1 weights `phi`, add `probe_transfer*phi*phi'` to `L(t)` and add `probe_transfer*(probe_ambient+probe_ambient_slope*t)*phi` to the load. Step `k` uses `h_k=dt*(1+dt_growth*(k-1))`, `t_k=dt*(k+dt_growth*k*(k-1)/2)`, and the theta method. In the theta-method update, use `L(t_{k-1})` and all load terms at `t_{k-1}` with weight `(1-theta)`, and `L(t_k)` and all load terms at `t_k` with weight `theta`. Impose `u_0=left_bc+left_bc_slope*t_k` by elimination before solving. `ordering="natural"` and `ordering="rcm"` must solve the same discrete problem and return the same coordinates and solution to roundoff.

## Output JSON

The output JSON contains exactly `time`, `steps`, `nodes`, `values`, `residuals`, and `checksum`. `time` is `t_steps`; `steps` is the configured final step count; `nodes` and `values` have length `nodes`; `residuals` contains only the relative linear residuals from the current invocation and each residual is at most `5e-10`. The checksum is SHA-256 over the comma-joined solution values formatted with `%.17e`.

## Checkpoint records

Checkpoint files are append-only sequences of little-endian records:

| Field | Contents |
|---|---|
| magic | bytes `JFC1` |
| length | `Int32` payload byte count from `version` through `record_hash` inclusive; for `node_count=n`, this is `116 + 16*n` bytes |
| version | `Int32`, exactly `4` |
| step | `Int32` checkpoint step |
| node_count | `Int32` stored node count |
| time | `Float64` checkpoint time |
| nodes | `Float64[node_count]` stored coordinates |
| values | `Float64[node_count]` stored solution |
| problem_hash | SHA-256 of the canonical restart signature for the time grid, coefficients, source, boundary, and probe exchange fields |
| previous_record_hash | 32 bytes, all zero for the first record |
| record_hash | SHA-256 of the payload bytes from `version` through `previous_record_hash`; the hash bytes are the final 32 bytes counted by `length` |

When `checkpoint_interval > 0`, write records at positive interval steps and at the final step if it is not already an interval step. When `checkpoint_interval <= 0`, write a final record.

## Restart compatibility

For `restart=true`, scan only the valid prefix and continue from the newest compatible record. Compatibility requires version `4`, finite strictly increasing nodes on `[0,1]`, finite values whose first entry matches `left_bc+left_bc_slope*time` to roundoff, strictly increasing checkpoint steps, `step <= steps`, roundoff-sized agreement between the record time and `t_step`, an unbroken hash chain, a `problem_hash` matching the current restart signature, and matching current nodes unless `restart_remesh=true`. The restart signature is the newline-joined list of `key=%.17e` for `dt`, `dt_growth`, `diffusion`, `diffusion_slope`, `diffusion_time`, `interface_x`, `interface_contrast`, `reaction`, `reaction_slope`, `reaction_time`, `left_bc`, `left_bc_slope`, `right_flux`, `right_flux_slope`, `right_transfer`, `right_ambient`, `right_ambient_slope`, `source`, `source_slope`, `source_quadratic`, `source_wave`, `source_time`, `theta`, `mass_lump`, `probe_x`, `probe_transfer`, `probe_ambient`, and `probe_ambient_slope` in that order, using default values for absent optional fields and encoding booleans as `1.0` for true and `0.0` for false. With `restart_remesh=true`, interpolate the stored P1 state onto the current nodes before continuing. Truncate the checkpoint at the first malformed, incomplete, invalid, or incompatible record before appending new records. This includes node-count or coordinate mismatch, future or non-increasing step, wrong version, wrong problem hash, nonfinite values, boundary-inconsistent values, broken previous hash or record hash, and wrong time even if later bytes would parse. If the newest compatible record is already the requested final step, write the JSON state without appending a duplicate checkpoint record.
