module interval;

import std.math;

struct Interval {
    double lo;
    double hi;
}

Interval thin(double x) {
    return Interval(x, x);
}

Interval pad(double x, double radius) {
    double r = abs(radius);
    return Interval(x - r, x + r);
}

bool contains(Interval x, double v) {
    return x.lo <= v && v <= x.hi;
}
