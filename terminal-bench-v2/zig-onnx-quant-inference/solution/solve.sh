#!/bin/bash
set -euo pipefail

cd /app/qinfer
python3 - <<'PY'
from pathlib import Path

path = Path('src/main.zig')
src = path.read_text()


def function_bounds(source: str, name: str, prefix: str = 'fn'):
    marker = f'{prefix} {name}('
    start = source.find(marker)
    if start < 0:
        raise SystemExit(f'could not locate {marker}')
    brace = source.find('{', start)
    if brace < 0:
        raise SystemExit(f'could not locate body for {name}')
    depth = 0
    end = None
    for i in range(brace, len(source)):
        ch = source[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise SystemExit(f'could not locate end of {name}')
    return start, end


def replace_function(source: str, name: str, replacement: str, prefix: str = 'fn') -> str:
    start, end = function_bounds(source, name, prefix)
    return source[:start] + replacement.rstrip() + source[end:]


def remove_function_if_present(source: str, name: str, prefix: str = 'fn') -> str:
    marker = f'{prefix} {name}('
    if marker not in source:
        return source
    start, end = function_bounds(source, name, prefix)
    return source[:start] + source[end:].lstrip('\n')


def replace_checked(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count == 0 and source.count(new) == 1:
        return source
    if count != 1:
        raise SystemExit(f'{label}: expected one match, found {count}')
    return source.replace(old, new)


src = replace_function(src, 'scalarOrFirst', r'''
fn scalarOrFirst(o: std.json.ObjectMap, key: []const u8, allocator: std.mem.Allocator, n: usize) ![]f64 {
    const v = try get(o, key);
    const out = try allocator.alloc(f64, n);
    switch (v) {
        .array => |a| {
            if (a.items.len == 1) {
                const x = try num(a.items[0]);
                for (out) |*slot| slot.* = x;
            } else if (a.items.len == n) {
                for (a.items, 0..) |item, i| out[i] = try num(item);
            } else return error.InvalidInput;
        },
        else => {
            const x = try num(v);
            for (out) |*slot| slot.* = x;
        },
    }
    return out;
}
''')

src = replace_function(src, 'opPad', r'''
fn opPad(layer: std.json.ObjectMap, map: *std.StringHashMap(Tensor), allocator: std.mem.Allocator) !void {
    const input = try getTensor(map, try getStr(layer, "input"));
    if (input.shape.len != 4) return error.InvalidInput;
    const pads = try parseUsizeArray(try arr(try get(layer, "pads")), allocator);
    if (pads.len != 8 and pads.len != 4) return error.InvalidInput;
    const out_shape = try allocator.alloc(usize, 4);
    var before = [_]usize{ 0, 0, 0, 0 };
    var after = [_]usize{ 0, 0, 0, 0 };
    if (pads.len == 8) {
        for (0..4) |i| { before[i] = pads[i]; after[i] = pads[i + 4]; }
    } else {
        before[2] = pads[0]; before[3] = pads[1]; after[2] = pads[2]; after[3] = pads[3];
    }
    for (0..4) |i| out_shape[i] = input.shape[i] + before[i] + after[i];
    const out_data = try allocator.alloc(f64, shapeCount(out_shape));
    const fill = if (layer.get("value")) |vv| try num(vv) else 0.0;
    for (out_data) |*p| p.* = fill;
    const N = input.shape[0]; const C = input.shape[1]; const H = input.shape[2]; const W = input.shape[3];
    const OH = out_shape[2]; const OW = out_shape[3];
    for (0..N) |n| for (0..C) |c| for (0..H) |h| for (0..W) |w| {
        const si = (((n * C + c) * H + h) * W + w);
        const di = (((n * C + c) * OH + h + before[2]) * OW + w + before[3]);
        out_data[di] = input.data[si];
    };
    try map.put(try getStr(layer, "output"), Tensor{ .shape = out_shape, .data = out_data });
}
''')

src = replace_function(src, 'opQConv', r'''
fn opQConv(layer: std.json.ObjectMap, map: *std.StringHashMap(Tensor), allocator: std.mem.Allocator) !void {
    const x = try getTensor(map, try getStr(layer, "input"));
    const w = try getTensor(map, try getStr(layer, "weight"));
    if (x.shape.len != 4 or w.shape.len != 4) return error.InvalidInput;
    const N = x.shape[0]; const C = x.shape[1]; const H = x.shape[2]; const W = x.shape[3];
    const M = w.shape[0]; const KC = w.shape[1]; const KH = w.shape[2]; const KW = w.shape[3];
    if (C != KC) return error.InvalidInput;
    const strides = try parseUsizeArray(try arr(try get(layer, "strides")), allocator);
    const dilations = try parseUsizeArray(try arr(try get(layer, "dilations")), allocator);
    const pads = try parseUsizeArray(try arr(try get(layer, "pads")), allocator);
    if (strides.len != 2 or dilations.len != 2 or pads.len != 4) return error.InvalidInput;
    const eff_h = dilations[0] * (KH - 1) + 1;
    const eff_w = dilations[1] * (KW - 1) + 1;
    if (H + pads[0] + pads[2] < eff_h or W + pads[1] + pads[3] < eff_w) return error.InvalidInput;
    const out_h = (H + pads[0] + pads[2] - eff_h) / strides[0] + 1;
    const out_w = (W + pads[1] + pads[3] - eff_w) / strides[1] + 1;
    const shape = try allocator.alloc(usize, 4);
    shape[0] = N; shape[1] = M; shape[2] = out_h; shape[3] = out_w;
    const data = try allocator.alloc(f64, shapeCount(shape));
    const xs = try getNum(layer, "x_scale");
    const xz = try getNum(layer, "x_zero");
    const ys = try getNum(layer, "y_scale");
    const yz = try getNum(layer, "y_zero");
    const ws = try scalarOrFirst(layer, "w_scale", allocator, M);
    const wz = try scalarOrFirst(layer, "w_zero", allocator, M);
    const bias_name = if (layer.get("bias")) |bv| try str(bv) else "";
    const has_bias = bias_name.len != 0;
    const bias = if (has_bias) try getTensor(map, bias_name) else Tensor{ .shape = &[_]usize{}, .data = &[_]f64{} };
    for (0..N) |n| for (0..M) |m| for (0..out_h) |oh| for (0..out_w) |ow| {
        var acc: f64 = if (has_bias) bias.data[m] else 0.0;
        for (0..C) |c| for (0..KH) |kh| for (0..KW) |kw| {
            const ih0: isize = @as(isize, @intCast(oh * strides[0] + kh * dilations[0])) - @as(isize, @intCast(pads[0]));
            const iw0: isize = @as(isize, @intCast(ow * strides[1] + kw * dilations[1])) - @as(isize, @intCast(pads[1]));
            var xv: f64 = xz;
            if (ih0 >= 0 and iw0 >= 0 and ih0 < @as(isize, @intCast(H)) and iw0 < @as(isize, @intCast(W))) {
                const ih: usize = @intCast(ih0);
                const iw: usize = @intCast(iw0);
                xv = x.data[(((n * C + c) * H + ih) * W + iw)];
            }
            const wv = w.data[(((m * C + c) * KH + kh) * KW + kw)];
            acc += (xv - xz) * xs * (wv - wz[m]) * ws[m];
        };
        var q = @round(acc / ys) + yz;
        if (q < 0) q = 0;
        if (q > 255) q = 255;
        data[(((n * M + m) * out_h + oh) * out_w + ow)] = (q - yz) * ys;
    };
    try map.put(try getStr(layer, "output"), Tensor{ .shape = shape, .data = data });
}
''')

src = remove_function_if_present(src, 'dimIndex')
src = remove_function_if_present(src, 'broadcastOffset')
src = replace_function(src, 'opAdd', r'''
fn dimIndex(flat: usize, shape: []const usize, dim: usize) usize {
    var div: usize = 1;
    var i = dim + 1;
    while (i < shape.len) : (i += 1) div *= shape[i];
    return (flat / div) % shape[dim];
}

fn broadcastOffset(flat: usize, out_shape: []const usize, src_shape: []const usize) usize {
    var off: usize = 0;
    const rank = out_shape.len;
    for (src_shape, 0..) |d, j| {
        const out_dim = rank - src_shape.len + j;
        const ix = if (d == 1) 0 else dimIndex(flat, out_shape, out_dim);
        off = off * d + ix;
    }
    return off;
}

fn opAdd(layer: std.json.ObjectMap, map: *std.StringHashMap(Tensor), allocator: std.mem.Allocator) !void {
    const a = try getTensor(map, try getStr(layer, "a"));
    const b = try getTensor(map, try getStr(layer, "b"));
    const shape = try broadcastShape(a.shape, b.shape, allocator);
    const data = try allocator.alloc(f64, shapeCount(shape));
    for (data, 0..) |*slot, i| {
        slot.* = a.data[broadcastOffset(i, shape, a.shape)] + b.data[broadcastOffset(i, shape, b.shape)];
    }
    try map.put(try getStr(layer, "output"), Tensor{ .shape = shape, .data = data });
}
''')

src = replace_function(src, 'opGemm', r'''
fn opGemm(layer: std.json.ObjectMap, map: *std.StringHashMap(Tensor), allocator: std.mem.Allocator) !void {
    const a = try getTensor(map, try getStr(layer, "a"));
    const b = try getTensor(map, try getStr(layer, "b"));
    if (a.shape.len != 2 or b.shape.len != 2) return error.InvalidInput;
    const trans_b = if (layer.get("transB")) |v| switch (v) { .bool => |bb| bb, else => false } else false;
    const n = a.shape[0]; const k = a.shape[1];
    const o = if (trans_b) b.shape[0] else b.shape[1];
    const bk = if (trans_b) b.shape[1] else b.shape[0];
    if (bk != k) return error.InvalidInput;
    const alpha = if (layer.get("alpha")) |v| try num(v) else 1.0;
    const beta = if (layer.get("beta")) |v| try num(v) else 1.0;
    const bias_name = if (layer.get("bias")) |v| try str(v) else "";
    const has_bias = bias_name.len != 0;
    const bias = if (has_bias) try getTensor(map, bias_name) else Tensor{ .shape = &[_]usize{}, .data = &[_]f64{} };
    const shape = try allocator.alloc(usize, 2);
    shape[0] = n; shape[1] = o;
    const data = try allocator.alloc(f64, n * o);
    for (0..n) |row| for (0..o) |col| {
        var s: f64 = 0.0;
        for (0..k) |kk| {
            const bv = if (trans_b) b.data[col * k + kk] else b.data[kk * o + col];
            s += a.data[row * k + kk] * bv;
        }
        const bias_v = if (has_bias) bias.data[if (bias.data.len == 1) 0 else col] else 0.0;
        data[row * o + col] = alpha * s + beta * bias_v;
    };
    try map.put(try getStr(layer, "output"), Tensor{ .shape = shape, .data = data });
}
''')

src = replace_checked(
    src,
    'logits.data[b] == logits.data[a] and b > a',
    'logits.data[b] == logits.data[a] and b < a',
    'tie-break ordering',
)

src = replace_function(src, 'main', r'''
pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    const allocator = gpa.allocator();
    const args = try std.process.argsAlloc(allocator);
    var model_path: ?[]const u8 = null;
    var req_path: ?[]const u8 = null;
    var out_dir: ?[]const u8 = null;
    var i: usize = 1;
    while (i < args.len) : (i += 1) {
        if (std.mem.eql(u8, args[i], "--model")) { i += 1; if (i < args.len) model_path = args[i]; }
        else if (std.mem.eql(u8, args[i], "--requests")) { i += 1; if (i < args.len) req_path = args[i]; }
        else if (std.mem.eql(u8, args[i], "--out")) { i += 1; if (i < args.len) out_dir = args[i]; }
    }
    if (model_path == null or req_path == null or out_dir == null) try fail("usage: qinfer --model <model.json> --requests <requests.jsonl> --out <dir>");
    const model_bytes = try std.fs.cwd().readFileAlloc(allocator, model_path.?, 10 * 1024 * 1024);
    const parsed_model = try std.json.parseFromSlice(std.json.Value, allocator, model_bytes, .{});
    const model = try parseModel(parsed_model.value, allocator);

    try std.fs.cwd().makePath(out_dir.?);
    const pred_path = try std.fs.path.join(allocator, &.{ out_dir.?, "predictions.jsonl" });
    var pred_file = try std.fs.cwd().createFile(pred_path, .{ .truncate = true });
    defer pred_file.close();

    const req_bytes = try std.fs.cwd().readFileAlloc(allocator, req_path.?, 10 * 1024 * 1024);
    var lines = std.mem.splitScalar(u8, req_bytes, '\n');
    while (lines.next()) |line0| {
        const line = std.mem.trim(u8, line0, " \t\r");
        if (line.len == 0) continue;
        const parsed_req = std.json.parseFromSlice(std.json.Value, allocator, line, .{}) catch continue;
        const ro = obj(parsed_req.value) catch continue;
        const id = getStr(ro, "id") catch continue;
        const logits = runRequest(&model, parsed_req.value, allocator) catch continue;
        try writePrediction(pred_file.writer(), &model, id, logits, allocator);
    }
}
''', prefix='pub fn')

path.write_text(src)
PY
zig build -Doptimize=ReleaseFast
