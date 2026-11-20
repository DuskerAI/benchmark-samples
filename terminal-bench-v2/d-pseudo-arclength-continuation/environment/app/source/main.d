import std.algorithm : min, max;
import std.conv : to;
import std.file : write;
import std.format : format;
import std.math : abs, isFinite;
import std.stdio : stderr, writeln;
import std.string : strip;

import model, linalg, stateio;

struct Options {
    double a=1.0, c=0.0, x0=-1.2, ds=0.18;
    int direction=1;
    uint steps=20, checkpointEvery=0;
    string output, checkpoint, resume;
}

void usage() {
    writeln("usage: continuation --output FILE [--a A --c C --x0 X --ds DS --direction -1|1 --steps N] [--checkpoint FILE --checkpoint-every N] [--resume FILE]");
    writeln("CSV fields: step,x,lambda,tx,tl,ds,iterations,rejected");
}

Options arguments(string[] args) {
    Options o;
    for (size_t i=1; i<args.length; ++i) {
        string k=args[i];
        if (k=="--help") { usage(); import core.stdc.stdlib : exit; exit(0); }
        if (i+1>=args.length) throw new Exception("missing value");
        string v=args[++i];
        switch(k) {
            case "--a": o.a=to!double(v); break; case "--c": o.c=to!double(v); break;
            case "--x0": o.x0=to!double(v); break; case "--ds": o.ds=to!double(v); break;
            case "--direction": o.direction=to!int(v); break; case "--steps": o.steps=to!uint(v); break;
            case "--output": o.output=v; break; case "--checkpoint": o.checkpoint=v; break;
            case "--checkpoint-every": o.checkpointEvery=to!uint(v); break; case "--resume": o.resume=v; break;
            default: throw new Exception("unknown option "~k);
        }
    }
    if (o.output.length==0 || o.a<=0 || o.ds<=0 || o.steps<1 || (o.direction!=1 && o.direction != -1))
        throw new Exception("invalid arguments");
    return o;
}

void emit(string path, const Row[] rows) {
    string s="step,x,lambda,tx,tl,ds,iterations,rejected\n";
    foreach(r; rows) s ~= format("%d,%.17g,%.17g,%.17g,%.17g,%.17g,%d,%d\n",
        r.index,r.x,r.lambda,r.tx,r.tl,r.usedDs,r.iterations,r.rejected);
    write(path,s);
}

int main(string[] args) {
    try {
        auto o=arguments(args);
        Saved state;
        double nextDs;
        if (o.resume.length) {
            state=loadState(o.resume);
            if (abs(state.a-o.a)>1e-14 || abs(state.c-o.c)>1e-14 || state.direction!=o.direction)
                throw new Exception("checkpoint model mismatch");
            nextDs=state.requestedDs;
            state.rows[$-1].tl=abs(state.rows[$-1].tl);
        } else {
            state.a=o.a; state.c=o.c; state.requestedDs=o.ds; state.nextDs=o.ds; state.direction=o.direction;
            Row first; first.x=o.x0; first.lambda=o.c+o.a*o.x0-o.x0*o.x0*o.x0;
            branchTangent(first.x,o.a,0,0,true,o.direction,first.tx,first.tl);
            state.rows ~= first;
            nextDs=o.ds;
        }
        while (state.rows.length <= o.steps) {
            auto base=state.rows[$-1];
            uint rejected=0;
            double used=nextDs;
            Row accepted;
            bool ok=false;
            while (!ok) {
                double px=base.x+used*base.tx, pl=base.lambda+used*base.tl;
                double x=px, lambda=pl;
                uint it=0;
                for (; it<7; ++it) {
                    double f=residual(x,lambda,o.a,o.c);
                    double g=(x-px)*base.tx+(lambda-pl)*base.tl;
                    if (abs(f)<2e-13 && abs(g)<2e-13) { ok=true; break; }
                    double dx,dl;
                    if (!solveBordered(derivative(x,o.a),1,base.tx,base.tl,-f,-g,dx,dl)) break;
                    x+=dx; lambda+=dl;
                    base.x=x; base.lambda=lambda;
                    if (!isFinite(x) || !isFinite(lambda)) break;
                }
                if (ok) {
                    accepted.index=cast(uint)state.rows.length; accepted.x=x; accepted.lambda=lambda;
                    accepted.usedDs=used; accepted.iterations=it; accepted.rejected=rejected;
                    branchTangent(x,o.a,base.tx,base.tl,false,o.direction,accepted.tx,accepted.tl);
                    nextDs = it<=3 ? min(o.ds*1.6,used*1.25) : (it>=6 ? max(o.ds/32,used*0.6) : used);
                } else {
                    ++rejected; used*=0.5;
                    if (used < o.ds/64 || rejected>12) throw new Exception("continuation failed");
                }
            }
            state.rows ~= accepted;
            state.nextDs=nextDs;
            if (o.checkpoint.length && o.checkpointEvery && (accepted.index % o.checkpointEvery)==0)
                saveState(o.checkpoint,state);
        }
        emit(o.output,state.rows[0 .. o.steps+1]);
        if (o.checkpoint.length) saveState(o.checkpoint,state);
        return 0;
    } catch(Exception e) { stderr.writeln(e.msg); return 2; }
}
