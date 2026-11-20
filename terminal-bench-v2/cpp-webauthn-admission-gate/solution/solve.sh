#!/bin/bash
set -euo pipefail

cat > /app/webauthn_gate/src/main.cpp <<'CPP'
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <openssl/sha.h>

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

struct Json {
    enum class Type { Null, Bool, Number, String, Array, Object } type = Type::Null;
    bool b = false;
    uint64_t n = 0;
    std::string s;
    std::vector<Json> a;
    std::map<std::string, Json> o;
};

class JsonParser {
  public:
    explicit JsonParser(std::string text) : text_(std::move(text)) {}
    Json parse() {
        Json v = parse_value();
        skip_ws();
        if (pos_ != text_.size()) throw std::runtime_error("trailing-json");
        return v;
    }

  private:
    std::string text_;
    std::size_t pos_ = 0;

    void skip_ws() {
        while (pos_ < text_.size() && std::isspace(static_cast<unsigned char>(text_[pos_]))) ++pos_;
    }
    char get() {
        if (pos_ >= text_.size()) throw std::runtime_error("json-eof");
        return text_[pos_++];
    }
    bool accept(char c) {
        skip_ws();
        if (pos_ < text_.size() && text_[pos_] == c) {
            ++pos_;
            return true;
        }
        return false;
    }
    void expect(const std::string &lit) {
        if (text_.compare(pos_, lit.size(), lit) != 0) throw std::runtime_error("json-token");
        pos_ += lit.size();
    }
    Json parse_value() {
        skip_ws();
        if (pos_ >= text_.size()) throw std::runtime_error("json-value");
        char c = text_[pos_];
        if (c == '"') return parse_string();
        if (c == '{') return parse_object();
        if (c == '[') return parse_array();
        if (c == 't') { expect("true"); Json v; v.type = Json::Type::Bool; v.b = true; return v; }
        if (c == 'f') { expect("false"); Json v; v.type = Json::Type::Bool; return v; }
        if (c == 'n') { expect("null"); return Json{}; }
        if (std::isdigit(static_cast<unsigned char>(c))) return parse_number();
        throw std::runtime_error("json-value");
    }
    Json parse_string() {
        if (get() != '"') throw std::runtime_error("json-string");
        Json v;
        v.type = Json::Type::String;
        while (pos_ < text_.size()) {
            char c = get();
            if (c == '"') return v;
            if (c == '\\') {
                char e = get();
                if (e == '"' || e == '\\' || e == '/') v.s.push_back(e);
                else if (e == 'b') v.s.push_back('\b');
                else if (e == 'f') v.s.push_back('\f');
                else if (e == 'n') v.s.push_back('\n');
                else if (e == 'r') v.s.push_back('\r');
                else if (e == 't') v.s.push_back('\t');
                else throw std::runtime_error("json-escape");
            } else {
                v.s.push_back(c);
            }
        }
        throw std::runtime_error("json-string-eof");
    }
    Json parse_number() {
        Json v;
        v.type = Json::Type::Number;
        if (pos_ >= text_.size() || !std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
            throw std::runtime_error("json-number");
        }
        while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
            v.n = v.n * 10 + static_cast<unsigned>(text_[pos_] - '0');
            ++pos_;
        }
        return v;
    }
    Json parse_array() {
        get();
        Json v;
        v.type = Json::Type::Array;
        skip_ws();
        if (accept(']')) return v;
        while (true) {
            v.a.push_back(parse_value());
            skip_ws();
            if (accept(']')) return v;
            if (!accept(',')) throw std::runtime_error("json-array");
        }
    }
    Json parse_object() {
        get();
        Json v;
        v.type = Json::Type::Object;
        skip_ws();
        if (accept('}')) return v;
        while (true) {
            Json key = parse_string();
            if (!accept(':')) throw std::runtime_error("json-object");
            if (v.o.find(key.s) != v.o.end()) throw std::runtime_error("duplicate-json-key");
            v.o[key.s] = parse_value();
            skip_ws();
            if (accept('}')) return v;
            if (!accept(',')) throw std::runtime_error("json-object");
        }
    }
};

static std::string read_file(const std::string &path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("read-file");
    return std::string((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
}

static const Json &field(const Json &j, const std::string &name) {
    if (j.type != Json::Type::Object) throw std::runtime_error("object-required");
    auto it = j.o.find(name);
    if (it == j.o.end()) throw std::runtime_error("missing-field");
    return it->second;
}

static std::string string_field(const Json &j, const std::string &name) {
    const Json &v = field(j, name);
    if (v.type != Json::Type::String) throw std::runtime_error("string-field");
    return v.s;
}

static uint64_t number_field(const Json &j, const std::string &name) {
    const Json &v = field(j, name);
    if (v.type != Json::Type::Number) throw std::runtime_error("number-field");
    return v.n;
}

static int hex_digit(char c) {
    if ('0' <= c && c <= '9') return c - '0';
    if ('a' <= c && c <= 'f') return c - 'a' + 10;
    if ('A' <= c && c <= 'F') return c - 'A' + 10;
    return -1;
}

static std::vector<uint8_t> hex_to_bytes(const std::string &hex) {
    if (hex.size() % 2) throw std::runtime_error("hex-length");
    std::vector<uint8_t> out;
    out.reserve(hex.size() / 2);
    for (std::size_t i = 0; i < hex.size(); i += 2) {
        int hi = hex_digit(hex[i]);
        int lo = hex_digit(hex[i + 1]);
        if (hi < 0 || lo < 0) throw std::runtime_error("hex-char");
        out.push_back(static_cast<uint8_t>((hi << 4) | lo));
    }
    return out;
}

static std::string bytes_to_hex(const std::vector<uint8_t> &bytes) {
    static const char *digits = "0123456789abcdef";
    std::string out;
    out.reserve(bytes.size() * 2);
    for (uint8_t b : bytes) {
        out.push_back(digits[b >> 4]);
        out.push_back(digits[b & 15]);
    }
    return out;
}

static std::vector<uint8_t> base64url_decode(const std::string &in) {
    auto val = [](char c) -> int {
        if ('A' <= c && c <= 'Z') return c - 'A';
        if ('a' <= c && c <= 'z') return c - 'a' + 26;
        if ('0' <= c && c <= '9') return c - '0' + 52;
        if (c == '-') return 62;
        if (c == '_') return 63;
        if (c == '=') return -2;
        return -1;
    };
    std::vector<uint8_t> out;
    int bits = 0;
    int acc = 0;
    for (std::size_t i = 0; i < in.size(); ++i) {
        int v = val(in[i]);
        if (v == -2) {
            for (std::size_t j = i; j < in.size(); ++j) {
                if (in[j] != '=') throw std::runtime_error("base64-padding");
            }
            break;
        }
        if (v < 0) throw std::runtime_error("base64");
        acc = (acc << 6) | v;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out.push_back(static_cast<uint8_t>((acc >> bits) & 0xff));
        }
    }
    return out;
}

static std::vector<uint8_t> sha256(const std::vector<uint8_t> &data) {
    std::vector<uint8_t> out(SHA256_DIGEST_LENGTH);
    SHA256(data.data(), data.size(), out.data());
    return out;
}

static std::vector<uint8_t> sha256_string(const std::string &data) {
    return sha256(std::vector<uint8_t>(data.begin(), data.end()));
}

static std::vector<uint8_t> hmac_sha256(const std::vector<uint8_t> &key, const std::vector<uint8_t> &data) {
    unsigned int len = 0;
    std::vector<uint8_t> out(EVP_MAX_MD_SIZE);
    HMAC(EVP_sha256(), key.data(), static_cast<int>(key.size()), data.data(), data.size(), out.data(), &len);
    out.resize(len);
    return out;
}

static uint64_t cbor_len(const std::vector<uint8_t> &b, std::size_t &p, uint8_t add) {
    if (add < 24) return add;
    if (add == 24) {
        if (p >= b.size()) throw std::runtime_error("cbor-len");
        return b[p++];
    }
    if (add == 25) {
        if (p + 2 > b.size()) throw std::runtime_error("cbor-len");
        uint64_t v = (static_cast<uint64_t>(b[p]) << 8) | b[p + 1];
        p += 2;
        return v;
    }
    throw std::runtime_error("cbor-len");
}

static void skip_cbor(const std::vector<uint8_t> &b, std::size_t &p) {
    if (p >= b.size()) throw std::runtime_error("cbor-eof");
    uint8_t ib = b[p++];
    uint8_t major = ib >> 5;
    uint8_t add = ib & 31;
    if (major <= 3) {
        uint64_t len = cbor_len(b, p, add);
        if (major >= 2) {
            if (p + len > b.size()) throw std::runtime_error("cbor-data");
            p += static_cast<std::size_t>(len);
        }
        return;
    }
    if (major == 4) {
        uint64_t len = cbor_len(b, p, add);
        for (uint64_t i = 0; i < len; ++i) skip_cbor(b, p);
        return;
    }
    if (major == 5) {
        uint64_t len = cbor_len(b, p, add);
        for (uint64_t i = 0; i < len; ++i) { skip_cbor(b, p); skip_cbor(b, p); }
        return;
    }
    if (major == 7 && add < 24) return;
    throw std::runtime_error("cbor-type");
}

static std::string read_cbor_text(const std::vector<uint8_t> &b, std::size_t &p) {
    if (p >= b.size()) throw std::runtime_error("cbor-text");
    uint8_t ib = b[p++];
    if ((ib >> 5) != 3) throw std::runtime_error("cbor-text");
    uint64_t len = cbor_len(b, p, ib & 31);
    if (p + len > b.size()) throw std::runtime_error("cbor-text");
    std::string out(reinterpret_cast<const char *>(b.data() + p), static_cast<std::size_t>(len));
    p += static_cast<std::size_t>(len);
    return out;
}

static std::vector<uint8_t> read_cbor_bytes(const std::vector<uint8_t> &b, std::size_t &p) {
    if (p >= b.size()) throw std::runtime_error("cbor-bytes");
    uint8_t ib = b[p++];
    if ((ib >> 5) != 2) throw std::runtime_error("cbor-bytes");
    uint64_t len = cbor_len(b, p, ib & 31);
    if (p + len > b.size()) throw std::runtime_error("cbor-bytes");
    std::vector<uint8_t> out(b.begin() + static_cast<long>(p), b.begin() + static_cast<long>(p + len));
    p += static_cast<std::size_t>(len);
    return out;
}

struct Attestation {
    std::string fmt;
    std::vector<uint8_t> aaguid;
    std::vector<uint8_t> credential_id;
    std::vector<uint8_t> nonce;
    std::vector<uint8_t> sig;
};

static Attestation parse_attestation(const std::vector<uint8_t> &b) {
    std::size_t p = 0;
    if (p >= b.size()) throw std::runtime_error("cbor-map");
    uint8_t ib = b[p++];
    if ((ib >> 5) != 5) throw std::runtime_error("cbor-map");
    uint64_t len = cbor_len(b, p, ib & 31);
    Attestation att;
    bool have_fmt = false, have_aaguid = false, have_cred = false, have_nonce = false, have_sig = false;
    for (uint64_t i = 0; i < len; ++i) {
        std::string key = read_cbor_text(b, p);
        if (key == "fmt") { if (have_fmt) throw std::runtime_error("duplicate-cbor-key"); att.fmt = read_cbor_text(b, p); have_fmt = true; }
        else if (key == "aaguid") { if (have_aaguid) throw std::runtime_error("duplicate-cbor-key"); att.aaguid = read_cbor_bytes(b, p); have_aaguid = true; }
        else if (key == "credential_id") { if (have_cred) throw std::runtime_error("duplicate-cbor-key"); att.credential_id = read_cbor_bytes(b, p); have_cred = true; }
        else if (key == "nonce") { if (have_nonce) throw std::runtime_error("duplicate-cbor-key"); att.nonce = read_cbor_bytes(b, p); have_nonce = true; }
        else if (key == "sig") { if (have_sig) throw std::runtime_error("duplicate-cbor-key"); att.sig = read_cbor_bytes(b, p); have_sig = true; }
        else { skip_cbor(b, p); }
    }
    if (p != b.size() || !have_fmt || !have_aaguid || !have_cred || !have_nonce || !have_sig) {
        throw std::runtime_error("attestation-fields");
    }
    return att;
}

struct AuthData {
    std::vector<uint8_t> rp_hash;
    uint8_t flags = 0;
    uint32_t counter = 0;
    bool has_attested = false;
    std::vector<uint8_t> aaguid;
    std::vector<uint8_t> credential_id;
};

static AuthData parse_auth_data(const std::vector<uint8_t> &b) {
    if (b.size() < 37) throw std::runtime_error("auth-data-short");
    AuthData ad;
    ad.rp_hash.assign(b.begin(), b.begin() + 32);
    ad.flags = b[32];
    ad.counter = (static_cast<uint32_t>(b[33]) << 24) | (static_cast<uint32_t>(b[34]) << 16) |
                 (static_cast<uint32_t>(b[35]) << 8) | static_cast<uint32_t>(b[36]);
    std::size_t p = 37;
    if (ad.flags & 0x40) {
        ad.has_attested = true;
        if (p + 18 > b.size()) throw std::runtime_error("attested-short");
        ad.aaguid.assign(b.begin() + static_cast<long>(p), b.begin() + static_cast<long>(p + 16));
        p += 16;
        uint16_t id_len = (static_cast<uint16_t>(b[p]) << 8) | b[p + 1];
        p += 2;
        if (p + id_len > b.size()) throw std::runtime_error("credential-short");
        ad.credential_id.assign(b.begin() + static_cast<long>(p), b.begin() + static_cast<long>(p + id_len));
        p += id_len;
        if (p >= b.size() || (b[p] >> 5) != 5) throw std::runtime_error("credential-key-map");
        skip_cbor(b, p);
    }
    if (p != b.size()) throw std::runtime_error("auth-data-extra");
    return ad;
}

struct Trust {
    std::string aaguid;
    std::vector<uint8_t> attestation_key;
    std::vector<uint8_t> assertion_key;
};

struct Policy {
    std::string rp_id;
    std::vector<std::string> origins;
    std::vector<std::string> challenges;
    std::vector<Trust> trust;
};

struct Credential {
    std::string credential_id;
    std::string aaguid;
    uint64_t sign_count = 0;
};

struct AuditEntry {
    uint64_t seq = 0;
    std::string event_hash;
    std::string status;
    std::string credential_id;
    uint64_t sign_count = 0;
    std::string prev_hash;
    std::string entry_hash;
};

struct State {
    std::vector<Credential> credentials;
    std::vector<AuditEntry> audit;
    std::string current_hash;
};

static const std::string ZERO_HASH(64, '0');

static void require_hash_hex(const std::string &hex) {
    if (hex_to_bytes(hex).size() != 32) throw std::runtime_error("hash-size");
}

static std::vector<uint8_t> u64be(uint64_t value) {
    std::vector<uint8_t> out(8);
    for (int i = 7; i >= 0; --i) {
        out[static_cast<std::size_t>(7 - i)] = static_cast<uint8_t>((value >> (i * 8)) & 0xff);
    }
    return out;
}

static std::string compute_entry_hash(const std::string &event_hash, const std::string &prev_hash,
                                      const std::string &credential_id, uint64_t sign_count,
                                      const std::string &status) {
    require_hash_hex(event_hash);
    require_hash_hex(prev_hash);
    std::vector<uint8_t> body = hex_to_bytes(event_hash);
    std::vector<uint8_t> prev = hex_to_bytes(prev_hash);
    std::vector<uint8_t> cred = hex_to_bytes(credential_id);
    std::vector<uint8_t> count = u64be(sign_count);
    body.insert(body.end(), prev.begin(), prev.end());
    body.insert(body.end(), cred.begin(), cred.end());
    body.insert(body.end(), count.begin(), count.end());
    body.insert(body.end(), status.begin(), status.end());
    return bytes_to_hex(sha256(body));
}

static AuditEntry make_audit_entry(const State &state, const std::string &event_raw,
                                   const std::string &credential_id, uint64_t sign_count,
                                   const std::string &status) {
    AuditEntry e;
    e.seq = state.audit.size();
    e.event_hash = bytes_to_hex(sha256_string(event_raw));
    e.status = status;
    e.credential_id = credential_id;
    e.sign_count = sign_count;
    e.prev_hash = state.current_hash;
    e.entry_hash = compute_entry_hash(e.event_hash, e.prev_hash, e.credential_id, e.sign_count, e.status);
    return e;
}

static Policy parse_policy(const Json &j) {
    Policy p;
    p.rp_id = string_field(j, "rp_id");
    const Json &origins = field(j, "origins");
    if (origins.type != Json::Type::Array) throw std::runtime_error("origins-array");
    for (const Json &v : origins.a) {
        if (v.type != Json::Type::String) throw std::runtime_error("origin");
        p.origins.push_back(v.s);
    }
    const Json &challenges = field(j, "challenges");
    if (challenges.type != Json::Type::Array) throw std::runtime_error("challenges-array");
    for (const Json &v : challenges.a) {
        if (v.type != Json::Type::String) throw std::runtime_error("challenge");
        p.challenges.push_back(v.s);
    }
    const Json &trusted = field(j, "trusted_aaguids");
    if (trusted.type != Json::Type::Array) throw std::runtime_error("trusted-array");
    for (const Json &v : trusted.a) {
        Trust t;
        t.aaguid = bytes_to_hex(hex_to_bytes(string_field(v, "aaguid")));
        t.attestation_key = hex_to_bytes(string_field(v, "attestation_key"));
        t.assertion_key = hex_to_bytes(string_field(v, "assertion_key"));
        p.trust.push_back(t);
    }
    return p;
}

static State parse_state(const Json &j) {
    State out;
    const Json &credentials = field(j, "credentials");
    if (credentials.type != Json::Type::Array) throw std::runtime_error("credentials-array");
    for (const Json &v : credentials.a) {
        Credential c;
        c.credential_id = bytes_to_hex(hex_to_bytes(string_field(v, "credential_id")));
        c.aaguid = bytes_to_hex(hex_to_bytes(string_field(v, "aaguid")));
        c.sign_count = number_field(v, "sign_count");
        out.credentials.push_back(c);
    }
    const Json &audit = field(j, "audit");
    if (audit.type != Json::Type::Array) throw std::runtime_error("audit-array");
    std::string expected_prev = ZERO_HASH;
    for (std::size_t i = 0; i < audit.a.size(); ++i) {
        const Json &v = audit.a[i];
        AuditEntry e;
        e.seq = number_field(v, "seq");
        e.event_hash = bytes_to_hex(hex_to_bytes(string_field(v, "event_hash")));
        e.status = string_field(v, "status");
        e.credential_id = bytes_to_hex(hex_to_bytes(string_field(v, "credential_id")));
        e.sign_count = number_field(v, "sign_count");
        e.prev_hash = bytes_to_hex(hex_to_bytes(string_field(v, "prev_hash")));
        e.entry_hash = bytes_to_hex(hex_to_bytes(string_field(v, "entry_hash")));
        require_hash_hex(e.event_hash);
        require_hash_hex(e.prev_hash);
        require_hash_hex(e.entry_hash);
        if (e.seq != i || (e.status != "registered" && e.status != "authenticated")) {
            throw std::runtime_error("audit-fields");
        }
        if (e.prev_hash != expected_prev) throw std::runtime_error("audit-prev");
        if (compute_entry_hash(e.event_hash, e.prev_hash, e.credential_id, e.sign_count, e.status) != e.entry_hash) {
            throw std::runtime_error("audit-hash");
        }
        expected_prev = e.entry_hash;
        out.audit.push_back(e);
    }
    out.current_hash = expected_prev;
    return out;
}

static const Trust *trust_for(const Policy &p, const std::string &aaguid) {
    for (const Trust &t : p.trust) if (t.aaguid == aaguid) return &t;
    return nullptr;
}

static bool contains(const std::vector<std::string> &values, const std::string &value) {
    return std::find(values.begin(), values.end(), value) != values.end();
}

static std::string json_escape(const std::string &s) {
    std::string out;
    for (char c : s) {
        if (c == '"' || c == '\\') { out.push_back('\\'); out.push_back(c); }
        else if (c == '\n') out += "\\n";
        else out.push_back(c);
    }
    return out;
}

static void write_report(const std::string &path, bool admitted, const std::string &status,
                          const std::string &credential, uint64_t count,
                          const std::string &audit_hash,
                          const std::vector<std::string> &violations) {
    std::ofstream out(path, std::ios::binary | std::ios::trunc);
    if (!out) throw std::runtime_error("report-write");
    out << "{\"admitted\":" << (admitted ? "true" : "false") << ",\"status\":\"" << status
        << "\",\"credential_id\":";
    if (credential.empty()) out << "null"; else out << "\"" << json_escape(credential) << "\"";
    out << ",\"sign_count\":" << count << ",\"audit_hash\":";
    if (audit_hash.empty()) out << "null"; else out << "\"" << json_escape(audit_hash) << "\"";
    out << ",\"violations\":[";
    for (std::size_t i = 0; i < violations.size(); ++i) {
        if (i) out << ',';
        out << "\"" << json_escape(violations[i]) << "\"";
    }
    out << "]}\n";
}

static void write_state_atomic(const std::string &path, const State &state) {
    std::string tmp = path + ".tmp";
    {
        std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
        if (!out) throw std::runtime_error("state-write");
        out << "{\"credentials\":[";
        for (std::size_t i = 0; i < state.credentials.size(); ++i) {
            if (i) out << ',';
            out << "{\"credential_id\":\"" << state.credentials[i].credential_id << "\",\"aaguid\":\""
                << state.credentials[i].aaguid << "\",\"sign_count\":" << state.credentials[i].sign_count << '}';
        }
        out << "],\"audit\":[";
        for (std::size_t i = 0; i < state.audit.size(); ++i) {
            if (i) out << ',';
            const AuditEntry &e = state.audit[i];
            out << "{\"seq\":" << e.seq << ",\"event_hash\":\"" << e.event_hash
                << "\",\"status\":\"" << e.status << "\",\"credential_id\":\"" << e.credential_id
                << "\",\"sign_count\":" << e.sign_count << ",\"prev_hash\":\"" << e.prev_hash
                << "\",\"entry_hash\":\"" << e.entry_hash << "\"}";
        }
        out << "]}\n";
    }
    std::filesystem::rename(tmp, path);
}

static std::string arg_value(int argc, char **argv, const std::string &name) {
    for (int i = 1; i + 1 < argc; ++i) if (argv[i] == name) return argv[i + 1];
    return "";
}

int main(int argc, char **argv) {
    std::string policy_path = arg_value(argc, argv, "--policy");
    std::string event_path = arg_value(argc, argv, "--event");
    std::string state_path = arg_value(argc, argv, "--state");
    std::string report_path = arg_value(argc, argv, "--out");
    if (policy_path.empty() || event_path.empty() || state_path.empty() || report_path.empty()) return 2;

    try {
        std::string policy_raw = read_file(policy_path);
        std::string event_raw = read_file(event_path);
        std::string state_raw = read_file(state_path);
        Policy policy = parse_policy(JsonParser(policy_raw).parse());
        Json event = JsonParser(event_raw).parse();
        State state = parse_state(JsonParser(state_raw).parse());
        std::string kind = string_field(event, "kind");
        std::string credential_hex = bytes_to_hex(hex_to_bytes(string_field(event, "credential_id")));
        std::string client_text;
        {
            auto client_bytes = base64url_decode(string_field(event, "client_data_json"));
            client_text.assign(client_bytes.begin(), client_bytes.end());
        }
        Json client = JsonParser(client_text).parse();
        std::string expected_type = kind == "registration" ? "webauthn.create" : "webauthn.get";
        if (kind != "registration" && kind != "authentication") throw std::runtime_error("kind");
        std::vector<std::string> violations;
        if (string_field(client, "type") != expected_type) violations.push_back("client-type");
        if (!contains(policy.challenges, string_field(client, "challenge"))) violations.push_back("challenge");
        if (!contains(policy.origins, string_field(client, "origin"))) violations.push_back("origin");

        std::vector<uint8_t> auth_bytes = hex_to_bytes(string_field(event, "authenticator_data"));
        AuthData ad = parse_auth_data(auth_bytes);
        if (ad.rp_hash != sha256_string(policy.rp_id)) violations.push_back("rp-id");
        if ((ad.flags & 0x01) == 0) violations.push_back("user-present");
        if ((ad.flags & 0x04) == 0) violations.push_back("user-verified");

        if (kind == "registration") {
            if (!ad.has_attested) violations.push_back("attested-data");
            Attestation att = parse_attestation(hex_to_bytes(string_field(event, "attestation_object")));
            std::string ad_aaguid = bytes_to_hex(ad.aaguid);
            std::string att_aaguid = bytes_to_hex(att.aaguid);
            std::string ad_cred = bytes_to_hex(ad.credential_id);
            std::string att_cred = bytes_to_hex(att.credential_id);
            if (att.fmt != "packed") violations.push_back("attestation-format");
            if (ad_aaguid != att_aaguid || ad_aaguid.empty()) violations.push_back("aaguid");
            if (credential_hex != ad_cred || credential_hex != att_cred) violations.push_back("credential-id");
            const Trust *trust = trust_for(policy, ad_aaguid);
            if (!trust) violations.push_back("trust");
            else {
                std::vector<uint8_t> base = auth_bytes;
                std::vector<uint8_t> client_hash = sha256_string(client_text);
                std::vector<uint8_t> audit_hash = hex_to_bytes(state.current_hash);
                base.insert(base.end(), client_hash.begin(), client_hash.end());
                base.insert(base.end(), att.nonce.begin(), att.nonce.end());
                base.insert(base.end(), audit_hash.begin(), audit_hash.end());
                if (hmac_sha256(trust->attestation_key, base) != att.sig) violations.push_back("attestation-signature");
            }
            for (const Credential &c : state.credentials) if (c.credential_id == credential_hex) violations.push_back("credential-exists");
            if (!violations.empty()) {
                write_report(report_path, false, "rejected", credential_hex, ad.counter, state.current_hash, violations);
                return 0;
            }
            AuditEntry audit = make_audit_entry(state, event_raw, credential_hex, ad.counter, "registered");
            state.credentials.push_back(Credential{credential_hex, ad_aaguid, ad.counter});
            state.audit.push_back(audit);
            state.current_hash = audit.entry_hash;
            write_state_atomic(state_path, state);
            write_report(report_path, true, "registered", credential_hex, ad.counter, audit.entry_hash, {});
            return 0;
        }

        if (ad.has_attested) violations.push_back("attested-data");
        auto it = std::find_if(state.credentials.begin(), state.credentials.end(), [&](const Credential &c) { return c.credential_id == credential_hex; });
        const Trust *trust = nullptr;
        if (it == state.credentials.end()) violations.push_back("credential-unknown");
        else {
            trust = trust_for(policy, it->aaguid);
            if (!trust) violations.push_back("trust");
            if (ad.counter <= it->sign_count) violations.push_back("sign-count");
        }
        if (trust) {
            std::vector<uint8_t> base = hex_to_bytes(credential_hex);
            base.insert(base.end(), auth_bytes.begin(), auth_bytes.end());
            std::vector<uint8_t> client_hash = sha256_string(client_text);
            std::vector<uint8_t> audit_hash = hex_to_bytes(state.current_hash);
            base.insert(base.end(), client_hash.begin(), client_hash.end());
            base.insert(base.end(), audit_hash.begin(), audit_hash.end());
            if (hmac_sha256(trust->assertion_key, base) != hex_to_bytes(string_field(event, "assertion_signature"))) {
                violations.push_back("assertion-signature");
            }
        }
        if (!violations.empty()) {
            write_report(report_path, false, "rejected", credential_hex, ad.counter, state.current_hash, violations);
            return 0;
        }
        it->sign_count = ad.counter;
        AuditEntry audit = make_audit_entry(state, event_raw, credential_hex, ad.counter, "authenticated");
        state.audit.push_back(audit);
        state.current_hash = audit.entry_hash;
        write_state_atomic(state_path, state);
        write_report(report_path, true, "authenticated", credential_hex, ad.counter, audit.entry_hash, {});
        return 0;
    } catch (const std::exception &) {
        try { write_report(report_path, false, "malformed", "", 0, "", {"malformed"}); } catch (...) {}
        return 2;
    }
}
CPP

make -C /app/webauthn_gate clean all
