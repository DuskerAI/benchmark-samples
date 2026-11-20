#!/bin/bash
set -euo pipefail

cd /app

perl -0pi -e 's/double n = tx\*tx \+ tl\*tl;/double n = sqrt(tx*tx + tl*tl);/; s/else if \(tl \* oldTl < 0\.0\)/else if (tx * oldTx + tl * oldTl < 0.0)/' source/model.d

perl -0pi -e 's/q = a01; a01 = a11; a11 = q;\n    }/q = a01; a01 = a11; a11 = q;\n        q = b0; b0 = b1; b1 = q;\n    }/' source/linalg.d

perl -0pi -e 's/nextDs=state\.requestedDs;\n            state\.rows\[\$-1\]\.tl=abs\(state\.rows\[\$-1\]\.tl\);/nextDs=state.nextDs;/; s/Row first; first\.x=o\.x0;/Row first; first.usedDs=0.0; first.x=o.x0;/; s/\n                    base\.x=x; base\.lambda=lambda;//' source/main.d

perl -0pi -e 's/min\(o\.ds\*1\.6,used\*1\.25\)/min(state.requestedDs*1.6,used*1.25)/; s/max\(o\.ds\/32,used\*0\.6\)/max(state.requestedDs\/32,used*0.6)/; s/used < o\.ds\/64/used < state.requestedDs\/64/' source/main.d

perl -0pi -e 's/put\(b, s\.a\); put\(b, s\.c\); put\(b, s\.requestedDs\); put\(b, s\.requestedDs\);/put(b, s.a); put(b, s.c); put(b, s.requestedDs); put(b, s.nextDs);/' source/stateio.d

python3 - <<'PY'
from pathlib import Path
p = Path('source/main.d')
s = p.read_text()
insert = r'''
void validateLoadedCheckpoint(ref const Saved state) {
    if (state.rows.length == 0) throw new Exception("invalid checkpoint");
    foreach (size_t i, const Row r; state.rows) {
        if (r.index != cast(uint)i) throw new Exception("invalid checkpoint");
        if (!isFinite(r.x) || !isFinite(r.lambda) || !isFinite(r.tx) || !isFinite(r.tl) || !isFinite(r.usedDs))
            throw new Exception("invalid checkpoint");
        if (abs(residual(r.x, r.lambda, state.a, state.c)) > 1e-10)
            throw new Exception("invalid checkpoint");
        if (abs(r.tx*r.tx + r.tl*r.tl - 1.0) > 3e-12)
            throw new Exception("invalid checkpoint");
        if (abs(derivative(r.x, state.a)*r.tx + r.tl) > 3e-12)
            throw new Exception("invalid checkpoint");
        if (i == 0) {
            if (abs(r.usedDs) > 0.0 || r.iterations != 0 || r.rejected != 0)
                throw new Exception("invalid checkpoint");
        } else {
            const Row prev = state.rows[i-1];
            if (prev.tx*r.tx + prev.tl*r.tl < ROTATION_DOT_MIN)
                throw new Exception("invalid checkpoint");
            if (abs((r.x-prev.x)*prev.tx + (r.lambda-prev.lambda)*prev.tl - r.usedDs) > 2e-10)
                throw new Exception("invalid checkpoint");
        }
    }
}
'''
s = s.replace('import model, linalg, stateio;\n', 'import model, linalg, stateio;\n\nenum double ROTATION_DOT_MIN = 0.95;\n')
s = s.replace('int main(string[] args) {', insert + '\nint main(string[] args) {')
s = s.replace('state=loadState(o.resume);\n            if (abs(state.a-o.a)>1e-14',
              'state=loadState(o.resume);\n            validateLoadedCheckpoint(state);\n            if (abs(state.a-o.a)>1e-14')
s = s.replace('            if (o.checkpoint.length && o.checkpointEvery && (accepted.index % o.checkpointEvery)==0)\n                saveState(o.checkpoint,state);\n', '')
s = s.replace('                    accepted.usedDs=used; accepted.iterations=it; accepted.rejected=rejected;\n                    branchTangent(x,o.a,base.tx,base.tl,false,o.direction,accepted.tx,accepted.tl);\n                    nextDs = it<=3 ? min(state.requestedDs*1.6,used*1.25) : (it>=6 ? max(state.requestedDs/32,used*0.6) : used);',
              '                    double candTx, candTl;\n                    branchTangent(x,o.a,base.tx,base.tl,false,o.direction,candTx,candTl);\n                    if (base.tx*candTx + base.tl*candTl < ROTATION_DOT_MIN) {\n                        ok=false;\n                        ++rejected; used*=0.5;\n                        if (used < state.requestedDs/64 || rejected>12) throw new Exception("continuation failed");\n                        continue;\n                    } else {\n                        accepted.usedDs=used; accepted.iterations=it; accepted.rejected=rejected;\n                        accepted.tx=candTx; accepted.tl=candTl;\n                        nextDs = it<=3 ? min(state.requestedDs*1.6,used*1.25) : (it>=6 ? max(state.requestedDs/32,used*0.6) : used);\n                    }')
p.write_text(s)
PY

python3 - <<'PY'
from pathlib import Path
p = Path('source/stateio.d')
p.write_text(r'''module stateio;

import std.file : read, write, rename;
import std.stdint : uint64_t;

struct Row {
    uint index;
    double x, lambda, tx, tl, usedDs;
    uint iterations, rejected;
}

struct Saved {
    double a, c, requestedDs, nextDs;
    int direction;
    Row[] rows;
}

private void put(T)(ref ubyte[] b, T v) {
    auto p = (cast(ubyte[]) (&v)[0 .. 1]);
    b ~= p;
}

private T get(T)(const(ubyte)[] b, ref size_t at) {
    T v;
    (cast(ubyte[]) (&v)[0 .. 1])[] = b[at .. at + T.sizeof];
    at += T.sizeof;
    return v;
}

private ulong digest(const(ubyte)[] b) pure {
    ulong h = 14695981039346656037UL;
    foreach (ubyte x; b) {
        h ^= cast(ulong)x;
        h *= 1099511628211UL;
    }
    return h;
}

void saveState(string path, ref const Saved s) {
    ubyte[] b = cast(ubyte[]) "PACD0002".dup;
    put(b, s.a); put(b, s.c); put(b, s.requestedDs); put(b, s.nextDs);
    put(b, s.direction);
    put(b, cast(uint64_t) s.rows.length);
    foreach (r; s.rows) {
        put(b, r.index); put(b, r.x); put(b, r.lambda); put(b, r.tx); put(b, r.tl);
        put(b, r.usedDs); put(b, r.iterations); put(b, r.rejected);
    }
    put(b, cast(uint64_t)digest(b));
    auto tmp = path ~ ".tmp";
    write(tmp, b);
    rename(tmp, path);
}

Saved loadState(string path) {
    auto b = cast(ubyte[]) read(path);
    if (b.length < 16 || cast(string)b[0..8] != "PACD0002") throw new Exception("invalid checkpoint");
    size_t payloadEnd = b.length - uint64_t.sizeof;
    size_t sumAt = payloadEnd;
    auto stored = get!uint64_t(b, sumAt);
    if (digest(b[0 .. payloadEnd]) != stored) throw new Exception("invalid checkpoint");
    size_t at = 8;
    Saved s;
    s.a=get!double(b,at); s.c=get!double(b,at); s.requestedDs=get!double(b,at); s.nextDs=get!double(b,at);
    s.direction=get!int(b,at);
    auto n=get!uint64_t(b,at);
    if (n > 100000 || at + n*(uint.sizeof + 5*double.sizeof + 2*uint.sizeof) != payloadEnd)
        throw new Exception("invalid checkpoint");
    foreach (_; 0 .. n) {
        Row r;
        r.index=get!uint(b,at); r.x=get!double(b,at); r.lambda=get!double(b,at);
        r.tx=get!double(b,at); r.tl=get!double(b,at); r.usedDs=get!double(b,at);
        r.iterations=get!uint(b,at); r.rejected=get!uint(b,at);
        s.rows ~= r;
    }
    return s;
}
''')
PY

make clean all
