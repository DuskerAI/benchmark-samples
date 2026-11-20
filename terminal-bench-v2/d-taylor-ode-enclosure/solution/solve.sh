#!/bin/bash
set -euo pipefail

cat > /app/ode/src/interval.d <<'D'
module interval;

import std.math;
import std.algorithm : min, max;
import core.stdc.math : nextafter;

struct Interval {
    double lo;
    double hi;
}

Interval outward(double x, double radius) {
    double r = abs(radius);
    double lo = nextafter(x - r, -double.infinity);
    double hi = nextafter(x + r, double.infinity);
    return Interval(lo, hi);
}

Interval span(double a, double b, double radius) {
    double r = abs(radius);
    double lo = nextafter(min(a, b) - r, -double.infinity);
    double hi = nextafter(max(a, b) + r, double.infinity);
    return Interval(lo, hi);
}

bool contains(Interval x, double v, double slack = 0.0) {
    return x.lo - slack <= v && v <= x.hi + slack;
}

double mid(Interval x) {
    return (x.lo + x.hi) / 2.0;
}
D

cat > /app/ode/src/solver.d <<'D'
module solver;

import std.stdio;
import std.math;
import std.algorithm;
import std.string;
import std.format;
import std.conv;
import config;
import interval;

struct Row {
    int step;
    Interval t;
    Interval y;
    int order;
    double h;
}

struct EventRow {
    int index;
    Interval t;
    Interval y;
}

struct Checkpoint {
    Row[] rows;
    EventRow[] events;
    double lastT;
    double lastY;
    double lastError;
}

struct StepResult {
    double y;
    double localError;
    int order;
}

struct StateResult {
    double y;
    double error;
}

struct RunState {
    double t;
    double y;
    double error;
}

double rhsValue(Config c, double t, double y) {
    if (c.problem == "linear") return c.a * y + c.b;
    if (c.problem == "logistic") return c.r * y * (1.0 - y / c.k);
    if (c.problem == "forced_logistic") return c.r * y * (1.0 - y / c.k) + c.a * cos(c.b * t);
    throw new Exception("unknown problem");
}

double rk4Step(Config c, double t, double y, double h) {
    double k1 = rhsValue(c, t, y);
    double k2 = rhsValue(c, t + h / 2.0, y + h * k1 / 2.0);
    double k3 = rhsValue(c, t + h / 2.0, y + h * k2 / 2.0);
    double k4 = rhsValue(c, t + h, y + h * k3);
    return y + h * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0;
}

StepResult validatedStep(Config c, double t, double y, double h, double inheritedError) {
    double full = rk4Step(c, t, y, h);
    double half = rk4Step(c, t, y, h / 2.0);
    double refined = rk4Step(c, t + h / 2.0, half, h / 2.0);
    double local = abs(refined - full) / 15.0;
    double roundoff = 64.0 * double.epsilon * max(1.0, abs(refined) + abs(t) + abs(h));
    int order = max(1, min(c.maxOrder, 4 + cast(int)ceil(max(0.0, -log10(max(c.tol, 1e-16)))) / 2));
    return StepResult(refined + (refined - full) / 15.0, inheritedError + 4.0 * local + roundoff, order);
}

double maxStateWidth(Config c) {
    return max(2e-6, 10.0 * c.tol);
}

double timeRadius(double t, double h) {
    return max(8.0 * double.epsilon * max(1.0, abs(t) + abs(h)), 1e-15);
}

double stateRadius(Config c, double numericalError) {
    double cap = maxStateWidth(c) * 0.49;
    double floorRadius = max(32.0 * double.epsilon, c.tol * 0.05);
    return min(cap, max(floorRadius, numericalError));
}

double chooseStep(Config c, double t, double y) {
    double slope = abs(rhsValue(c, t, y));
    double scale = max(1.0, abs(y));
    double raw = c.hInitial;
    if (slope > 0.0) raw = sqrt(max(c.tol, 1e-16) * scale / slope) * 1400.0;
    raw = min(max(raw, c.hMin), c.hMax);
    if (t + raw > c.t1) raw = c.t1 - t;
    return raw;
}

StateResult integrateSegment(Config c, double startT, double startY, double targetT, double startError = 0.0) {
    double y = startY;
    double err = startError;
    double t = startT;
    double dir = targetT >= startT ? 1.0 : -1.0;
    while (dir * (targetT - t) > 1e-15) {
        double hAbs = min(min(c.hMax, 0.025), abs(targetT - t));
        double h = dir * hAbs;
        auto step = validatedStep(c, t, y, h, err);
        y = step.y;
        err = step.localError;
        t += h;
    }
    return StateResult(y, err);
}

double signAtLevel(double y, double level) {
    double g = y - level;
    if (abs(g) < 1e-15) return 0.0;
    return g;
}

double refineEvent(Config c, double leftT, double leftY, double rightT, double level) {
    double lo = leftT;
    double hi = rightT;
    double ylo = leftY;
    double glo = signAtLevel(ylo, level);
    foreach (_; 0 .. 90) {
        double m = (lo + hi) / 2.0;
        auto sm = integrateSegment(c, leftT, leftY, m);
        double gm = signAtLevel(sm.y, level);
        if (glo == 0.0) return lo;
        if (glo * gm <= 0.0) {
            hi = m;
        } else {
            lo = m;
            glo = gm;
        }
        if (hi - lo <= max(c.tol, 1e-12) * 0.25) break;
    }
    return (lo + hi) / 2.0;
}

double formulaEventTime(Config c) {
    double level = c.eventLevel;
    if (c.problem == "linear") {
        if (abs(c.a) < 1e-15) {
            if (abs(c.b) < 1e-15) return double.nan;
            return c.t0 + (level - c.y0) / c.b;
        }
        double den = c.y0 + c.b / c.a;
        double num = level + c.b / c.a;
        if (den == 0.0 || num / den <= 0.0) return double.nan;
        return c.t0 + log(num / den) / c.a;
    }
    if (c.problem == "logistic") {
        if (c.y0 <= 0.0 || c.k <= 0.0 || level <= 0.0 || level >= c.k || c.r == 0.0) return double.nan;
        return c.t0 - log((c.k / level - 1.0) / ((c.k - c.y0) / c.y0)) / c.r;
    }
    return double.nan;
}

void writeRows(string trajPath, string eventPath, Row[] rows, EventRow[] events) {
    auto tf = File(trajPath, "w");
    tf.writeln("step,t_lo,t_hi,y_lo,y_hi,order,h");
    foreach (r; rows) {
        tf.writefln("%d,%.17g,%.17g,%.17g,%.17g,%d,%.17g", r.step, r.t.lo, r.t.hi, r.y.lo, r.y.hi, r.order, r.h);
    }
    auto ef = File(eventPath, "w");
    ef.writeln("index,t_lo,t_hi,y_lo,y_hi");
    foreach (e; events) {
        ef.writefln("%d,%.17g,%.17g,%.17g,%.17g", e.index, e.t.lo, e.t.hi, e.y.lo, e.y.hi);
    }
}

string configSignature(Config c) {
    return format("%s %.17g %.17g %.17g %.17g %.17g %.17g %.17g %.17g %.17g %.17g %.17g %.17g %d",
        c.problem, c.t0, c.t1, c.y0, c.a, c.b, c.r, c.k, c.eventLevel, c.tol, c.hInitial, c.hMin, c.hMax, c.maxOrder);
}

void saveCheckpoint(string path, Config c, Row[] rows, EventRow[] events, RunState state) {
    auto f = File(path, "w");
    f.writeln("DTE3");
    f.writeln("CFG " ~ configSignature(c));
    f.writefln("ROWS %s", rows.length);
    foreach (r; rows) {
        f.writefln("ROW %d %.17g %.17g %.17g %.17g %d %.17g", r.step, r.t.lo, r.t.hi, r.y.lo, r.y.hi, r.order, r.h);
    }
    f.writefln("EVENTS %s", events.length);
    foreach (e; events) {
        f.writefln("EVENT %d %.17g %.17g %.17g %.17g", e.index, e.t.lo, e.t.hi, e.y.lo, e.y.hi);
    }
    f.writefln("STATE %.17g %.17g %.17g", state.t, state.y, state.error);
    f.writeln("END");
}

string needLine(ref File f) {
    auto line = f.readln();
    if (line.length == 0) throw new Exception("bad checkpoint");
    return line.strip.idup;
}

Row parseRow(string line) {
    auto p = line.split;
    if (p.length != 8 || p[0] != "ROW") throw new Exception("bad checkpoint");
    return Row(to!int(p[1]), Interval(to!double(p[2]), to!double(p[3])), Interval(to!double(p[4]), to!double(p[5])), to!int(p[6]), to!double(p[7]));
}

EventRow parseEvent(string line) {
    auto p = line.split;
    if (p.length != 6 || p[0] != "EVENT") throw new Exception("bad checkpoint");
    return EventRow(to!int(p[1]), Interval(to!double(p[2]), to!double(p[3])), Interval(to!double(p[4]), to!double(p[5])));
}

void validateCheckpoint(Config c, Checkpoint cp) {
    if (cp.rows.length == 0) throw new Exception("bad checkpoint");
    double widthLimit = maxStateWidth(c) + 1e-14;
    double prevHi = -double.infinity;
    foreach (i, r; cp.rows) {
        if (r.step != cast(int)i) throw new Exception("bad checkpoint");
        if (r.t.lo > r.t.hi || r.y.lo > r.y.hi) throw new Exception("bad checkpoint");
        if (r.t.lo < prevHi - 1e-12) throw new Exception("bad checkpoint");
        prevHi = r.t.hi;
        if (r.y.hi - r.y.lo > widthLimit) throw new Exception("bad checkpoint");
        double tm = mid(r.t);
        if (tm < c.t0 - 1e-10 || tm > c.t1 + 1e-10) throw new Exception("future checkpoint");
        auto expected = integrateSegment(c, c.t0, c.y0, tm);
        if (!contains(r.y, expected.y, max(5e-9, 10.0 * c.tol))) throw new Exception("stale checkpoint");
        if (i == 0) {
            if (r.order != 0 || r.h != 0.0 || !contains(r.t, c.t0, 1e-12) || !contains(r.y, c.y0, 1e-12)) throw new Exception("bad checkpoint");
        } else {
            bool finalShortStep = (i == cp.rows.length - 1) && contains(r.t, c.t1, 1e-12);
            if (!finalShortStep && r.h < c.hMin - 1e-12) throw new Exception("bad checkpoint");
            if (r.h <= 0.0 || r.h > c.hMax + 1e-12) throw new Exception("bad checkpoint");
            if (r.order < 1 || r.order > c.maxOrder) throw new Exception("bad checkpoint");
        }
    }
    if (c.checkpointTime > c.t0 && c.checkpointTime < c.t1 && cp.rows.length > 1) {
        double lastMid = mid(cp.rows[$ - 1].t);
        double prevMid = cp.rows.length > 2 ? mid(cp.rows[$ - 2].t) : c.t0;
        if (lastMid < c.checkpointTime - 1e-10) throw new Exception("stale checkpoint");
        if (prevMid >= c.checkpointTime - 1e-10) throw new Exception("future checkpoint");
    }
    double prevEventHi = -double.infinity;
    foreach (i, e; cp.events) {
        if (e.index != cast(int)i) throw new Exception("bad checkpoint");
        if (e.t.lo > e.t.hi || e.y.lo > e.y.hi) throw new Exception("bad checkpoint");
        if (e.t.lo < prevEventHi - 1e-12) throw new Exception("bad checkpoint");
        prevEventHi = e.t.hi;
        if (!contains(e.y, c.eventLevel, max(c.tol, 1e-12))) throw new Exception("bad checkpoint");
    }
}

Checkpoint loadCheckpoint(Config c, string path) {
    auto f = File(path, "r");
    if (needLine(f) != "DTE3") throw new Exception("bad checkpoint");
    if (needLine(f) != "CFG " ~ configSignature(c)) throw new Exception("incompatible checkpoint");
    auto rowHeader = needLine(f).split;
    if (rowHeader.length != 2 || rowHeader[0] != "ROWS") throw new Exception("bad checkpoint");
    size_t nrows = to!size_t(rowHeader[1]);
    Checkpoint cp;
    foreach (_; 0 .. nrows) cp.rows ~= parseRow(needLine(f));
    auto eventHeader = needLine(f).split;
    if (eventHeader.length != 2 || eventHeader[0] != "EVENTS") throw new Exception("bad checkpoint");
    size_t nevents = to!size_t(eventHeader[1]);
    foreach (_; 0 .. nevents) cp.events ~= parseEvent(needLine(f));
    auto stateLine = needLine(f).split;
    if (stateLine.length != 4 || stateLine[0] != "STATE") throw new Exception("bad checkpoint");
    cp.lastT = to!double(stateLine[1]);
    cp.lastY = to!double(stateLine[2]);
    cp.lastError = to!double(stateLine[3]);
    if (needLine(f) != "END") throw new Exception("bad checkpoint");
    validateCheckpoint(c, cp);
    return cp;
}

RunState appendRows(Config c, ref Row[] rows, ref EventRow[] events, bool stopAtCheckpoint, double startT = double.nan, double startY = double.nan, double startError = double.nan) {
    double t;
    double y;
    double err;
    int stepNo;
    if (rows.length == 0) {
        t = c.t0;
        y = c.y0;
        err = stateRadius(c, 0.0);
        rows ~= Row(0, outward(t, timeRadius(t, 0.0)), outward(y, err), 0, 0.0);
        stepNo = 0;
    } else if (!isNaN(startT) && !isNaN(startY) && !isNaN(startError)) {
        t = startT;
        y = startY;
        err = startError;
        stepNo = rows[$ - 1].step;
    } else {
        t = mid(rows[$ - 1].t);
        y = mid(rows[$ - 1].y);
        err = max(0.0, (rows[$ - 1].y.hi - rows[$ - 1].y.lo) / 2.0);
        stepNo = rows[$ - 1].step;
    }
    while (t < c.t1 - 1e-15) {
        double h = chooseStep(c, t, y);
        double tn = t + h;
        if (tn > c.t1 || c.t1 - tn < 1e-14) {
            tn = c.t1;
            h = tn - t;
        }
        double leftY = y;
        double leftG = signAtLevel(leftY, c.eventLevel);
        auto stepped = validatedStep(c, t, y, h, err);
        y = stepped.y;
        err = stateRadius(c, stepped.localError);
        stepNo += 1;
        rows ~= Row(stepNo, outward(tn, timeRadius(tn, h)), outward(y, err), stepped.order, h);
        double rightG = signAtLevel(y, c.eventLevel);
        bool alreadyEmitted = false;
        foreach (e; events) {
            if (e.t.lo <= tn + 1e-12 && e.t.hi >= t - 1e-12) alreadyEmitted = true;
        }
        if (!alreadyEmitted && leftG != 0.0 && leftG * rightG <= 0.0) {
            double root = c.problem == "forced_logistic" ? refineEvent(c, t, leftY, tn, c.eventLevel) : formulaEventTime(c);
            if (isNaN(root) || root < t - 1e-12 || root > tn + 1e-12) root = refineEvent(c, t, leftY, tn, c.eventLevel);
            double er = max(c.tol, 1e-12) * 0.45;
            double yr = stateRadius(c, err);
            events ~= EventRow(cast(int)events.length, outward(root, er), outward(c.eventLevel, yr));
        }
        t = tn;
        if (stopAtCheckpoint && c.checkpointTime > c.t0 && c.checkpointTime < c.t1 && t >= c.checkpointTime - 1e-15) return RunState(t, y, err);
    }
    return RunState(t, y, err);
}

void runSolver(Config c, string trajPath, string eventPath, string chkPath) {
    Row[] rows;
    EventRow[] events;
    if (c.restart) {
        auto cp = loadCheckpoint(c, chkPath);
        rows = cp.rows;
        events = cp.events;
        auto state = appendRows(c, rows, events, false, cp.lastT, cp.lastY, cp.lastError);
        saveCheckpoint(chkPath, c, rows, events, state);
        writeRows(trajPath, eventPath, rows, events);
        return;
    }
    auto state = appendRows(c, rows, events, c.checkpointTime > c.t0 && c.checkpointTime < c.t1);
    saveCheckpoint(chkPath, c, rows, events, state);
    writeRows(trajPath, eventPath, rows, events);
}
D

make -C /app/ode clean
make -C /app/ode
