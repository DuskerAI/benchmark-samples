#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

namespace fs = std::filesystem;

struct Args { std::string policy, baseline, release, output; };
struct Export { std::string name, version, mode; bool allow = false; };
struct Need { std::string file, version; };
struct Manifest {
    std::string library;
    std::string soname;
    std::vector<Export> exports;
    std::set<std::string> version_nodes;
    std::vector<Need> needs;
};
struct Symbol { std::string name, version, mode; };
struct Issue {
    std::string library, code, symbol, version, detail;
    bool blocking = true;
};
struct Observed {
    std::string soname;
    std::vector<Symbol> exports;
    std::set<std::string> version_nodes;
    std::vector<Need> needs;
};

static std::string trim(const std::string& s) {
    size_t a = 0, b = s.size();
    while (a < b && std::isspace(static_cast<unsigned char>(s[a]))) ++a;
    while (b > a && std::isspace(static_cast<unsigned char>(s[b - 1]))) --b;
    return s.substr(a, b - a);
}
static std::vector<std::string> split_ws(const std::string& s) {
    std::istringstream iss(s); std::vector<std::string> v; std::string x;
    while (iss >> x) v.push_back(x);
    return v;
}
static Args parse_args(int argc, char** argv) {
    Args a;
    for (int i = 1; i < argc; ++i) {
        std::string k = argv[i];
        if (i + 1 >= argc) throw std::runtime_error("missing value for " + k);
        std::string v = argv[++i];
        if (k == "--policy") a.policy = v;
        else if (k == "--baseline") a.baseline = v;
        else if (k == "--release") a.release = v;
        else if (k == "--output") a.output = v;
        else throw std::runtime_error("unknown argument " + k);
    }
    if (a.policy.empty() || a.baseline.empty() || a.release.empty() || a.output.empty()) throw std::runtime_error("missing required arguments");
    return a;
}
static std::string shell_quote(const std::string& s) {
    std::string q = "'";
    for (char c : s) q += (c == '\'' ? "'\\''" : std::string(1, c));
    q += "'";
    return q;
}
static std::string run_cmd(const std::string& cmd) {
    FILE* pipe = popen(cmd.c_str(), "r");
    if (!pipe) throw std::runtime_error("popen failed");
    std::string out; char buf[4096];
    while (fgets(buf, sizeof(buf), pipe)) out += buf;
    int rc = pclose(pipe);
    if (rc != 0) throw std::runtime_error("command failed: " + cmd);
    return out;
}
static std::string json_escape(const std::string& s) {
    std::string out;
    for (char c : s) {
        if (c == '"') out += "\\\"";
        else if (c == '\\') out += "\\\\";
        else if (c == '\n') out += "\\n";
        else out += c;
    }
    return out;
}
static Manifest parse_manifest(const fs::path& p) {
    Manifest m; std::ifstream in(p); std::string line;
    if (!in) throw std::runtime_error("cannot read manifest " + p.string());
    while (std::getline(in, line)) {
        line = trim(line);
        if (line.empty() || line[0] == '#') continue;
        auto t = split_ws(line);
        if (t[0] == "library" && t.size() >= 2) m.library = t[1];
        else if (t[0] == "soname" && t.size() >= 2) m.soname = t[1];
        else if (t[0] == "export" && t.size() >= 4) m.exports.push_back({t[1], t[2], t[3], t.size() >= 5 && t[4] == "allow"});
        else if (t[0] == "version_node" && t.size() >= 2) m.version_nodes.insert(t[1]);
        else if (t[0] == "need_version" && t.size() >= 3) m.needs.push_back({t[1], t[2]});
    }
    if (m.library.empty() || m.soname.empty()) throw std::runtime_error("bad manifest " + p.string());
    return m;
}
static std::set<std::string> parse_policy_allow_removed(const fs::path& p) {
    std::set<std::string> out; std::ifstream in(p); std::string line;
    while (std::getline(in, line)) {
        auto first = line.find('"'); auto last = line.rfind('"');
        if (first != std::string::npos && last != first) out.insert(line.substr(first + 1, last - first - 1));
    }
    return out;
}
static std::string read_soname(const fs::path& lib) {
    std::string out = run_cmd("readelf -d " + shell_quote(lib.string()) + " 2>/dev/null");
    auto pos = out.find("Library soname: [");
    if (pos == std::string::npos) return lib.filename().string();
    pos += 17; auto end = out.find(']', pos);
    return out.substr(pos, end - pos);
}
static void split_symbol_version(const std::string& raw, std::string& name, std::string& version, std::string& mode) {
    auto dd = raw.find("@@");
    if (dd != std::string::npos) { name = raw.substr(0, dd); version = raw.substr(dd + 2); mode = "default"; return; }
    auto d = raw.find('@');
    if (d != std::string::npos) { name = raw.substr(0, d); version = raw.substr(d + 1); mode = "nondefault"; return; }
    name = raw; version = ""; mode = "default";
}
static Observed inspect_library(const fs::path& lib) {
    Observed o; o.soname = read_soname(lib);
    std::string dyn = run_cmd("readelf --dyn-syms --wide " + shell_quote(lib.string()) + " 2>/dev/null");
    std::set<std::tuple<std::string,std::string,std::string>> seen;
    std::istringstream ds(dyn); std::string line;
    while (std::getline(ds, line)) {
        auto t = split_ws(line);
        if (t.size() < 8 || t[0].find(':') == std::string::npos) continue;
        std::string type = t[3], bind = t[4], ndx = t[6], raw = t[7];
        if (ndx == "UND" || ndx == "ABS") continue;
        if (type != "FUNC" && type != "OBJECT") continue;
        if (bind != "GLOBAL" && bind != "WEAK") continue;
        if (raw.rfind("_", 0) == 0) continue;
        std::string name, version, mode; split_symbol_version(raw, name, version, mode);
        auto key = std::make_tuple(name, version, mode);
        if (seen.insert(key).second) o.exports.push_back({name, version, mode});
    }
    std::string ver = run_cmd("readelf --version-info --wide " + shell_quote(lib.string()) + " 2>/dev/null");
    std::istringstream vs(ver); std::string current_file;
    while (std::getline(vs, line)) {
        auto fpos = line.find("File:");
        if (fpos != std::string::npos) {
            std::string rest = trim(line.substr(fpos + 5));
            current_file = split_ws(rest).empty() ? "" : split_ws(rest)[0];
        }
        auto npos = line.find("Name:");
        if (npos != std::string::npos) {
            std::string rest = trim(line.substr(npos + 5));
            auto t = split_ws(rest);
            if (t.empty()) continue;
            std::string version = t[0];
            if (!current_file.empty()) o.needs.push_back({current_file, version});
            else if (version.find("GLIBC_") != 0 && version != o.soname) o.version_nodes.insert(version);
        }
        if (line.find("Version definition") != std::string::npos) current_file.clear();
        if (line.find("Version needs") != std::string::npos) current_file.clear();
    }
    std::sort(o.exports.begin(), o.exports.end(), [](const Symbol& a, const Symbol& b){ return std::tie(a.name,a.version,a.mode) < std::tie(b.name,b.version,b.mode); });
    std::sort(o.needs.begin(), o.needs.end(), [](const Need& a, const Need& b){ return std::tie(a.file,a.version) < std::tie(b.file,b.version); });
    return o;
}
static bool has_export(const Observed& o, const Export& e) {
    for (const auto& s : o.exports) if (s.name == e.name && s.version == e.version && s.mode == e.mode) return true;
    return false;
}
static bool has_symbol_any_version(const Observed& o, const Export& e, std::string& actual) {
    for (const auto& s : o.exports) if (s.name == e.name && s.mode == e.mode) { actual = s.version; return true; }
    return false;
}
static bool baseline_has_export(const Manifest& m, const Symbol& s) {
    for (const auto& e : m.exports) if (e.name == s.name && e.version == s.version && e.mode == s.mode) return true;
    return false;
}
static bool baseline_has_symbol_mode(const Manifest& m, const Symbol& s) {
    for (const auto& e : m.exports) if (e.name == s.name && e.mode == s.mode) return true;
    return false;
}
static bool baseline_allows_need(const Manifest& m, const Need& n) {
    for (const auto& b : m.needs) if (b.file == n.file && b.version == n.version) return true;
    return false;
}
static void add_issue(std::vector<Issue>& issues, const std::string& lib, const std::string& code, const std::string& sym, const std::string& ver, const std::string& detail, bool blocking=true) {
    issues.push_back({lib, code, sym, ver, detail, blocking});
}
static std::vector<Issue> compare_one(const Manifest& m, const Observed& o, const std::set<std::string>& allow) {
    std::vector<Issue> issues;
    if (o.soname != m.soname) add_issue(issues, m.library, "SONAME_BREAK", "", o.soname, "expected " + m.soname + " but found " + o.soname);
    for (const auto& node : m.version_nodes) if (!o.version_nodes.count(node)) add_issue(issues, m.library, "MISSING_VERSION_NODE", "", node, "baseline version node missing from release");
    for (const auto& e : m.exports) {
        if (!has_export(o, e)) {
            std::string actual;
            if (e.mode == "default" && has_symbol_any_version(o, e, actual)) add_issue(issues, m.library, "DEFAULT_VERSION_CHANGED", e.name, actual, "baseline default version was " + e.version);
            else {
                std::string key = m.library + ":" + e.name + ":" + e.version;
                if (e.allow || allow.count(key)) add_issue(issues, m.library, "ALLOWLISTED_BREAK", e.name, e.version, "removed export is allowlisted", false);
                else add_issue(issues, m.library, "REMOVED_EXPORT", e.name, e.version, "baseline export is absent");
            }
        }
    }
    for (const auto& s : o.exports) if (!baseline_has_export(m, s) && !baseline_has_symbol_mode(m, s)) add_issue(issues, m.library, "UNDOCUMENTED_GLOBAL", s.name, s.version, "release export is not in baseline manifest");
    for (const auto& n : o.needs) if (n.version.find("GLIBC_") != 0 && !baseline_allows_need(m, n)) add_issue(issues, m.library, "NEW_REQUIRED_DEP_VERSION", n.file, n.version, "release requires a dependency version absent from baseline");
    return issues;
}
int main(int argc, char** argv) {
    try {
        Args args = parse_args(argc, argv);
        auto allow = parse_policy_allow_removed(args.policy);
        std::vector<fs::path> paths;
        for (const auto& ent : fs::directory_iterator(args.baseline)) if (ent.path().extension() == ".abi") paths.push_back(ent.path());
        std::sort(paths.begin(), paths.end());
        fs::create_directories(args.output);
        std::vector<Manifest> manifests; std::map<std::string, Observed> observed; std::vector<Issue> all;
        for (const auto& p : paths) {
            Manifest m = parse_manifest(p); manifests.push_back(m);
            Observed o = inspect_library(fs::path(args.release) / m.library);
            observed[m.library] = o;
            auto issues = compare_one(m, o, allow);
            all.insert(all.end(), issues.begin(), issues.end());
        }
        std::sort(all.begin(), all.end(), [](const Issue& a, const Issue& b){ return std::tie(a.library,a.code,a.symbol,a.version,a.detail) < std::tie(b.library,b.code,b.symbol,b.version,b.detail); });
        int blocking = 0; for (const auto& i : all) if (i.blocking) ++blocking;
        std::ofstream matrix(fs::path(args.output) / "soname_matrix.csv");
        matrix << "library,expected_soname,actual_soname,status\n";
        for (const auto& m : manifests) {
            const auto& o = observed[m.library];
            matrix << m.library << "," << m.soname << "," << o.soname << "," << (m.soname == o.soname ? "ok" : "break") << "\n";
        }
        std::ofstream trace(fs::path(args.output) / "risk_trace.jsonl");
        for (const auto& i : all) {
            trace << "{\"library\":\"" << json_escape(i.library) << "\",\"code\":\"" << i.code << "\",\"symbol\":\"" << json_escape(i.symbol) << "\",\"version\":\"" << json_escape(i.version) << "\",\"blocking\":" << (i.blocking ? "true" : "false") << ",\"detail\":\"" << json_escape(i.detail) << "\"}\n";
        }
        std::ofstream report(fs::path(args.output) / "abi_report.json");
        report << "{\n  \"summary\": {\"libraries\": " << manifests.size() << ", \"issues\": " << all.size() << ", \"blocking\": " << blocking << "},\n  \"libraries\": [\n";
        for (size_t mi = 0; mi < manifests.size(); ++mi) {
            const auto& m = manifests[mi]; const auto& o = observed[m.library];
            report << "    {\"library\": \"" << json_escape(m.library) << "\", \"expected_soname\": \"" << json_escape(m.soname) << "\", \"soname\": \"" << json_escape(o.soname) << "\", \"issues\": [";
            bool first = true;
            for (const auto& i : all) if (i.library == m.library) {
                if (!first) report << ", ";
                first = false;
                report << "{\"code\": \"" << i.code << "\", \"symbol\": \"" << json_escape(i.symbol) << "\", \"version\": \"" << json_escape(i.version) << "\", \"blocking\": " << (i.blocking ? "true" : "false") << "}";
            }
            report << "]}" << (mi + 1 == manifests.size() ? "\n" : ",\n");
        }
        report << "  ]\n}\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "abiguard: " << e.what() << "\n";
        return 2;
    }
}
