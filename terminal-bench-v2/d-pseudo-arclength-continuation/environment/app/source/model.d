module model;

import std.math : sqrt;

double residual(double x, double lambda, double a, double c) pure {
    return x*x*x - a*x + lambda - c;
}

double derivative(double x, double a) pure {
    return 3.0*x*x - a;
}

void branchTangent(double x, double a, double oldTx, double oldTl,
                   bool initial, int direction, out double tx, out double tl) pure {
    tx = 1.0;
    tl = a - 3.0*x*x;
    double n = tx*tx + tl*tl;
    tx /= n;
    tl /= n;
    if (initial) {
        if (tx * direction < 0.0) { tx = -tx; tl = -tl; }
    } else if (tl * oldTl < 0.0) {
        tx = -tx;
        tl = -tl;
    }
}
