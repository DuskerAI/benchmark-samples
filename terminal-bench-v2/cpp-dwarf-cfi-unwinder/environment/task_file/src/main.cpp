#include <algorithm>
#include <cctype>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using u64 = std::uint64_t;

struct Reader {
    std::vector<unsigned char> b;
    size_t p = 0;
    explicit Reader(const std::string& path) {
        std::ifstream f(path, std::ios::binary);
        if (!f) throw std::runtime_error("open cfi");
        b.assign(std::istreambuf_iterator<char>(f), {});
    }
    explicit Reader(const std::vector<unsigned char>& bytes) : b(bytes) {}
    unsigned char u8() { if (p >= b.size()) throw std::runtime_error("eof"); return b[p++]; }
    u64 le(size_t n) { u64 v = 0; for (size_t i = 0; i < n; ++i) v |= u64(u8()) << (8 * i); return v; }
    u64 uleb() { u64 v = 0; int s = 0; while (true) { unsigned char c = u8(); v |= u64(c & 0x7f) << s; if (!(c & 0x80)) return v; s += 7; } }
    std::int64_t sleb() { return static_cast<std::int64_t>(uleb()); }
};

struct Rule { int kind = 0; int reg = -1; std::int64_t off = 0; };
struct State { int cfa_reg = -1; std::int64_t cfa_off = 0; std::map<int, Rule> rules; };
struct Cie { u64 id = 0; u64 code = 1; std::int64_t data = -8; int ret = 16; State init; };
struct Fde { u64 cie = 0; u64 start = 0; u64 range = 0; std::vector<unsigned char> ins; };
struct Table { std::map<u64, Cie> cies; std::vector<Fde> fdes; };
struct Thread { std::string id; u64 start = 0; std::map<int, u64> regs; std::map<u64, u64> mem; };
struct Record { std::string cfi; u64 base = 0; std::vector<Thread> threads; };

std::string hx(u64 v) { std::ostringstream o; o << "0x" << std::hex << std::nouppercase << v; return o.str(); }

u64 parse_hex(const std::string& s) { return std::stoull(s, nullptr, 0); }

void run_ins(const std::vector<unsigned char>& ins, const Cie& cie, State& st, u64 target) {
    Reader r(ins);
    u64 loc = 0;
    while (r.p < r.b.size() && loc <= target) {
        unsigned char op = r.u8();
        if ((op & 0xc0) == 0x40) loc += (op & 0x3f) * cie.code;
        else if ((op & 0xc0) == 0x80) st.rules[op & 0x3f] = {1, -1, static_cast<std::int64_t>(r.uleb())};
        else if ((op & 0xc0) == 0xc0) st.rules.erase(op & 0x3f);
        else if (op == 0x0c) { st.cfa_reg = static_cast<int>(r.uleb()); st.cfa_off = static_cast<std::int64_t>(r.uleb()); }
        else if (op == 0x0d) st.cfa_reg = static_cast<int>(r.uleb());
        else if (op == 0x0e) st.cfa_off = static_cast<std::int64_t>(r.uleb());
        else if (op == 0x02) loc += r.u8() * cie.code;
        else if (op == 0x03) loc += r.le(2) * cie.code;
        else if (op == 0x08) st.rules[static_cast<int>(r.uleb())] = {3, -1, 0};
        else if (op == 0x09) { int a = static_cast<int>(r.uleb()); int b = static_cast<int>(r.uleb()); st.rules[a] = {2, b, 0}; }
        else if (op == 0x00) {}
        else break;
    }
}

Table load_table(const std::string& path, u64) {
    Reader r(path);
    if (r.le(4) != 0x49464354 || r.u8() != 1) throw std::runtime_error("bad cfi");
    u64 nc = r.le(2), nf = r.le(2);
    Table t;
    for (u64 i = 0; i < nc; ++i) {
        Cie c; c.id = r.le(4); c.code = r.uleb(); c.data = r.sleb(); c.ret = static_cast<int>(r.uleb());
        u64 al = r.uleb(); while (al--) r.u8();
        u64 il = r.le(2); std::vector<unsigned char> ins; while (il--) ins.push_back(r.u8());
        run_ins(ins, c, c.init, 0);
        t.cies[c.id] = c;
    }
    for (u64 i = 0; i < nf; ++i) {
        Fde f; f.cie = r.le(4); unsigned char enc = r.u8();
        if (enc != 0x00) { f.start = r.le(4); f.range = r.le(4); }
        else { f.start = r.le(8); f.range = r.le(8); }
        u64 il = r.le(2); while (il--) f.ins.push_back(r.u8());
        t.fdes.push_back(f);
    }
    return t;
}

Record load_record(const std::string& path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("open record");
    Record rec; Thread* cur = nullptr; std::string line;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        std::istringstream s(line); std::string k; s >> k;
        if (k == "CFI") s >> rec.cfi;
        else if (k == "BASE") { std::string v; s >> v; rec.base = parse_hex(v); }
        else if (k == "THREAD") { std::string id; s >> id; rec.threads.push_back(Thread{}); cur = &rec.threads.back(); cur->id = id; }
        else if (k == "START") { std::string v; s >> v; cur->start = parse_hex(v); }
        else if (k == "REG") { int reg; std::string v; s >> reg >> v; cur->regs[reg] = parse_hex(v); }
        else if (k == "MEM") { std::string a, v; s >> a >> v; cur->mem[parse_hex(a)] = parse_hex(v); }
        else if (k == "END") cur = nullptr;
    }
    return rec;
}

const Fde* find_fde(const Table& t, u64 pc) {
    for (const auto& f : t.fdes) if (pc >= f.start && pc <= f.start + f.range) return &f;
    return nullptr;
}

struct Frame { int index; u64 pc; std::optional<u64> cfa; std::optional<u64> ra; std::string status; };

std::vector<Frame> unwind(const Table& t, const Thread& th) {
    std::vector<Frame> out; u64 pc = th.start;
    for (int depth = 0; depth < 32; ++depth) {
        Frame fr{depth, pc, {}, {}, "ok"};
        const Fde* f = find_fde(t, pc);
        if (!f) { fr.status = "no_fde"; out.push_back(fr); break; }
        const Cie& c = t.cies.at(f->cie);
        State st = c.init;
        run_ins(f->ins, c, st, pc - f->start);
        auto reg = th.regs.find(st.cfa_reg);
        if (reg == th.regs.end()) { fr.status = "missing_register"; out.push_back(fr); break; }
        u64 cfa = reg->second + st.cfa_off; fr.cfa = cfa;
        auto rr = st.rules.find(c.ret);
        if (rr == st.rules.end()) { fr.status = "no_return"; out.push_back(fr); break; }
        u64 ra = 0;
        if (rr->second.kind == 1) {
            auto m = th.mem.find(cfa + rr->second.off);
            if (m == th.mem.end()) { fr.status = "missing_memory"; out.push_back(fr); break; }
            ra = m->second;
        } else if (rr->second.kind == 2) {
            auto r = th.regs.find(rr->second.reg);
            if (r == th.regs.end()) { fr.status = "missing_register"; out.push_back(fr); break; }
            ra = r->second;
        } else if (rr->second.kind == 3) {
            auto r = th.regs.find(c.ret);
            if (r == th.regs.end()) { fr.status = "missing_register"; out.push_back(fr); break; }
            ra = r->second;
        }
        fr.ra = ra;
        if (ra == 0) { fr.status = "root"; out.push_back(fr); break; }
        out.push_back(fr); pc = ra;
    }
    return out;
}

void write_json(const std::string& in, const std::string& outp, const std::vector<std::pair<std::string, std::vector<Frame>>>& all) {
    std::ofstream o(outp);
    int complete = 0, count = 0;
    for (const auto& p : all) { count += static_cast<int>(p.second.size()); if (!p.second.empty() && p.second.back().status == "root") ++complete; }
    o << "{\n  \"input\": \"" << in << "\",\n  \"threads\": [\n";
    for (size_t i = 0; i < all.size(); ++i) {
        o << "    {\"id\": \"" << all[i].first << "\", \"frames\": [";
        for (size_t j = 0; j < all[i].second.size(); ++j) {
            const auto& f = all[i].second[j];
            if (j) o << ", ";
            o << "{\"index\": " << f.index << ", \"pc\": \"" << hx(f.pc) << "\", \"cfa\": ";
            if (f.cfa) o << "\"" << hx(*f.cfa) << "\""; else o << "null";
            o << ", \"return_address\": ";
            if (f.ra) o << "\"" << hx(*f.ra) << "\""; else o << "null";
            o << ", \"status\": \"" << f.status << "\"}";
        }
        o << "]}" << (i + 1 == all.size() ? "\n" : ",\n");
    }
    o << "  ],\n  \"summary\": {\"thread_count\": " << all.size() << ", \"complete_threads\": " << complete << ", \"frame_count\": " << count << "}\n}\n";
}

int main(int argc, char** argv) {
    try {
        if (argc != 3) return 2;
        Record rec = load_record(argv[1]);
        Table t = load_table(rec.cfi, rec.base);
        std::vector<std::pair<std::string, std::vector<Frame>>> all;
        for (const auto& th : rec.threads) all.push_back({th.id, unwind(t, th)});
        write_json(argv[1], argv[2], all);
        return 0;
    } catch (const std::exception& e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}
