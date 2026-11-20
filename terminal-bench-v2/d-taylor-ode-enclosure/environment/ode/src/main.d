module main;

import std.stdio;
import config;
import solver;

int main(string[] args) {
    if (args.length != 5) {
        stderr.writeln("usage: taylor_ode <config> <trajectory.csv> <events.csv> <checkpoint.bin>");
        return 2;
    }
    try {
        auto c = loadConfig(args[1]);
        runSolver(c, args[2], args[3], args[4]);
        return 0;
    } catch (Exception e) {
        stderr.writeln(e.msg);
        return 1;
    }
}
