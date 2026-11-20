#!/bin/bash
set -euo pipefail
python3 - <<'PY'
from pathlib import Path
p = Path('/opt/quic-replay/src/main.cpp')
s = p.read_text()
s = s.replace('#include <iostream>\n', '#include <iostream>\n#include <limits>\n#include <numeric>\n')
s = s.replace('uint64_t value = first;\n    for (int i = 1; i < len; ++i) {', 'uint64_t value = first & 0x3f;\n    for (int i = 1; i < len; ++i) {')
s = s.replace('for (auto& e : fs::directory_iterator(root)) if (e.is_regular_file() && e.path().extension() == ".jsonl") paths.push_back(e.path());', 'for (auto& e : fs::recursive_directory_iterator(root)) if (e.is_regular_file() && e.path().extension() == ".jsonl") paths.push_back(e.path());')
s = s.replace('for (auto& e : fs::directory_iterator(caproot)) if (e.is_regular_file() && e.path().extension() == ".qcap") caps.push_back(e.path());', 'for (auto& e : fs::recursive_directory_iterator(caproot)) if (e.is_regular_file() && e.path().extension() == ".qcap") caps.push_back(e.path());')
s = s.replace('static map<string, string> read_conf(const string& path) {', '''static uint64_t reconstruct_pn(uint64_t truncated, uint8_t pn_len, uint64_t expected) {
    uint64_t window = 1ULL << (8 * pn_len);
    uint64_t half = window / 2;
    uint64_t mask = window - 1;
    uint64_t candidate = (expected & ~mask) | truncated;
    if (candidate + half <= expected) candidate += window;
    else if (candidate > expected + half && candidate >= window) candidate -= window;
    return candidate;
}

static string packet_key(const string& c, const string& d, int e, int k, uint64_t pn) {
    return c + "|" + d + "|" + to_string(e) + "|" + to_string(k) + "|" + to_string(pn);
}

static map<string, string> read_conf(const string& path) {''')
start = s.index('        auto events = parse_qlogs(qroot);')
end = s.index('        fs::remove_all(outdir);', start)
replacement = r'''        auto events = parse_qlogs(qroot);
        map<tuple<string,string,int>, uint64_t> largest;
        set<tuple<string,string,int>> have_largest;
        for (auto& p : packets) {
            auto space = make_tuple(p.connection, p.direction, (int)p.epoch);
            uint64_t expected = have_largest.count(space) ? largest[space] + 1 : 0;
            p.packet_number = reconstruct_pn(p.truncated_pn, p.pn_len, expected);
            if (!have_largest.count(space) || p.packet_number > largest[space]) {
                largest[space] = p.packet_number;
                have_largest.insert(space);
            }
        }
        map<string, size_t> timeline;
         set<string> accepted;
         for (auto& e : events) {
             string k = packet_key(e.connection, e.direction, e.epoch, e.key_phase, e.packet_number);
             if (!timeline.count(k)) timeline[k] = e.order;
             if (e.event == "received") accepted.insert(k);
         }
        vector<size_t> order(packets.size());
        for (size_t i = 0; i < packets.size(); ++i) order[i] = i;
        sort(order.begin(), order.end(), [&](size_t a, size_t b) {
            auto& pa = packets[a]; auto& pb = packets[b];
            string ka = packet_key(pa.connection, pa.direction, pa.epoch, pa.key_phase, pa.packet_number);
            string kb = packet_key(pb.connection, pb.direction, pb.epoch, pb.key_phase, pb.packet_number);
            size_t oa = timeline.count(ka) ? timeline[ka] : numeric_limits<size_t>::max();
            size_t ob = timeline.count(kb) ? timeline[kb] : numeric_limits<size_t>::max();
            return tie(oa, pa.connection, pa.direction, pa.epoch, pa.key_phase, pa.packet_number, a) < tie(ob, pb.connection, pb.direction, pb.epoch, pb.key_phase, pb.packet_number, b);
        });
        map<tuple<string,string,uint64_t>, StreamState> streams;
        set<string> seen;
        struct Trace { string c,d,decision; int e,k; uint64_t pn; size_t fc, bytes; };
        vector<Trace> trace;
        size_t replayed = 0, duplicates = 0;
        set<string> conns;
        for (size_t idx : order) {
            auto& p = packets[idx];
            conns.insert(p.connection);
            string pk = packet_key(p.connection, p.direction, p.epoch, p.key_phase, p.packet_number);
            size_t bytes = 0;
            for (auto& f : p.frames) bytes += f.data.size();
            if (!accepted.count(pk)) {
                trace.push_back({p.connection,p.direction,"skipped",p.epoch,p.key_phase,p.packet_number,p.frames.size(),bytes});
                continue;
            }
            if (seen.count(pk)) {
                ++duplicates;
                trace.push_back({p.connection,p.direction,"duplicate",p.epoch,p.key_phase,p.packet_number,p.frames.size(),bytes});
                continue;
            }
            seen.insert(pk);
            ++replayed;
            trace.push_back({p.connection,p.direction,"replayed",p.epoch,p.key_phase,p.packet_number,p.frames.size(),bytes});
            for (auto& f : p.frames) {
                auto& st = streams[make_tuple(p.connection, p.direction, f.stream_id)];
                auto it = st.segments.find(f.offset);
                if (it == st.segments.end()) st.segments.emplace(f.offset, f.data);
                else if (it->second != f.data) throw runtime_error("conflicting stream segment");
                if (f.fin) { st.has_fin = true; st.fin_offset = f.offset + f.data.size(); }
            }
        }
'''
s = s[:start] + replacement + s[end:]
s = s.replace('        sort(trace.begin(), trace.end(), [](const auto& a, const auto& b){ return tie(a.c,a.d,a.e,a.k,a.pn,a.decision) < tie(b.c,b.d,b.e,b.k,b.pn,b.decision); });\n', '')
p.write_text(s)
PY
/opt/quic-replay/scripts/build.sh
/opt/quic-replay/bin/quic-replay /etc/quic-replay/runtime.conf
