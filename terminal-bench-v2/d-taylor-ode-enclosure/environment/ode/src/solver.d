module solver;

import std.stdio;
import std.math;
import std.format;
import std.algorithm;
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

struct State {
    double t;
    double y;
    int step;
    double h;
}

void saveState(string path, State s) {
    auto f = File(path, "w");
    f.writefln("%.17g %.17g %d %.17g", s.t, s.y, s.step, s.h);
}

State loadState(string path) {
    auto f = File(path, "r");
    State s;
    f.readf("%f %f %d %f", &s.t, &s.y, &s.step, &s.h);
    return s;
}

double exactValue(Config c, double t, double yStart, double tStart) {
    double dt = t - tStart;
    if (c.problem == "linear") {
        if (abs(c.a) < 1e-15) return yStart + c.b * dt;
        double adt = c.a * dt;
        double term = 1.0;
        double s = 1.0;
        int lim = min(c.maxOrder, 6);
        foreach (i; 1 .. lim + 1) {
            term *= adt;
            s += term;
        }
        return yStart * s + c.b * dt;
    }
    double dy = c.r * yStart * (1.0 - yStart / c.k);
    return yStart + dt * dy;
}

int chooseOrder(Config c, double h) {
    int o = cast(int)ceil(-log10(max(c.tol, 1e-16))) + 1;
    if (h > 0.15) o -= 1;
    return max(2, min(o, c.maxOrder));
}

double chooseStep(Config c, double t, double y, double h) {
    double slope = abs(c.problem == "linear" ? c.a * y + c.b : c.r * y * (1.0 - y / c.k));
    double next = h;
    if (slope * h > 0.2) next = h * 0.5;
    else if (slope * h < 0.02) next = h * 1.25;
    next = min(max(next, c.hMin), c.hMax);
    if (t + next > c.t1) next = c.t1 - t;
    return next;
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

void runSolver(Config c, string trajPath, string eventPath, string chkPath) {
    Row[] rows;
    EventRow[] events;
    State s;
    if (c.restart) {
        s = loadState(chkPath);
    } else {
        s = State(c.t0, c.y0, 0, min(max(c.hInitial, c.hMin), c.hMax));
    }
    rows ~= Row(s.step, pad(s.t, c.tol * 0.1), pad(s.y, c.tol * 0.1), 0, 0.0);
    double y = s.y;
    double t = s.t;
    double h = s.h;
    int step = s.step;
    while (t < c.t1 - 1e-15) {
        h = chooseStep(c, t, y, h);
        double tn = t + h;
        double yn = exactValue(c, tn, y, t);
        int order = chooseOrder(c, h);
        auto ti = pad(tn, c.tol * 0.1);
        auto yi = pad(yn, c.tol * 0.25 + abs(yn) * 1e-15);
        double g0 = y - c.eventLevel;
        double g1 = yn - c.eventLevel;
        if ((g0 < 0 && g1 >= 0) || (g0 > 0 && g1 <= 0)) {
            events ~= EventRow(cast(int)events.length, Interval(t, tn), Interval(min(y, yn), max(y, yn)));
        }
        step += 1;
        rows ~= Row(step, ti, yi, order, h);
        t = tn;
        y = yn;
        if (!c.restart && c.checkpointTime > c.t0 && c.checkpointTime < c.t1 && t >= c.checkpointTime) {
            saveState(chkPath, State(t, y, step, h));
            writeRows(trajPath, eventPath, rows, events);
            return;
        }
    }
    saveState(chkPath, State(t, y, step, h));
    writeRows(trajPath, eventPath, rows, events);
}
