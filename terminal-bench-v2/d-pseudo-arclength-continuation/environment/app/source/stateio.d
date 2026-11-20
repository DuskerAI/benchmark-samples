module stateio;

import std.file : read, write;
import std.stdint : uint32_t, uint64_t;
import std.string : representation;

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

void saveState(string path, ref const Saved s) {
    ubyte[] b = cast(ubyte[]) "PACD0001".dup;
    put(b, s.a); put(b, s.c); put(b, s.requestedDs); put(b, s.requestedDs);
    put(b, s.direction);
    put(b, cast(uint64_t) s.rows.length);
    foreach (r; s.rows) {
        put(b, r.index); put(b, r.x); put(b, r.lambda); put(b, r.tx); put(b, r.tl);
        put(b, r.usedDs); put(b, r.iterations); put(b, r.rejected);
    }
    write(path, b);
}

Saved loadState(string path) {
    auto b = cast(ubyte[]) read(path);
    if (b.length < 8 || cast(string)b[0..8] != "PACD0001") throw new Exception("invalid checkpoint");
    size_t at = 8;
    Saved s;
    s.a=get!double(b,at); s.c=get!double(b,at); s.requestedDs=get!double(b,at); s.nextDs=get!double(b,at);
    s.direction=get!int(b,at);
    auto n=get!uint64_t(b,at);
    if (n > 100000 || at + n*(uint.sizeof + 5*double.sizeof + 2*uint.sizeof) != b.length)
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
