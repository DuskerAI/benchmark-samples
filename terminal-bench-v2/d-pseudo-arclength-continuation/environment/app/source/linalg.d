module linalg;

import std.math : abs;

bool solveBordered(double a00, double a01, double a10, double a11,
                   double b0, double b1, out double x0, out double x1) pure {
    if (abs(a10) > abs(a00)) {
        double q = a00; a00 = a10; a10 = q;
        q = a01; a01 = a11; a11 = q;
    }
    if (abs(a00) < 1e-15) return false;
    double m = a10 / a00;
    a11 -= m * a01;
    b1 -= m * b0;
    if (abs(a11) < 1e-15) return false;
    x1 = b1 / a11;
    x0 = (b0 - a01*x1) / a00;
    return true;
}
