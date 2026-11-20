const std = @import("std");

const Tensor = struct {
    shape: []usize,
    data: []f64,
};

const Model = struct {
    tensors: std.StringHashMap(Tensor),
    layers: []std.json.Value,
    classes: []const []const u8,
    output_name: []const u8,
    top_k: usize,
};

fn fail(msg: []const u8) !void {
    try std.io.getStdErr().writer().print("{s}\n", .{msg});
    return error.InvalidInput;
}

fn obj(v: std.json.Value) !std.json.ObjectMap {
    return switch (v) {
        .object => |o| o,
        else => error.InvalidInput,
    };
}

fn arr(v: std.json.Value) !std.json.Array {
    return switch (v) {
        .array => |a| a,
        else => error.InvalidInput,
    };
}

fn str(v: std.json.Value) ![]const u8 {
    return switch (v) {
        .string => |s| s,
        else => error.InvalidInput,
    };
}

fn num(v: std.json.Value) !f64 {
    return switch (v) {
        .integer => |i| @as(f64, @floatFromInt(i)),
        .float => |f| f,
        else => error.InvalidInput,
    };
}

fn get(o: std.json.ObjectMap, key: []const u8) !std.json.Value {
    return o.get(key) orelse error.InvalidInput;
}

fn getStr(o: std.json.ObjectMap, key: []const u8) ![]const u8 {
    return try str(try get(o, key));
}

fn getNum(o: std.json.ObjectMap, key: []const u8) !f64 {
    return try num(try get(o, key));
}

fn parseUsizeArray(a: std.json.Array, allocator: std.mem.Allocator) ![]usize {
    const out = try allocator.alloc(usize, a.items.len);
    for (a.items, 0..) |it, i| {
        const x = try num(it);
        if (x < 0 or @floor(x) != x) return error.InvalidInput;
        out[i] = @as(usize, @intFromFloat(x));
    }
    return out;
}

fn parseTensor(v: std.json.Value, allocator: std.mem.Allocator) !Tensor {
    const o = try obj(v);
    const shape = try parseUsizeArray(try arr(try get(o, "shape")), allocator);
    const data_a = try arr(try get(o, "data"));
    var count: usize = 1;
    for (shape) |d| count *= d;
    if (count != data_a.items.len) return error.InvalidInput;
    const data = try allocator.alloc(f64, data_a.items.len);
    for (data_a.items, 0..) |it, i| data[i] = try num(it);
    return Tensor{ .shape = shape, .data = data };
}

fn parseModel(root: std.json.Value, allocator: std.mem.Allocator) !Model {
    const ro = try obj(root);
    const tensor_obj = try obj(try get(ro, "tensors"));
    var tensors = std.StringHashMap(Tensor).init(allocator);
    var it = tensor_obj.iterator();
    while (it.next()) |entry| {
        try tensors.put(entry.key_ptr.*, try parseTensor(entry.value_ptr.*, allocator));
    }

    const class_a = try arr(try get(ro, "classes"));
    const classes = try allocator.alloc([]const u8, class_a.items.len);
    for (class_a.items, 0..) |cv, i| classes[i] = try str(cv);
    if (classes.len == 0) return error.InvalidInput;

    const layer_a = try arr(try get(ro, "layers"));
    if (layer_a.items.len == 0) return error.InvalidInput;
    const top_raw = try getNum(ro, "top_k");
    if (top_raw <= 0 or @floor(top_raw) != top_raw) return error.InvalidInput;
    const top_k: usize = @intFromFloat(top_raw);
    if (top_k > classes.len) return error.InvalidInput;

    return Model{
        .tensors = tensors,
        .layers = layer_a.items,
        .classes = classes,
        .output_name = try getStr(ro, "output"),
        .top_k = top_k,
    };
}

fn cloneTensorMap(base: *const std.StringHashMap(Tensor), allocator: std.mem.Allocator) !std.StringHashMap(Tensor) {
    var out = std.StringHashMap(Tensor).init(allocator);
    var it = base.iterator();
    while (it.next()) |entry| try out.put(entry.key_ptr.*, entry.value_ptr.*);
    return out;
}

fn shapeCount(shape: []const usize) usize {
    var c: usize = 1;
    for (shape) |d| c *= d;
    return c;
}

fn getTensor(map: *std.StringHashMap(Tensor), name: []const u8) !Tensor {
    return map.get(name) orelse error.InvalidInput;
}

fn scalarOrFirst(o: std.json.ObjectMap, key: []const u8, allocator: std.mem.Allocator, n: usize) ![]f64 {
    const v = try get(o, key);
    const out = try allocator.alloc(f64, n);
    switch (v) {
        .array => |a| {
            if (a.items.len == 0) return error.InvalidInput;
            const x = try num(a.items[0]);
            for (out) |*p| p.* = x;
        },
        else => {
            const x = try num(v);
            for (out) |*p| p.* = x;
        },
    }
    return out;
}

fn opPad(layer: std.json.ObjectMap, map: *std.StringHashMap(Tensor), allocator: std.mem.Allocator) !void {
    const input = try getTensor(map, try getStr(layer, "input"));
    if (input.shape.len != 4) return error.InvalidInput;
    const pads = try parseUsizeArray(try arr(try get(layer, "pads")), allocator);
    if (pads.len != 8 and pads.len != 4) return error.InvalidInput;
    const out_shape = try allocator.alloc(usize, 4);
    var before = [_]usize{ 0, 0, 0, 0 };
    var after = [_]usize{ 0, 0, 0, 0 };
    if (pads.len == 8) {
        before[2] = pads[2]; before[3] = pads[3]; after[2] = pads[6]; after[3] = pads[7];
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
    const out_h = (H + pads[0] + pads[2] - KH) / strides[0] + 1;
    const out_w = (W + pads[1] + pads[3] - KW) / strides[1] + 1;
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
            var xv: f64 = 0.0;
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

fn broadcastShape(a: []const usize, b: []const usize, allocator: std.mem.Allocator) ![]usize {
    const r = @max(a.len, b.len);
    const out = try allocator.alloc(usize, r);
    for (0..r) |i| {
        const ai = if (i + a.len >= r) a[i + a.len - r] else 1;
        const bi = if (i + b.len >= r) b[i + b.len - r] else 1;
        if (ai != bi and ai != 1 and bi != 1) return error.InvalidInput;
        out[i] = @max(ai, bi);
    }
    return out;
}

fn opAdd(layer: std.json.ObjectMap, map: *std.StringHashMap(Tensor), allocator: std.mem.Allocator) !void {
    const a = try getTensor(map, try getStr(layer, "a"));
    const b = try getTensor(map, try getStr(layer, "b"));
    const shape = try broadcastShape(a.shape, b.shape, allocator);
    const data = try allocator.alloc(f64, shapeCount(shape));
    for (data, 0..) |*p, i| {
        const av = if (a.data.len == data.len) a.data[i] else a.data[0];
        const bv = if (b.data.len == data.len) b.data[i] else b.data[0];
        p.* = av + bv;
    }
    try map.put(try getStr(layer, "output"), Tensor{ .shape = shape, .data = data });
}

fn opRelu(layer: std.json.ObjectMap, map: *std.StringHashMap(Tensor), allocator: std.mem.Allocator) !void {
    const x = try getTensor(map, try getStr(layer, "input"));
    const shape = try allocator.dupe(usize, x.shape);
    const data = try allocator.alloc(f64, x.data.len);
    for (x.data, 0..) |v, i| data[i] = if (v < 0) 0 else v;
    try map.put(try getStr(layer, "output"), Tensor{ .shape = shape, .data = data });
}

fn opFlatten(layer: std.json.ObjectMap, map: *std.StringHashMap(Tensor), allocator: std.mem.Allocator) !void {
    const x = try getTensor(map, try getStr(layer, "input"));
    if (x.shape.len == 0) return error.InvalidInput;
    const shape = try allocator.alloc(usize, 2);
    shape[0] = x.shape[0];
    shape[1] = x.data.len / x.shape[0];
    const data = try allocator.dupe(f64, x.data);
    try map.put(try getStr(layer, "output"), Tensor{ .shape = shape, .data = data });
}

fn opGemm(layer: std.json.ObjectMap, map: *std.StringHashMap(Tensor), allocator: std.mem.Allocator) !void {
    const a = try getTensor(map, try getStr(layer, "a"));
    const b = try getTensor(map, try getStr(layer, "b"));
    if (a.shape.len != 2 or b.shape.len != 2) return error.InvalidInput;
    const n = a.shape[0]; const k = a.shape[1];
    if (b.shape[0] != k) return error.InvalidInput;
    const o = b.shape[1];
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
        for (0..k) |kk| s += a.data[row * k + kk] * b.data[kk * o + col];
        const bv = if (has_bias) bias.data[if (bias.data.len == 1) 0 else col] else 0.0;
        data[row * o + col] = alpha * s + beta * bv;
    };
    try map.put(try getStr(layer, "output"), Tensor{ .shape = shape, .data = data });
}

fn runRequest(model: *const Model, req: std.json.Value, allocator: std.mem.Allocator) !Tensor {
    const ro = try obj(req);
    _ = try getStr(ro, "id");
    const input = try parseTensor(try get(ro, "input"), allocator);
    var map = try cloneTensorMap(&model.tensors, allocator);
    try map.put("input", input);
    for (model.layers) |lv| {
        const lo = try obj(lv);
        const op = try getStr(lo, "op");
        if (std.mem.eql(u8, op, "Pad")) try opPad(lo, &map, allocator)
        else if (std.mem.eql(u8, op, "QLinearConv")) try opQConv(lo, &map, allocator)
        else if (std.mem.eql(u8, op, "Add")) try opAdd(lo, &map, allocator)
        else if (std.mem.eql(u8, op, "Relu")) try opRelu(lo, &map, allocator)
        else if (std.mem.eql(u8, op, "Flatten")) try opFlatten(lo, &map, allocator)
        else if (std.mem.eql(u8, op, "Gemm")) try opGemm(lo, &map, allocator)
        else return error.InvalidInput;
    }
    return try getTensor(&map, model.output_name);
}

fn writePrediction(writer: anytype, model: *const Model, id: []const u8, logits: Tensor, allocator: std.mem.Allocator) !void {
    if (logits.data.len != model.classes.len) return error.InvalidInput;
    const order = try allocator.alloc(usize, logits.data.len);
    for (order, 0..) |*p, i| p.* = i;
    var i: usize = 0;
    while (i < order.len) : (i += 1) {
        var j = i + 1;
        while (j < order.len) : (j += 1) {
            const a = order[i]; const b = order[j];
            if (logits.data[b] > logits.data[a] or (logits.data[b] == logits.data[a] and b > a)) {
                order[i] = b; order[j] = a;
            }
        }
    }
    try writer.print("{{\"id\":\"{s}\",\"scores\":[", .{id});
    for (logits.data, 0..) |v, idx| {
        if (idx != 0) try writer.writeAll(",");
        try writer.print("{d:.6}", .{v});
    }
    try writer.writeAll("],\"top\":[");
    for (0..model.top_k) |k| {
        if (k != 0) try writer.writeAll(",");
        const idx = order[k];
        try writer.print("[\"{s}\",{d:.6}]", .{ model.classes[idx], logits.data[idx] });
    }
    try writer.writeAll("]}\n");
}

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
    try std.fs.cwd().makePath(out_dir.?);
    const pred_path = try std.fs.path.join(allocator, &.{ out_dir.?, "predictions.jsonl" });
    var pred_file = try std.fs.cwd().createFile(pred_path, .{ .truncate = true });
    defer pred_file.close();

    const model_bytes = try std.fs.cwd().readFileAlloc(allocator, model_path.?, 10 * 1024 * 1024);
    const parsed_model = try std.json.parseFromSlice(std.json.Value, allocator, model_bytes, .{});
    const model = try parseModel(parsed_model.value, allocator);
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
