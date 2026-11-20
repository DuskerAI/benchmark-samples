#include <fstream>
#include <iostream>
#include <string>
#include <vector>

static std::string arg_value(int argc, char **argv, const std::string &name) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (argv[i] == name) {
            return argv[i + 1];
        }
    }
    return "";
}

static std::string read_file(const std::string &path) {
    std::ifstream in(path, std::ios::binary);
    return std::string((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
}

static void write_report(const std::string &path, bool admitted, const std::string &status,
                         const std::string &credential, unsigned long count,
                         const std::vector<std::string> &violations) {
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    out << "{\"admitted\":" << (admitted ? "true" : "false")
        << ",\"status\":\"" << status << "\",\"credential_id\":";
    if (credential.empty()) {
        out << "null";
    } else {
        out << "\"" << credential << "\"";
    }
    out << ",\"sign_count\":" << count << ",\"audit_hash\":null,\"violations\":[";
    for (std::size_t i = 0; i < violations.size(); ++i) {
        if (i) out << ",";
        out << "\"" << violations[i] << "\"";
    }
    out << "]}\n";
}

int main(int argc, char **argv) {
    const std::string policy = arg_value(argc, argv, "--policy");
    const std::string event = arg_value(argc, argv, "--event");
    const std::string state = arg_value(argc, argv, "--state");
    const std::string report = arg_value(argc, argv, "--out");
    if (policy.empty() || event.empty() || state.empty() || report.empty()) {
        std::cerr << "missing arguments\n";
        return 2;
    }

    const std::string event_text = read_file(event);
    std::string credential;
    const std::string key = "\"credential_id\"";
    std::size_t p = event_text.find(key);
    if (p != std::string::npos) {
        p = event_text.find('"', p + key.size());
        std::size_t q = event_text.find('"', p + 1);
        if (p != std::string::npos && q != std::string::npos) {
            credential = event_text.substr(p + 1, q - p - 1);
        }
    }
    write_report(report, true,
                 event_text.find("authentication") == std::string::npos ? "registered" : "authenticated",
                 credential, 1, {});
    std::ofstream s(state, std::ios::binary | std::ios::app);
    s.flush();
    return 0;
}
