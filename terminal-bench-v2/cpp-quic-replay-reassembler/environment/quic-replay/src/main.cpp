#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

using namespace std;
namespace fs = std::filesystem;

struct Frame {
    uint64_t stream_id{};
    uint64_t offset{};
    bool fin{};
    vector<uint8_t> data;
};

struct Packet {
    string connection;
    string direction;
    uint8_t epoch{};
    uint8_t key_phase{};
    uint8_t pn_len{};
    uint64_t truncated_pn{};
    uint64_t packet_number{};
    vector<Frame> frames;
};

struct Event {
    string connection;
    string direction;
    uint8_t epoch{};
    uint8_t key_phase{};
    uint64_t packet_number{};
    string event;
    size_t order{};
};

struct StreamState {
    map<uint64_t, vector<uint8_t>> segments;
    bool has_fin = false;
    uint64_t fin_offset = 0;
};

static vector<uint8_t> read_bytes(const fs::path& p) {
    ifstream f(p, ios::binary);
    if (!f) throw runtime_error("cannot open " + p.string());
    return vector<uint8_t>((istreambuf_iterator<char>(f)), istreambuf_iterator<char>());
}

static uint64_t read_varint(const vector<uint8_t>& b, size_t& pos, size_t end) {
    if (pos >= end) throw runtime_error("short varint");
    uint8_t first = b[pos++];
    int prefix = first >> 6;
    int len = 1 << prefix;
    uint64_t value = first;
    for (int i = 1; i < len; ++i) {
        if (pos >= end) throw runtime_error("short varint");
        value = (value << 8) | b[pos++];
    }
    return value;
}

static vector<Frame> parse_frames(const vector<uint8_t>& b, size_t pos, size_t end) {
    vector<Frame> frames;
    while (pos < end) {
        uint64_t type = read_varint(b, pos, end);
        if (type == 0) continue;
        if ((type & 0xf8) != 0x08) throw runtime_error("unsupported frame");
        bool has_off = (type & 0x04) != 0;
        bool has_len = (type & 0x02) != 0;
        bool fin = (type & 0x01) != 0;
        Frame fr;
        fr.stream_id = read_varint(b, pos, end);
        fr.offset = has_off ? read_varint(b, pos, end) : 0;
        uint64_t len = has_len ? read_varint(b, pos, end) : (end - pos);
        if (pos + len > end) throw runtime_error("short stream data");
        fr.fin = fin;
        fr.data.assign(b.begin() + pos, b.begin() + pos + len);
        pos += len;
        frames.push_back(fr);
    }
    return frames;
}

static vector<Packet> parse_capture(const fs::path& p) {
    vector<uint8_t> b = read_bytes(p);
    if (b.size() < 4 || string((char*)b.data(), (char*)b.data() + 4) != "QRC1") throw runtime_error("bad capture");
    vector<Packet> out;
    size_t pos = 4;
    while (pos < b.size()) {
        if (pos + 2 > b.size() || b[pos++] != 'Q' || b[pos++] != 'R') throw runtime_error("bad record");
        uint64_t body_len = read_varint(b, pos, b.size());
        size_t end = pos + body_len;
        if (end > b.size()) throw runtime_error("short record");
        Packet pkt;
        uint8_t clen = b[pos++];
        pkt.connection.assign((char*)&b[pos], (char*)&b[pos] + clen);
        pos += clen;
        uint8_t dir = b[pos++];
        pkt.direction = dir == 0 ? "client" : "server";
        pkt.epoch = b[pos++];
        pkt.key_phase = b[pos++];
        pkt.pn_len = b[pos++];
        for (uint8_t i = 0; i < pkt.pn_len; ++i) pkt.truncated_pn = (pkt.truncated_pn << 8) | b[pos++];
        pkt.packet_number = pkt.truncated_pn;
        pkt.frames = parse_frames(b, pos, end);
        pos = end;
        out.push_back(pkt);
    }
    return out;
}

static string get_string(const string& s, const string& key) {
    regex r("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"");
    smatch m;
    if (!regex_search(s, m, r)) throw runtime_error("missing string " + key);
    return m[1];
}

static uint64_t get_uint(const string& s, const string& key) {
    regex r("\\\"" + key + "\\\"\\s*:\\s*([0-9]+)");
    smatch m;
    if (!regex_search(s, m, r)) throw runtime_error("missing uint " + key);
    return stoull(m[1]);
}

static vector<Event> parse_qlogs(const fs::path& root) {
    vector<fs::path> paths;
    for (auto& e : fs::directory_iterator(root)) if (e.is_regular_file() && e.path().extension() == ".jsonl") paths.push_back(e.path());
    sort(paths.begin(), paths.end());
    vector<Event> events;
    string line;
    size_t order = 0;
    for (auto& p : paths) {
        ifstream f(p);
        while (getline(f, line)) {
            if (line.empty()) continue;
            Event e;
            e.connection = get_string(line, "connection");
            e.direction = get_string(line, "direction");
            e.epoch = (uint8_t)get_uint(line, "epoch");
            e.key_phase = (uint8_t)get_uint(line, "key_phase");
            e.packet_number = get_uint(line, "packet_number");
            e.event = get_string(line, "event");
            e.order = order++;
            events.push_back(e);
        }
    }
    return events;
}

static map<string, string> read_conf(const string& path) {
    ifstream f(path);
    if (!f) throw runtime_error("cannot open config");
    map<string, string> c;
    string line;
    while (getline(f, line)) {
        auto eq = line.find('=');
        if (eq != string::npos) c[line.substr(0, eq)] = line.substr(eq + 1);
    }
    return c;
}

static string hex_data(const vector<uint8_t>& v) {
    stringstream ss;
    ss << hex << setfill('0');
    for (auto x : v) ss << setw(2) << (unsigned)x;
    return ss.str();
}

static bool complete_stream(const StreamState& st, vector<uint8_t>& out, vector<pair<uint64_t,uint64_t>>& gaps) {
    if (!st.has_fin) return false;
    uint64_t cur = 0;
    for (auto& [off, data] : st.segments) {
        if (off > cur) gaps.push_back({cur, off});
        if (off >= cur) {
            out.insert(out.end(), data.begin(), data.end());
            cur = off + data.size();
        } else if (off + data.size() > cur) {
            size_t skip = cur - off;
            out.insert(out.end(), data.begin() + skip, data.end());
            cur = off + data.size();
        }
    }
    if (cur < st.fin_offset) gaps.push_back({cur, st.fin_offset});
    return gaps.empty();
}

int main(int argc, char** argv) {
    try {
        if (argc != 2) throw runtime_error("usage: quic-replay <runtime.conf>");
        auto conf = read_conf(argv[1]);
        fs::path caproot = conf.at("capture_root");
        fs::path qroot = conf.at("qlog_root");
        fs::path outdir = conf.at("output_dir");
        vector<fs::path> caps;
        for (auto& e : fs::directory_iterator(caproot)) if (e.is_regular_file() && e.path().extension() == ".qcap") caps.push_back(e.path());
        sort(caps.begin(), caps.end());
        vector<Packet> packets;
        for (auto& p : caps) {
            auto v = parse_capture(p);
            packets.insert(packets.end(), v.begin(), v.end());
        }
        auto events = parse_qlogs(qroot);
        map<string, bool> accepted;
        for (auto& e : events) {
            string k = e.connection + "|" + e.direction + "|" + to_string(e.epoch) + "|" + to_string(e.packet_number & 255);
            accepted[k] = e.event == "received";
        }
        map<tuple<string,string,uint64_t>, StreamState> streams;
        set<string> seen;
        struct Trace { string c,d,decision; int e,k; uint64_t pn; size_t fc, bytes; };
        vector<Trace> trace;
        size_t replayed = 0, duplicates = 0;
        set<string> conns;
        for (auto& p : packets) {
            conns.insert(p.connection);
            string qk = p.connection + "|" + p.direction + "|" + to_string(p.epoch) + "|" + to_string(p.packet_number & 255);
            string pk = p.connection + "|" + p.direction + "|" + to_string(p.epoch) + "|" + to_string(p.packet_number);
            size_t bytes = 0;
            for (auto& f : p.frames) bytes += f.data.size();
            if (!accepted[qk]) {
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
                auto& st = streams[make_tuple(p.connection, string("both"), f.stream_id)];
                st.segments.emplace(f.offset, f.data);
                if (f.fin) { st.has_fin = true; st.fin_offset = f.offset + f.data.size(); }
            }
        }
        fs::remove_all(outdir);
        fs::create_directories(outdir);
        sort(trace.begin(), trace.end(), [](const auto& a, const auto& b){ return tie(a.c,a.d,a.e,a.k,a.pn,a.decision) < tie(b.c,b.d,b.e,b.k,b.pn,b.decision); });
        ofstream pt(outdir / "packet_trace.csv");
        pt << "connection,direction,epoch,key_phase,packet_number,decision,frame_count,payload_bytes\n";
        for (auto& t : trace) pt << t.c << ',' << t.d << ',' << t.e << ',' << t.k << ',' << t.pn << ',' << t.decision << ',' << t.fc << ',' << t.bytes << "\n";
        struct Row { string c,d,h; uint64_t sid,fin; };
        vector<Row> rows;
        struct Gap { string c,d; uint64_t sid,s,e; };
        vector<Gap> gaps;
        size_t bytes_reassembled = 0, complete = 0, incomplete = 0;
        for (auto& [key, st] : streams) {
            vector<uint8_t> data;
            vector<pair<uint64_t,uint64_t>> gs;
            auto [c,d,sid] = key;
            if (complete_stream(st, data, gs)) {
                ++complete;
                bytes_reassembled += data.size();
                rows.push_back({c,d,hex_data(data),sid,st.fin_offset});
            } else {
                ++incomplete;
                for (auto& g : gs) gaps.push_back({c,d,sid,g.first,g.second});
            }
        }
        sort(rows.begin(), rows.end(), [](const Row& a, const Row& b){ return tie(a.c,a.d,a.sid) < tie(b.c,b.d,b.sid); });
        ofstream sj(outdir / "streams.jsonl");
        for (auto& r : rows) sj << "{\"connection\":\"" << r.c << "\",\"direction\":\"" << r.d << "\",\"stream_id\":" << r.sid << ",\"fin_offset\":" << r.fin << ",\"payload_hex\":\"" << r.h << "\"}\n";
        sort(gaps.begin(), gaps.end(), [](const Gap& a, const Gap& b){ return tie(a.c,a.d,a.sid,a.s) < tie(b.c,b.d,b.sid,b.s); });
        ofstream gf(outdir / "gaps.csv");
        gf << "connection,direction,stream_id,start,end,reason\n";
        for (auto& g : gaps) gf << g.c << ',' << g.d << ',' << g.sid << ',' << g.s << ',' << g.e << ",missing\n";
        ofstream sum(outdir / "replay_summary.json");
        sum << "{\"connections\":" << conns.size() << ",\"packets_seen\":" << packets.size() << ",\"packets_replayed\":" << replayed << ",\"duplicates\":" << duplicates << ",\"streams_complete\":" << complete << ",\"streams_incomplete\":" << incomplete << ",\"bytes_reassembled\":" << bytes_reassembled << "}\n";
        return 0;
    } catch (const exception& e) {
        cerr << e.what() << "\n";
        return 1;
    }
}
