#!/bin/bash
set -euo pipefail

cd /app

cat > /app/relplan/src/main.cpp <<'CPP'
#include <algorithm>
#include <cstdint>
#include <functional>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using i64 = long long;
using u64 = unsigned long long;

struct Section { std::string name, group; u64 size = 0; u64 align = 1; };
struct Def { std::string name, section, binding; u64 offset = 0; };
struct Alias { std::string name, target, binding; };
struct Common { std::string name; u64 size = 0; u64 align = 1; };
struct Need { std::string name, strength; };
struct Reloc { std::string section, type, symbol; u64 offset = 0; i64 addend = 0; };
struct Got { std::string symbol; };
struct Obj { std::string name; std::vector<Section> sections; std::vector<Def> defs; std::vector<Alias> aliases; std::vector<Common> commons; std::vector<Need> needs; std::vector<Reloc> relocs; std::vector<Got> gots; };
struct Archive { std::string name; std::vector<Obj> members; };
struct Item { bool archive = false; bool group = false; Obj object; Archive ar; std::vector<Archive> group_archives; };
struct Inc { Obj obj; std::string label; size_t order = 0; };
struct Sym { std::string name, binding, section, defined_by; u64 address = 0; u64 size = 0; };
struct SectionOut { std::string name; u64 address = 0; u64 size = 0; u64 alignment = 16; };

static u64 parse_u(const std::string& s) { return std::stoull(s, nullptr, 0); }
static i64 parse_i(const std::string& s) { return std::stoll(s, nullptr, 0); }
static u64 align_to(u64 x, u64 a) { if (a <= 1) return x; return ((x + a - 1) / a) * a; }

static std::string trim_comment(const std::string& line) {
    auto p = line.find('#');
    return p == std::string::npos ? line : line.substr(0, p);
}

static bool parse_obj_line(Obj& obj, const std::string& op, std::istringstream& iss) {
    if (op == "section") {
        Section s; std::string sz, al; iss >> s.name >> sz >> al; if (!(iss >> s.group)) s.group = "-"; s.size = parse_u(sz); s.align = parse_u(al); obj.sections.push_back(s); return true;
    }
    if (op == "got") {
        Got g; iss >> g.symbol; obj.gots.push_back(g); return true;
    }
    if (op == "define") {
        Def d; std::string off; iss >> d.name >> d.section >> off >> d.binding; d.offset = parse_u(off); obj.defs.push_back(d); return true;
    }
    if (op == "alias") {
        Alias a; iss >> a.name >> a.target >> a.binding; obj.aliases.push_back(a); return true;
    }
    if (op == "common") {
        Common c; std::string sz, al; iss >> c.name >> sz >> al; c.size = parse_u(sz); c.align = parse_u(al); obj.commons.push_back(c); return true;
    }
    if (op == "need") {
        Need n; iss >> n.name >> n.strength; obj.needs.push_back(n); return true;
    }
    if (op == "reloc") {
        Reloc r; std::string off, add; iss >> r.section >> off >> r.type >> r.symbol >> add; r.offset = parse_u(off); r.addend = parse_i(add); obj.relocs.push_back(r); return true;
    }
    return false;
}

static Archive parse_archive_body(std::istream& in, const std::string& name) {
    Archive ar; ar.name = name;
    std::string line;
    while (std::getline(in, line)) {
        std::istringstream sub(trim_comment(line));
        std::string op;
        if (!(sub >> op)) continue;
        if (op == "endarchive") break;
        if (op == "member") {
            Obj mem; sub >> mem.name;
            while (std::getline(in, line)) {
                std::istringstream ms(trim_comment(line));
                if (!(ms >> op)) continue;
                if (op == "endmember") break;
                parse_obj_line(mem, op, ms);
            }
            ar.members.push_back(mem);
        }
    }
    return ar;
}

static std::vector<Item> parse_manifest(const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open manifest");
    std::vector<Item> items;
    std::string line;
    while (std::getline(in, line)) {
        std::istringstream iss(trim_comment(line));
        std::string op;
        if (!(iss >> op)) continue;
        if (op == "object") {
            Item it; iss >> it.object.name;
            while (std::getline(in, line)) {
                std::istringstream sub(trim_comment(line));
                if (!(sub >> op)) continue;
                if (op == "end") break;
                parse_obj_line(it.object, op, sub);
            }
            items.push_back(it);
        } else if (op == "archive") {
            Item it; it.archive = true; iss >> it.ar.name;
            it.ar = parse_archive_body(in, it.ar.name);
            items.push_back(it);
        } else if (op == "group") {
            Item it; it.group = true;
            while (std::getline(in, line)) {
                std::istringstream sub(trim_comment(line));
                if (!(sub >> op)) continue;
                if (op == "endgroup") break;
                if (op == "archive") {
                    std::string name; sub >> name;
                    it.group_archives.push_back(parse_archive_body(in, name));
                }
            }
            items.push_back(it);
        }
    }
    return items;
}

static bool obj_defines(const Obj& o, const std::string& name) {
    for (const auto& d : o.defs) if (d.name == name) return true;
    for (const auto& a : o.aliases) if (a.name == name && a.binding == "strong") return true;
    for (const auto& c : o.commons) if (c.name == name) return true;
    return false;
}

static bool currently_defined(const std::vector<Inc>& inc, const std::string& name) {
    for (const auto& i : inc) if (obj_defines(i.obj, name)) return true;
    return false;
}

static std::set<std::string> unresolved_strong_now(const std::vector<Inc>& inc) {
    std::set<std::string> out;
    for (const auto& i : inc) for (const auto& n : i.obj.needs)
        if (n.strength == "strong" && !currently_defined(inc, n.name)) out.insert(n.name);
    return out;
}

static void include_obj(std::vector<Inc>& inc, const Obj& obj, const std::string& label) {
    inc.push_back(Inc{obj, label, inc.size()});
}

static bool scan_archive(std::vector<Inc>& included, const Archive& ar, std::vector<bool>& used) {
    bool any = false;
    bool changed = true;
    while (changed) {
        changed = false;
        auto unresolved = unresolved_strong_now(included);
        if (unresolved.empty()) break;
        for (size_t m = 0; m < ar.members.size(); ++m) {
            if (used[m]) continue;
            bool take = false;
            for (const auto& sym : unresolved) if (obj_defines(ar.members[m], sym)) { take = true; break; }
            if (take) {
                used[m] = true;
                include_obj(included, ar.members[m], ar.name + "(" + ar.members[m].name + ")");
                changed = true;
                any = true;
                unresolved = unresolved_strong_now(included);
            }
        }
    }
    return any;
}

static std::string js(const std::string& s) {
    std::string out = "\"";
    for (char c : s) { if (c == '\\' || c == '"') out += '\\'; out += c; }
    out += "\"";
    return out;
}

int main(int argc, char** argv) {
    std::string manifest, entry, map_path, diag_path;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (i + 1 < argc && a == "--manifest") manifest = argv[++i];
        else if (i + 1 < argc && a == "--entry") entry = argv[++i];
        else if (i + 1 < argc && a == "--map") map_path = argv[++i];
        else if (i + 1 < argc && a == "--diagnostics") diag_path = argv[++i];
    }
    if (manifest.empty() || entry.empty() || map_path.empty() || diag_path.empty()) return 2;

    std::vector<Item> items;
    try { items = parse_manifest(manifest); } catch (...) { return 2; }

    std::vector<Inc> included;
    for (const auto& it : items) {
        if (!it.archive && !it.group) {
            include_obj(included, it.object, it.object.name);
        } else if (it.archive) {
            std::vector<bool> used(it.ar.members.size(), false);
            scan_archive(included, it.ar, used);
        } else {
            std::vector<std::vector<bool>> used;
            for (const auto& ar : it.group_archives) used.push_back(std::vector<bool>(ar.members.size(), false));
            bool group_changed = true;
            while (group_changed) {
                group_changed = false;
                for (size_t a = 0; a < it.group_archives.size(); ++a) {
                    if (scan_archive(included, it.group_archives[a], used[a])) group_changed = true;
                }
            }
        }
    }

    auto section_key = [](const std::string& label, size_t idx) { return label + "\n#" + std::to_string(idx); };
    std::set<std::string> active_section_keys;
    std::set<std::string> seen_comdat;
    for (const auto& inc : included) {
        for (size_t si = 0; si < inc.obj.sections.size(); ++si) {
            const auto& sec = inc.obj.sections[si];
            if (sec.group.empty() || sec.group == "-" || seen_comdat.insert(sec.group).second) {
                active_section_keys.insert(section_key(inc.label, si));
            }
        }
    }
    auto has_active_section = [&](const Inc& inc, const std::string& secname) {
        for (size_t si = 0; si < inc.obj.sections.size(); ++si) {
            if (inc.obj.sections[si].name == secname && active_section_keys.count(section_key(inc.label, si))) return true;
        }
        return false;
    };

    std::vector<std::pair<std::string, std::string>> got_slots;
    std::set<std::string> got_seen;
    for (const auto& inc : included) for (const auto& g : inc.obj.gots) {
        if (got_seen.insert(g.symbol).second) got_slots.push_back({g.symbol, inc.label});
    }

    std::vector<std::string> diagnostics;
    struct Cand { std::string binding, section, by, target; u64 offset = 0, size = 0, align = 1; size_t order = 0; bool alias = false; };
    std::map<std::string, Cand> chosen;
    std::map<std::string, std::string> first_strong;
    std::map<std::string, Cand> common_best;
    for (const auto& inc : included) {
        for (const auto& d : inc.obj.defs) {
            if (!has_active_section(inc, d.section)) continue;
            if (d.binding == "strong") {
                if (first_strong.count(d.name)) diagnostics.push_back("duplicate strong symbol " + d.name + " in " + inc.label + " (first " + first_strong[d.name] + ")");
                else {
                    first_strong[d.name] = inc.label;
                    chosen[d.name] = Cand{"strong", d.section, inc.label, "", d.offset, 0, 1, inc.order, false};
                }
            } else if (!chosen.count(d.name)) {
                chosen[d.name] = Cand{"weak", d.section, inc.label, "", d.offset, 0, 1, inc.order, false};
            }
        }
        for (const auto& a : inc.obj.aliases) {
            if (a.binding == "strong") {
                if (first_strong.count(a.name)) diagnostics.push_back("duplicate strong symbol " + a.name + " in " + inc.label + " (first " + first_strong[a.name] + ")");
                else {
                    first_strong[a.name] = inc.label;
                    chosen[a.name] = Cand{"strong", "", inc.label, a.target, 0, 0, 1, inc.order, true};
                }
            } else if (!chosen.count(a.name)) {
                chosen[a.name] = Cand{"weak", "", inc.label, a.target, 0, 0, 1, inc.order, true};
            }
        }
        for (const auto& c : inc.obj.commons) {
            auto it = common_best.find(c.name);
            if (it == common_best.end() || c.size > it->second.size || (c.size == it->second.size && c.align > it->second.align)) {
                common_best[c.name] = Cand{"common", ".bss", inc.label, "", 0, c.size, c.align, inc.order, false};
            }
        }
    }
    for (const auto& [name, c] : common_best) if (!chosen.count(name)) chosen[name] = c;

    if (!chosen.count(entry)) diagnostics.push_back("undefined entry " + entry);
    std::set<std::pair<std::string, std::string>> missing;
    for (const auto& inc : included) for (const auto& n : inc.obj.needs)
        if (n.strength == "strong" && !chosen.count(n.name)) missing.insert({n.name, inc.label});
    for (const auto& p : missing) diagnostics.push_back("undefined symbol " + p.first + " needed by " + p.second);

    std::vector<std::string> secnames;
    auto add_sec = [&](const std::string& s) { if (std::find(secnames.begin(), secnames.end(), s) == secnames.end()) secnames.push_back(s); };
    std::set<std::string> present;
    for (const auto& inc : included) for (size_t si = 0; si < inc.obj.sections.size(); ++si) if (active_section_keys.count(section_key(inc.label, si))) present.insert(inc.obj.sections[si].name);
    if (!got_slots.empty()) present.insert(".got");
    bool has_common = false; for (const auto& [n, c] : chosen) if (c.binding == "common") has_common = true;
    if (has_common) present.insert(".bss");
    for (const std::string& s : {".text", ".rodata", ".data", ".got", ".bss"}) if (present.count(s)) add_sec(s);
    std::vector<std::string> extra;
    for (const auto& s : present) if (s != ".text" && s != ".rodata" && s != ".data" && s != ".got" && s != ".bss") extra.push_back(s);
    std::sort(extra.begin(), extra.end()); for (const auto& s : extra) add_sec(s);

    std::map<std::string, u64> input_base;
    std::map<std::string, SectionOut> section_out;
    std::map<std::string, u64> common_addr;
    std::map<std::string, u64> got_addr;
    u64 cur = 0x1000;
    for (const auto& secname : secnames) {
        cur = align_to(cur, 16);
        u64 start = cur;
        for (const auto& inc : included) {
            for (size_t si = 0; si < inc.obj.sections.size(); ++si) {
                const auto& s = inc.obj.sections[si];
                if (s.name == secname && active_section_keys.count(section_key(inc.label, si))) {
                    cur = align_to(cur, s.align);
                    if (!input_base.count(inc.label + "\n" + secname)) input_base[inc.label + "\n" + secname] = cur;
                    cur += s.size;
                }
            }
        }
        if (secname == ".got") {
            for (const auto& slot : got_slots) { cur = align_to(cur, 8); got_addr[slot.first] = cur; cur += 8; }
        }
        if (secname == ".bss") {
            std::vector<std::string> commons;
            for (const auto& [n, c] : chosen) if (c.binding == "common") commons.push_back(n);
            std::sort(commons.begin(), commons.end());
            for (const auto& n : commons) { cur = align_to(cur, chosen[n].align); common_addr[n] = cur; cur += chosen[n].size; }
        }
        section_out[secname] = SectionOut{secname, start, cur - start, 16};
    }

    std::vector<Sym> symbols;
    std::map<std::string, u64> symaddr;
    std::map<std::string, std::string> symsec;
    for (const auto& [name, c] : chosen) {
        if (c.alias) continue;
        Sym s; s.name = name; s.binding = c.binding; s.section = c.section; s.size = c.size; s.defined_by = c.by;
        if (c.binding == "common") s.address = common_addr[name];
        else s.address = input_base[c.by + "\n" + c.section] + c.offset;
        symaddr[name] = s.address;
        symsec[name] = s.section;
        symbols.push_back(s);
    }

    std::set<std::string> cycle_reported;
    std::function<bool(const std::string&, std::vector<std::string>&)> resolve_alias = [&](const std::string& name, std::vector<std::string>& stack) -> bool {
        if (!chosen.count(name)) return false;
        const Cand& c = chosen[name];
        if (!c.alias) return symaddr.count(name) != 0;
        if (symaddr.count(name)) return true;
        if (std::find(stack.begin(), stack.end(), name) != stack.end()) {
            bool in_cycle = false;
            for (const auto& sname : stack) {
                if (sname == name) in_cycle = true;
                if (in_cycle && !cycle_reported.count(sname)) {
                    diagnostics.push_back("alias cycle " + sname + " in " + chosen[sname].by);
                    cycle_reported.insert(sname);
                }
            }
            if (!cycle_reported.count(name)) { diagnostics.push_back("alias cycle " + name + " in " + c.by); cycle_reported.insert(name); }
            return false;
        }
        stack.push_back(name);
        bool ok_target = resolve_alias(c.target, stack);
        stack.pop_back();
        if (!ok_target) {
            if (!chosen.count(c.target) && c.binding == "strong") diagnostics.push_back("undefined symbol " + c.target + " needed by " + c.by);
            if (c.binding == "weak" && !chosen.count(c.target)) { symaddr[name] = 0; symsec[name] = ""; return true; }
            return false;
        }
        symaddr[name] = symaddr[c.target];
        symsec[name] = symsec[c.target];
        return true;
    };
    for (const auto& [name, c] : chosen) if (c.alias) { std::vector<std::string> stack; resolve_alias(name, stack); }
    for (const auto& [name, c] : chosen) if (c.alias && symaddr.count(name)) {
        Sym s; s.name = name; s.binding = c.binding; s.section = symsec[name]; s.address = symaddr[name]; s.size = 0; s.defined_by = c.by; symbols.push_back(s);
    }
    for (const auto& slot : got_slots) {
        std::string gname = "__got_" + slot.first;
        Sym s; s.name = gname; s.binding = "strong"; s.section = ".got"; s.address = got_addr[slot.first]; s.size = 8; s.defined_by = slot.second;
        symaddr[gname] = s.address; symsec[gname] = ".got"; symbols.push_back(s);
    }
    std::sort(symbols.begin(), symbols.end(), [](const Sym& a, const Sym& b){ return a.name < b.name; });

    struct ROut { std::string object, section, type, symbol; u64 offset = 0; i64 value = 0; };
    std::vector<ROut> rout;
    for (const auto& inc : included) for (const auto& r : inc.obj.relocs) {
        if (!has_active_section(inc, r.section)) continue;
        u64 S = symaddr.count(r.symbol) ? symaddr[r.symbol] : 0;
        u64 P = input_base.count(inc.label + "\n" + r.section) ? input_base[inc.label + "\n" + r.section] + r.offset : r.offset;
        i64 val = 0;
        if (r.type == "R_ABS32") val = static_cast<i64>(S) + r.addend;
        else if (r.type == "R_PC32") val = static_cast<i64>(S) + r.addend - static_cast<i64>(P);
        rout.push_back(ROut{inc.label, r.section, r.type, r.symbol, r.offset, val});
    }

    std::sort(diagnostics.begin(), diagnostics.end());
    diagnostics.erase(std::unique(diagnostics.begin(), diagnostics.end()), diagnostics.end());
    std::ofstream diag(diag_path);
    if (diagnostics.empty()) diag << "OK\n"; else for (const auto& d : diagnostics) diag << d << "\n";

    std::ofstream out(map_path);
    out << "{\n  \"status\": " << js(diagnostics.empty() ? "ok" : "error") << ",\n  \"entry\": " << js(entry) << ",\n  \"included\": [";
    for (size_t i = 0; i < included.size(); ++i) { if (i) out << ", "; out << js(included[i].label); }
    out << "],\n  \"sections\": [";
    for (size_t i = 0; i < secnames.size(); ++i) { const auto& s = section_out[secnames[i]]; if (i) out << ", "; out << "{\"name\": " << js(s.name) << ", \"address\": " << s.address << ", \"size\": " << s.size << ", \"alignment\": " << s.alignment << "}"; }
    out << "],\n  \"symbols\": [";
    for (size_t i = 0; i < symbols.size(); ++i) { const auto& s = symbols[i]; if (i) out << ", "; out << "{\"name\": " << js(s.name) << ", \"binding\": " << js(s.binding) << ", \"section\": " << js(s.section) << ", \"address\": " << s.address << ", \"size\": " << s.size << ", \"defined_by\": " << js(s.defined_by) << "}"; }
    out << "],\n  \"relocations\": [";
    for (size_t i = 0; i < rout.size(); ++i) { const auto& r = rout[i]; if (i) out << ", "; out << "{\"object\": " << js(r.object) << ", \"section\": " << js(r.section) << ", \"offset\": " << r.offset << ", \"type\": " << js(r.type) << ", \"symbol\": " << js(r.symbol) << ", \"value\": " << r.value << "}"; }
    out << "]\n}\n";
    return 0;
}
CPP

/app/relplan/scripts/build.sh
