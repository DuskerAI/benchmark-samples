#include <algorithm>
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

using Bytes = std::vector<uint8_t>;

static uint16_t be16(const Bytes& b, size_t p) { return (uint16_t(b.at(p)) << 8) | b.at(p + 1); }
static uint32_t be32(const Bytes& b, size_t p) { return (uint32_t(b.at(p)) << 24) | (uint32_t(b.at(p + 1)) << 16) | (uint32_t(b.at(p + 2)) << 8) | b.at(p + 3); }

struct Reader {
    const Bytes& b;
    size_t p = 0;
    explicit Reader(const Bytes& bytes) : b(bytes) {}
    size_t left() const { return b.size() - p; }
    uint8_t u8() { if (left() < 1) throw std::runtime_error("short"); return b[p++]; }
    uint16_t u16() { if (left() < 2) throw std::runtime_error("short"); uint16_t v = be16(b, p); p += 2; return v; }
    uint32_t u32() { if (left() < 4) throw std::runtime_error("short"); uint32_t v = be32(b, p); p += 4; return v; }
    Bytes bytes(size_t n) { if (left() < n) throw std::runtime_error("short"); Bytes out(b.begin() + p, b.begin() + p + n); p += n; return out; }
    void skip(size_t n) { if (left() < n) throw std::runtime_error("short"); p += n; }
};

struct NLRI { int afi; int plen; Bytes bytes; };
struct Peer { int index{}; uint32_t asn{}; std::string bgp_id; std::string address; Bytes address_bytes; bool ibgp{}; };
struct Attrs { int local_pref = 100; int origin = 2; std::optional<uint32_t> med; std::vector<uint32_t> as_path; std::vector<NLRI> reach; std::vector<NLRI> unreach; };
struct Candidate { uint32_t peer_as{}; std::string peer_address; Bytes peer_bytes; bool ibgp{}; int local_pref{}; int origin{}; std::optional<uint32_t> med; std::vector<uint32_t> as_path; uint32_t timestamp{}; uint64_t sequence{}; };
struct Route { int afi{}; int plen{}; Bytes network; std::map<std::string, Candidate> candidates; };

static std::string ipv4(const Bytes& b) {
    if (b.size() != 4) throw std::runtime_error("ip4");
    std::ostringstream o; o << int(b[0]) << "." << int(b[1]) << "." << int(b[2]) << "." << int(b[3]); return o.str();
}
static std::string ipv6(const Bytes& b) {
    if (b.size() != 16) throw std::runtime_error("ip6");
    std::ostringstream o; o << std::hex << std::nouppercase;
    for (int i = 0; i < 8; ++i) { if (i) o << ':'; o << std::setw(4) << std::setfill('0') << ((int(b[i * 2]) << 8) | int(b[i * 2 + 1])); }
    return o.str();
}
static std::string ip_string(int afi, const Bytes& b) { return afi == 1 ? ipv4(b) : ipv6(b); }
static Bytes normalized(int afi, const Bytes& raw, int plen) {
    if (plen < 0 || plen > (afi == 1 ? 32 : 128)) throw std::runtime_error("plen");
    size_t need = (plen + 7) / 8;
    if (raw.size() != need) throw std::runtime_error("prefix");
    Bytes out(afi == 1 ? 4 : 16, 0);
    std::copy(raw.begin(), raw.end(), out.begin());
    if (plen % 8 && need) out[need - 1] &= uint8_t(0xff << (8 - (plen % 8)));
    return out;
}
static std::string prefix_text(int afi, const Bytes& network, int plen) { return ip_string(afi, network) + "/" + std::to_string(plen); }
static std::string route_key(int afi, const Bytes& network, int plen) { return std::to_string(afi) + "|" + std::string(network.begin(), network.end()) + "|" + std::to_string(plen); }
static std::string peer_key(uint32_t asn, const std::string& addr) { return std::to_string(asn) + "@" + addr; }
static std::string origin_name(int v) { return v == 0 ? "igp" : (v == 1 ? "egp" : "incomplete"); }

static std::vector<uint32_t> path_from_segments(const Bytes& v, bool as4) {
    Reader r(v); std::vector<uint32_t> out; size_t width = as4 ? 4 : 2;
    while (r.left()) {
        uint8_t typ = r.u8(); uint8_t cnt = r.u8();
        if (r.left() < cnt * width) throw std::runtime_error("path");
        if (typ == 1) {
            uint32_t first = as4 ? r.u32() : r.u16();
            out.push_back(first);
            if (cnt > 1) r.skip((cnt - 1) * width);
        } else if (typ == 2) {
            for (int i = 0; i < cnt; ++i) out.push_back(as4 ? r.u32() : r.u16());
        } else {
            r.skip(cnt * width);
        }
    }
    return out;
}
static std::vector<uint32_t> merged_path(const std::vector<uint32_t>& as_path, const std::vector<uint32_t>& as4_path) {
    if (as_path.empty()) return as4_path;
    std::vector<uint32_t> out = as_path; size_t j = 0;
    for (uint32_t& v : out) if (v == 23456 && j < as4_path.size()) v = as4_path[j++];
    return out;
}
static std::vector<NLRI> parse_nlri(int afi, const Bytes& bytes) {
    Reader r(bytes); std::vector<NLRI> out;
    while (r.left()) { int plen = r.u8(); size_t n = (plen + 7) / 8; out.push_back({afi, plen, r.bytes(n)}); }
    return out;
}
static Attrs parse_attrs(const Bytes& bytes) {
    Reader r(bytes); Attrs a; std::vector<uint32_t> as_path, as4_path;
    while (r.left()) {
        uint8_t flags = r.u8(); uint8_t code = r.u8(); size_t len = (flags & 0x10) ? r.u16() : r.u8(); Bytes v = r.bytes(len);
        if (code == 1 && v.size() == 1) a.origin = v[0];
        else if (code == 2) as_path = path_from_segments(v, false);
        else if (code == 4 && v.size() == 4) a.med = be32(v, 0);
        else if (code == 5 && v.size() == 4) a.local_pref = int(be32(v, 0));
        else if (code == 17) as4_path = path_from_segments(v, true);
        else if (code == 14) {
            Reader m(v); int afi = m.u16(); if (m.u8() != 1) throw std::runtime_error("safi"); size_t nh = m.u8(); m.skip(nh); size_t snpa = m.u8(); m.skip(snpa); Bytes rest = m.bytes(m.left()); auto xs = parse_nlri(afi, rest); a.reach.insert(a.reach.end(), xs.begin(), xs.end());
        } else if (code == 15) {
            Reader m(v); int afi = m.u16(); if (m.u8() != 1) throw std::runtime_error("safi"); Bytes rest = m.bytes(m.left()); auto xs = parse_nlri(afi, rest); a.unreach.insert(a.unreach.end(), xs.begin(), xs.end());
        }
    }
    a.as_path = merged_path(as_path, as4_path);
    return a;
}

static bool less_bytes(const Bytes& a, const Bytes& b) { return std::lexicographical_compare(a.begin(), a.end(), b.begin(), b.end()); }
static uint32_t neighbor_as(const Candidate& c) { return c.as_path.empty() ? c.peer_as : c.as_path.front(); }
static bool better_candidate(const Candidate& a, const Candidate& b) {
    if (a.local_pref != b.local_pref) return a.local_pref > b.local_pref;
    if (a.as_path.size() != b.as_path.size()) return a.as_path.size() < b.as_path.size();
    if (a.origin != b.origin) return a.origin < b.origin;
    if (neighbor_as(a) == neighbor_as(b) && a.med.value_or(0) != b.med.value_or(0)) return a.med.value_or(0) < b.med.value_or(0);
    if (a.ibgp != b.ibgp) return !a.ibgp;
    if (a.peer_bytes != b.peer_bytes) return less_bytes(a.peer_bytes, b.peer_bytes);
    if (a.sequence != b.sequence) return a.sequence < b.sequence;
    return a.timestamp < b.timestamp;
}

static void upsert_route(std::map<std::string, Route>& routes, int afi, int plen, const Bytes& raw, const Candidate& c) {
    Bytes net = normalized(afi, raw, plen); std::string k = route_key(afi, net, plen);
    auto& r = routes[k]; r.afi = afi; r.plen = plen; r.network = net; r.candidates[peer_key(c.peer_as, c.peer_address)] = c;
}
static void withdraw_route(std::map<std::string, Route>& routes, int afi, int plen, const Bytes& raw, uint32_t asn, const std::string& addr) {
    Bytes net = normalized(afi, raw, plen); auto it = routes.find(route_key(afi, net, plen));
    if (it == routes.end()) return;
    it->second.candidates.erase(peer_key(asn, addr));
    if (it->second.candidates.empty()) routes.erase(it);
}

static Peer peer_from_bgp4mp(const std::vector<Peer>& peers, uint32_t asn, const std::string& addr, const Bytes& raw) {
    for (const auto& p : peers) if (p.asn == asn && p.address == addr) return p;
    return Peer{-1, asn, "", addr, raw, false};
}
static Candidate make_candidate(const Peer& p, const Attrs& a, uint32_t ts, uint64_t seq) {
    return Candidate{p.asn, p.address, p.address_bytes, p.ibgp, a.local_pref, a.origin, a.med, a.as_path, ts, seq};
}

int main(int argc, char** argv) {
    if (argc != 3) return 2;
    std::ifstream in(argv[1], std::ios::binary); if (!in) return 2;
    Bytes data((std::istreambuf_iterator<char>(in)), {});
    std::vector<Peer> peers; std::map<std::string, Route> routes;
    uint64_t replay_sequence = 0; int seen = 0, applied = 0, ignored = 0, malformed = 0;
    size_t pos = 0;
    while (pos < data.size()) {
        if (pos + 12 > data.size()) { malformed++; break; }
        uint32_t ts = be32(data, pos); uint16_t typ = be16(data, pos + 4), sub = be16(data, pos + 6); uint32_t len = be32(data, pos + 8); pos += 12; seen++;
        if (pos + len > data.size()) { malformed++; break; }
        Bytes payload(data.begin() + pos, data.begin() + pos + len); pos += len;
        try {
            if (typ == 13 && sub == 1) {
                Reader r(payload); r.u32(); size_t view_len = r.u16(); r.skip(view_len); int count = r.u16(); std::vector<Peer> next;
                for (int i = 0; i < count; ++i) {
                    uint8_t t = r.u8(); Bytes bgp = r.bytes(4); int afi = (t & 1) ? 2 : 1; Bytes addr = r.bytes(afi == 1 ? 4 : 16); uint32_t asn = (t & 2) ? r.u32() : r.u16();
                    next.push_back(Peer{i, asn, ipv4(bgp), ip_string(afi, addr), addr, bool(t & 4)});
                }
                if (r.left()) throw std::runtime_error("tail");
                peers = next;
                routes.clear();
                applied++;
            } else if (typ == 13 && (sub == 2 || sub == 4)) {
                int afi = sub == 2 ? 1 : 2; Reader r(payload); r.u32(); int plen = r.u8(); Bytes raw = r.bytes((plen + 7) / 8); int ec = r.u16(); std::vector<Candidate> pending;
                for (int i = 0; i < ec; ++i) {
                    int pi = r.u16(); if (pi < 0 || pi >= int(peers.size())) throw std::runtime_error("peer"); uint32_t ots = r.u32(); size_t alen = r.u16(); Attrs a = parse_attrs(r.bytes(alen)); pending.push_back(make_candidate(peers[pi], a, ots, ++replay_sequence));
                }
                if (r.left()) throw std::runtime_error("tail");
                for (const auto& c : pending) upsert_route(routes, afi, plen, raw, c);
                applied++;
            } else if (typ == 16 && sub == 1) {
                Reader r(payload); uint32_t pas = r.u32(); r.u32(); r.u16(); int afi = r.u16(); if (afi != 1 && afi != 2) throw std::runtime_error("afi"); Bytes paddr_raw = r.bytes(afi == 1 ? 4 : 16); std::string paddr = ip_string(afi, paddr_raw); r.skip(afi == 1 ? 4 : 16); if (r.u8() != 2) throw std::runtime_error("msg"); size_t wlen = r.u16(); Bytes w = r.bytes(wlen); size_t alen = r.u16(); Attrs a = parse_attrs(r.bytes(alen)); Bytes nlri = r.bytes(r.left()); Peer p = peer_from_bgp4mp(peers, pas, paddr, paddr_raw);
                for (const auto& n : parse_nlri(1, w)) withdraw_route(routes, 1, n.plen, n.bytes, pas, paddr);
                for (const auto& n : a.unreach) withdraw_route(routes, n.afi, n.plen, n.bytes, pas, paddr);
                for (const auto& n : parse_nlri(1, nlri)) upsert_route(routes, 1, n.plen, n.bytes, make_candidate(p, a, ts, ++replay_sequence));
                for (const auto& n : a.reach) upsert_route(routes, n.afi, n.plen, n.bytes, make_candidate(p, a, ts, ++replay_sequence));
                applied++;
            } else {
                ignored++;
            }
        } catch (...) { malformed++; }
    }

    std::vector<const Route*> ordered_routes;
    for (auto& [_, r] : routes) if (!r.candidates.empty()) ordered_routes.push_back(&r);
    std::sort(ordered_routes.begin(), ordered_routes.end(), [](const Route* a, const Route* b) {
        if (a->afi != b->afi) return a->afi < b->afi;
        if (a->network != b->network) return less_bytes(a->network, b->network);
        return a->plen < b->plen;
    });
    std::ofstream out(argv[2]); if (!out) return 2;
    out << "{\"input\":\"" << argv[1] << "\",\"records\":{\"seen\":" << seen << ",\"applied\":" << applied << ",\"ignored\":" << ignored << ",\"malformed\":" << malformed << "},\"peers\":[";
    for (size_t i = 0; i < peers.size(); ++i) { if (i) out << ','; const auto& p = peers[i]; out << "{\"index\":" << p.index << ",\"as\":" << p.asn << ",\"bgp_id\":\"" << p.bgp_id << "\",\"address\":\"" << p.address << "\",\"ibgp\":" << (p.ibgp ? "true" : "false") << "}"; }
    auto emit_candidate = [&](const Candidate& c) {
        out << "{\"peer_as\":" << c.peer_as << ",\"peer_address\":\"" << c.peer_address << "\",\"local_pref\":" << c.local_pref << ",\"as_path\":[";
        for (size_t i = 0; i < c.as_path.size(); ++i) { if (i) out << ','; out << c.as_path[i]; }
        out << "],\"as_path_len\":" << c.as_path.size() << ",\"origin\":\"" << origin_name(c.origin) << "\",\"med\":";
        if (c.med) out << *c.med; else out << "null";
        out << ",\"source\":\"" << (c.ibgp ? "ibgp" : "ebgp") << "\",\"timestamp\":" << c.timestamp << ",\"sequence\":" << c.sequence << "}";
    };
    out << "],\"routes\":[";
    for (size_t i = 0; i < ordered_routes.size(); ++i) {
        if (i) out << ',';
        const Route& r = *ordered_routes[i]; std::vector<Candidate> cs; for (const auto& [_, c] : r.candidates) cs.push_back(c); std::sort(cs.begin(), cs.end(), better_candidate);
        out << "{\"prefix\":\"" << prefix_text(r.afi, r.network, r.plen) << "\",\"selected\":"; emit_candidate(cs.front()); out << ",\"candidates\":[";
        for (size_t j = 0; j < cs.size(); ++j) { if (j) out << ','; emit_candidate(cs[j]); }
        out << "]}";
    }
    out << "],\"selected_count\":" << ordered_routes.size() << "}\n";
    return 0;
}
