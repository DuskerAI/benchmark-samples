module config;

import std.stdio;
import std.string;
import std.conv;

struct Config {
    string problem;
    double t0;
    double t1;
    double y0;
    double a;
    double b;
    double r;
    double k;
    double eventLevel;
    double tol;
    double hInitial;
    double hMin;
    double hMax;
    int maxOrder;
    double checkpointTime;
    bool restart;
}

Config loadConfig(string path) {
    string[string] kv;
    auto f = File(path, "r");
    foreach (raw; f.byLineCopy) {
        auto s = raw.strip;
        if (s.length == 0) continue;
        auto p = s.indexOf("=");
        if (p < 0) throw new Exception("bad config");
        kv[s[0 .. p].strip.idup] = s[p + 1 .. $].strip.idup;
    }
    string get(string k) {
        if (k !in kv) throw new Exception("missing config");
        return kv[k];
    }
    Config c;
    c.problem = get("problem");
    c.t0 = to!double(get("t0"));
    c.t1 = to!double(get("t1"));
    c.y0 = to!double(get("y0"));
    c.a = to!double(get("a"));
    c.b = to!double(get("b"));
    c.r = to!double(get("r"));
    c.k = to!double(get("k"));
    c.eventLevel = to!double(get("event_level"));
    c.tol = to!double(get("tol"));
    c.hInitial = to!double(get("h_initial"));
    c.hMin = to!double(get("h_min"));
    c.hMax = to!double(get("h_max"));
    c.maxOrder = to!int(get("max_order"));
    c.checkpointTime = to!double(get("checkpoint_time"));
    auto r = get("restart");
    c.restart = (r == "true" || r == "1");
    if (c.t1 < c.t0 || c.tol <= 0 || c.hMin <= 0 || c.hMax < c.hMin || c.maxOrder < 1) throw new Exception("invalid config");
    return c;
}
