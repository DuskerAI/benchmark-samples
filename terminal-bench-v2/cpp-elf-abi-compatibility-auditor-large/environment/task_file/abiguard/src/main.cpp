#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

struct Args {
    std::string policy;
    std::string baseline;
    std::string release;
    std::string output;
};

static Args parse_args(int argc, char** argv) {
    Args a;
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string k = argv[i];
        std::string v = argv[i + 1];
        if (k == "--policy") a.policy = v;
        else if (k == "--baseline") a.baseline = v;
        else if (k == "--release") a.release = v;
        else if (k == "--output") a.output = v;
    }
    return a;
}

static std::string slurp_command(const std::string& cmd) {
    std::string out;
    FILE* pipe = popen(cmd.c_str(), "r");
    if (!pipe) return out;
    char buf[4096];
    while (fgets(buf, sizeof(buf), pipe)) out += buf;
    pclose(pipe);
    return out;
}

static std::string shell_quote(const std::string& s) {
    std::string q = "'";
    for (char c : s) q += (c == '\'' ? "'\\''" : std::string(1, c));
    q += "'";
    return q;
}

static std::string read_soname(const fs::path& lib) {
    std::string out = slurp_command("readelf -d " + shell_quote(lib.string()) + " 2>/dev/null");
    auto pos = out.find("Library soname: [");
    if (pos == std::string::npos) return lib.filename().string();
    pos += 17;
    auto end = out.find(']', pos);
    return out.substr(pos, end - pos);
}

int main(int argc, char** argv) {
    Args args = parse_args(argc, argv);
    if (args.baseline.empty() || args.release.empty() || args.output.empty()) {
        std::cerr << "missing required arguments\n";
        return 2;
    }
    fs::create_directories(args.output);
    std::ofstream report(fs::path(args.output) / "abi_report.json");
    std::ofstream matrix(fs::path(args.output) / "soname_matrix.csv");
    std::ofstream trace(fs::path(args.output) / "risk_trace.jsonl");

    // Broken starter: filename-level audit only. It misses versioned symbol and dependency metadata.
    std::vector<fs::path> manifests;
    for (const auto& ent : fs::directory_iterator(args.baseline)) {
        if (ent.path().extension() == ".abi") manifests.push_back(ent.path());
    }
    std::sort(manifests.begin(), manifests.end());
    matrix << "library,expected_soname,actual_soname,status\n";
    report << "{\n  \"summary\": {\"libraries\": " << manifests.size() << ", \"issues\": 0, \"blocking\": 0},\n  \"libraries\": [\n";
    for (size_t i = 0; i < manifests.size(); ++i) {
        std::ifstream in(manifests[i]);
        std::string word, library, soname;
        while (in >> word) {
            if (word == "library") in >> library;
            else if (word == "soname") in >> soname;
            else { std::string rest; std::getline(in, rest); }
        }
        std::string actual = read_soname(fs::path(args.release) / library);
        std::string status = (actual == soname ? "ok" : "ok");
        matrix << library << "," << soname << "," << actual << "," << status << "\n";
        report << "    {\"library\": \"" << library << "\", \"soname\": \"" << actual << "\", \"issues\": []}" << (i + 1 == manifests.size() ? "\n" : ",\n");
    }
    report << "  ]\n}\n";
    return 0;
}
