#!/bin/bash
set -euo pipefail

cd /app/task_file
cat > src/main.cpp <<'CPP'
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
using u64 = std::uint64_t;
using i64 = std::int64_t;
struct Reader {
    std::vector<unsigned char> b; size_t p = 0;
    explicit Reader(const std::string& path) { std::ifstream f(path, std::ios::binary); if (!f) throw std::runtime_error("open cfi"); b.assign(std::istreambuf_iterator<char>(f), {}); }
    explicit Reader(const std::vector<unsigned char>& bytes) : b(bytes) {}
    unsigned char u8() { if (p >= b.size()) throw std::runtime_error("eof"); return b[p++]; }
    u64 le(size_t n) { u64 v = 0; for (size_t i = 0; i < n; ++i) v |= u64(u8()) << (8 * i); return v; }
    i64 sle_fixed(size_t n) { u64 raw = le(n); u64 bit = u64{1} << (n * 8 - 1); if (raw & bit) raw |= (~u64{0}) << (n * 8); return static_cast<i64>(raw); }
    u64 uleb() { u64 v = 0; int s = 0; while (true) { unsigned char c = u8(); v |= u64(c & 0x7f) << s; if ((c & 0x80) == 0) return v; s += 7; if (s > 63) throw std::runtime_error("uleb"); } }
    i64 sleb() { i64 v = 0; int s = 0; unsigned char c = 0; while (true) { c = u8(); v |= i64(c & 0x7f) << s; s += 7; if ((c & 0x80) == 0) break; if (s > 63) throw std::runtime_error("sleb"); } if ((c & 0x40) != 0 && s < 64) v |= static_cast<i64>((~u64{0}) << s); return v; }
};
struct Rule { enum Kind { Undefined, Offset, Register, Same, ValOffset } kind = Undefined; int reg = -1; i64 off = 0; };
struct State { int cfa_reg = -1; i64 cfa_off = 0; std::map<int, Rule> rules; };
struct Cie { u64 id = 0; u64 code = 1; i64 data = -8; int ret = 16; State init; };
struct Fde { u64 cie = 0; u64 start = 0; u64 range = 0; std::vector<unsigned char> ins; };
struct Table { std::map<u64, Cie> cies; std::vector<Fde> fdes; };
struct Thread { std::string id; u64 start = 0; std::map<int, u64> regs; std::map<u64, u64> mem; };
struct Record { std::string cfi; u64 base = 0; std::vector<Thread> threads; };
struct Frame { int index = 0; u64 pc = 0; std::optional<u64> cfa; std::optional<u64> ra; std::string status; };
std::string hx(u64 v) { std::ostringstream o; o << "0x" << std::hex << std::nouppercase << v; return o.str(); }
std::string esc(const std::string& s) { std::ostringstream o; for (char ch : s) { if (ch == '\\' || ch == '"') o << '\\' << ch; else if (ch == '\n') o << "\\n"; else o << ch; } return o.str(); }
u64 parse_hex(const std::string& s) { return std::stoull(s, nullptr, 0); }
u64 read_encoded(Reader& r, unsigned char enc, u64 base) {
    size_t field = r.p; unsigned char fmt = enc & 0x0f; unsigned char app = enc & 0x70; i64 raw = 0;
    if (fmt == 0x00) raw = static_cast<i64>(r.le(8)); else if (fmt == 0x01) raw = static_cast<i64>(r.uleb()); else if (fmt == 0x02) raw = static_cast<i64>(r.le(2)); else if (fmt == 0x03) raw = static_cast<i64>(r.le(4)); else if (fmt == 0x04) raw = static_cast<i64>(r.le(8)); else if (fmt == 0x09) raw = r.sleb(); else if (fmt == 0x0b) raw = r.sle_fixed(4); else if (fmt == 0x0c) raw = r.sle_fixed(8); else throw std::runtime_error("encoding");
    i64 value = raw; if (app == 0x10) value += static_cast<i64>(field); else if (app == 0x30) value += static_cast<i64>(base); else if (app != 0x00) throw std::runtime_error("relative"); return static_cast<u64>(value);
}
void apply_stream(const std::vector<unsigned char>& ins, const Cie& cie, State& st, std::optional<u64> target) {
    Reader r(ins); u64 loc = 0; std::vector<State> stack;
    while (r.p < r.b.size()) {
        unsigned char op = r.u8();
        auto advance = [&](u64 delta) { u64 next = loc + delta * cie.code; if (target && next > *target) { r.p = r.b.size(); return; } loc = next; };
        if ((op & 0xc0) == 0x40) advance(op & 0x3f); else if ((op & 0xc0) == 0x80) st.rules[op & 0x3f] = {Rule::Offset, -1, static_cast<i64>(r.uleb()) * cie.data}; else if ((op & 0xc0) == 0xc0) st.rules.erase(op & 0x3f); else if (op == 0x00) {} else if (op == 0x02) advance(r.u8()); else if (op == 0x03) advance(r.le(2)); else if (op == 0x04) advance(r.le(4)); else if (op == 0x05) { int reg = static_cast<int>(r.uleb()); st.rules[reg] = {Rule::Offset, -1, static_cast<i64>(r.uleb()) * cie.data}; } else if (op == 0x06) st.rules.erase(static_cast<int>(r.uleb())); else if (op == 0x08) { int reg = static_cast<int>(r.uleb()); st.rules[reg] = {Rule::Same, reg, 0}; } else if (op == 0x09) { int dst = static_cast<int>(r.uleb()); int src = static_cast<int>(r.uleb()); st.rules[dst] = {Rule::Register, src, 0}; } else if (op == 0x0a) stack.push_back(st); else if (op == 0x0b) { if (stack.empty()) throw std::runtime_error("state"); st = stack.back(); stack.pop_back(); } else if (op == 0x0c) { st.cfa_reg = static_cast<int>(r.uleb()); st.cfa_off = static_cast<i64>(r.uleb()); } else if (op == 0x0d) st.cfa_reg = static_cast<int>(r.uleb()); else if (op == 0x0e) st.cfa_off = static_cast<i64>(r.uleb()); else if (op == 0x11) { int reg = static_cast<int>(r.uleb()); st.rules[reg] = {Rule::Offset, -1, r.sleb() * cie.data}; } else if (op == 0x12) { st.cfa_reg = static_cast<int>(r.uleb()); st.cfa_off = r.sleb() * cie.data; } else if (op == 0x13) st.cfa_off = r.sleb() * cie.data; else if (op == 0x14) { int reg = static_cast<int>(r.uleb()); st.rules[reg] = {Rule::ValOffset, -1, static_cast<i64>(r.uleb()) * cie.data}; } else if (op == 0x15) { int reg = static_cast<int>(r.uleb()); st.rules[reg] = {Rule::ValOffset, -1, r.sleb() * cie.data}; } else throw std::runtime_error("opcode");
    }
}
Table load_table(const std::string& path, u64 base) {
    Reader r(path); if (r.le(4) != 0x49464354 || r.u8() != 1) throw std::runtime_error("bad cfi"); u64 nc = r.le(2), nf = r.le(2); Table t;
    for (u64 i = 0; i < nc; ++i) { Cie c; c.id = r.le(4); c.code = r.uleb(); c.data = r.sleb(); c.ret = static_cast<int>(r.uleb()); u64 aug = r.uleb(); while (aug--) r.u8(); u64 ilen = r.le(2); std::vector<unsigned char> ins; while (ilen--) ins.push_back(r.u8()); apply_stream(ins, c, c.init, std::nullopt); t.cies[c.id] = c; }
    for (u64 i = 0; i < nf; ++i) { Fde f; f.cie = r.le(4); unsigned char enc = r.u8(); f.start = read_encoded(r, enc, base); f.range = read_encoded(r, enc & 0x0f, base); u64 ilen = r.le(2); while (ilen--) f.ins.push_back(r.u8()); t.fdes.push_back(f); }
    return t;
}
Record load_record(const std::string& path) {
    std::ifstream f(path); if (!f) throw std::runtime_error("open record"); Record rec; Thread* cur = nullptr; std::string line;
    while (std::getline(f, line)) { if (line.empty()) continue; std::istringstream s(line); std::string k; s >> k; if (k == "CFI") s >> rec.cfi; else if (k == "BASE") { std::string v; s >> v; rec.base = parse_hex(v); } else if (k == "THREAD") { std::string id; s >> id; rec.threads.push_back(Thread{}); cur = &rec.threads.back(); cur->id = id; } else if (k == "START") { std::string v; s >> v; if (!cur) throw std::runtime_error("record"); cur->start = parse_hex(v); } else if (k == "REG") { int reg; std::string v; s >> reg >> v; if (!cur) throw std::runtime_error("record"); cur->regs[reg] = parse_hex(v); } else if (k == "MEM") { std::string a, v; s >> a >> v; if (!cur) throw std::runtime_error("record"); cur->mem[parse_hex(a)] = parse_hex(v); } else if (k == "END") cur = nullptr; }
    return rec;
}
const Fde* find_fde(const Table& t, u64 pc) { const Fde* best = nullptr; for (const auto& f : t.fdes) if (pc >= f.start && pc < f.start + f.range) if (!best || f.start > best->start) best = &f; return best; }
std::optional<u64> recover_rule(const Rule& rule, u64 cfa, const std::map<int, u64>& regs, const std::map<u64, u64>& mem) { if (rule.kind == Rule::Offset) { auto it = mem.find(cfa + rule.off); if (it == mem.end()) return std::nullopt; return it->second; } if (rule.kind == Rule::Register || rule.kind == Rule::Same) { auto it = regs.find(rule.reg); if (it == regs.end()) return std::nullopt; return it->second; } if (rule.kind == Rule::ValOffset) return cfa + rule.off; return std::nullopt; }
std::vector<Frame> unwind(const Table& t, const Thread& th) {
    std::vector<Frame> out;
    std::map<int, u64> regs = th.regs;
    std::set<u64> seen;
    u64 pc = th.start;
    bool first = true;
    for (int depth = 0; depth < 32; ++depth) {
        Frame fr;
        fr.index = depth;
        fr.pc = pc;
        fr.status = "ok";
        seen.insert(pc);
        u64 lookup = first ? pc : pc - 1;
        first = false;
        const Fde* f = find_fde(t, lookup);
        if (!f) { fr.status = "no_fde"; out.push_back(fr); break; }
        auto cie_it = t.cies.find(f->cie);
        if (cie_it == t.cies.end()) { fr.status = "no_fde"; out.push_back(fr); break; }
        const Cie& c = cie_it->second;
        State st = c.init;
        apply_stream(f->ins, c, st, lookup - f->start);
        auto cfa_reg = regs.find(st.cfa_reg);
        if (cfa_reg == regs.end()) { fr.status = "missing_register"; out.push_back(fr); break; }
        u64 cfa = cfa_reg->second + st.cfa_off;
        fr.cfa = cfa;
        auto rr = st.rules.find(c.ret);
        if (rr == st.rules.end()) { fr.status = "no_return"; out.push_back(fr); break; }
        std::optional<u64> ra;
        if (rr->second.kind == Rule::Same) {
            auto it = regs.find(c.ret);
            if (it != regs.end()) ra = it->second;
        } else {
            ra = recover_rule(rr->second, cfa, regs, th.mem);
        }
        if (!ra) { fr.status = rr->second.kind == Rule::Offset ? "missing_memory" : "missing_register"; out.push_back(fr); break; }
        fr.ra = *ra;
        if (*ra == 0) { fr.status = "root"; out.push_back(fr); break; }
        out.push_back(fr);
        if (seen.count(*ra)) {
            Frame cyc;
            cyc.index = depth + 1;
            cyc.pc = *ra;
            cyc.status = "cycle";
            out.push_back(cyc);
            break;
        }
        regs[st.cfa_reg] = cfa;
        regs[c.ret] = *ra;
        for (const auto& item : st.rules) {
            int regno = item.first;
            const Rule& rule = item.second;
            if (regno == c.ret) continue;
            auto val = recover_rule(rule, cfa, regs, th.mem);
            if (val) regs[regno] = *val;
        }
        pc = *ra;
    }
    return out;
}
void write_json(const std::string& input, const std::string& outp, const std::vector<std::pair<std::string, std::vector<Frame>>>& all) {
    std::ofstream o(outp); if (!o) throw std::runtime_error("open output"); int complete = 0; int frames = 0; for (const auto& item : all) { frames += static_cast<int>(item.second.size()); if (!item.second.empty() && item.second.back().status == "root") ++complete; }
    o << "{\n  \"input\": \"" << esc(input) << "\",\n  \"threads\": [\n";
    for (size_t i = 0; i < all.size(); ++i) { o << "    {\"id\": \"" << esc(all[i].first) << "\", \"frames\": ["; for (size_t j = 0; j < all[i].second.size(); ++j) { const Frame& f = all[i].second[j]; if (j) o << ", "; o << "{\"index\": " << f.index << ", \"pc\": \"" << hx(f.pc) << "\", \"cfa\": "; if (f.cfa) o << "\"" << hx(*f.cfa) << "\""; else o << "null"; o << ", \"return_address\": "; if (f.ra) o << "\"" << hx(*f.ra) << "\""; else o << "null"; o << ", \"status\": \"" << f.status << "\"}"; } o << "]}" << (i + 1 == all.size() ? "\n" : ",\n"); }
    o << "  ],\n  \"summary\": {\"thread_count\": " << all.size() << ", \"complete_threads\": " << complete << ", \"frame_count\": " << frames << "}\n}\n";
}
int main(int argc, char** argv) { try { if (argc != 3) return 2; Record rec = load_record(argv[1]); Table tab = load_table(rec.cfi, rec.base); std::vector<std::pair<std::string, std::vector<Frame>>> all; for (const auto& th : rec.threads) all.push_back({th.id, unwind(tab, th)}); write_json(argv[1], argv[2], all); return 0; } catch (const std::exception& e) { std::cerr << e.what() << "\n"; return 1; } }
CPP
make clean all
make test
