#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

using Bytes = std::vector<uint8_t>;

static uint16_t u16(const Bytes& b, size_t p) { return (uint16_t(b[p]) << 8) | b[p + 1]; }
static uint32_t u32(const Bytes& b, size_t p) { return (uint32_t(b[p]) << 24) | (uint32_t(b[p + 1]) << 16) | (uint32_t(b[p + 2]) << 8) | b[p + 3]; }

struct Peer { int index{}; uint32_t asn{}; std::string id; std::string address; bool ibgp{}; };
struct Attrs { int local_pref = 100; int origin = 2; std::optional<uint32_t> med; std::vector<uint32_t> as_path; std::vector<std::pair<int, Bytes>> reach; std::vector<std::pair<int, Bytes>> unreach; };
struct Cand { uint32_t peer_as{}; std::string peer_address; bool ibgp{}; int local_pref{}; int origin{}; std::optional<uint32_t> med; std::vector<uint32_t> as_path; uint32_t timestamp{}; uint64_t sequence{}; };

static std::string ip4(const Bytes& b, size_t p) {
    std::ostringstream o; o << int(b[p]) << "." << int(b[p+1]) << "." << int(b[p+2]) << "." << int(b[p+3]); return o.str();
}
static std::string ip6(const Bytes& b, size_t p) {
    std::ostringstream o; o << std::hex << std::nouppercase;
    for (int i=0;i<8;i++){ if(i) o << ':'; o << std::setw(4) << std::setfill('0') << ((int(b[p+i*2])<<8)|int(b[p+i*2+1])); }
    return o.str();
}
static std::string pfx(int afi, const Bytes& raw, int plen) {
    Bytes b = raw; b.resize(afi == 1 ? 4 : 16, 0);
    if (afi == 1) return ip4(b, 0) + "/" + std::to_string(plen);
    return ip6(b, 0) + "/" + std::to_string(plen);
}
static std::string origin_name(int v){ return v==0?"igp":(v==1?"egp":"incomplete"); }

static std::vector<uint32_t> parse_path(const Bytes& v, bool as4) {
    std::vector<uint32_t> out; size_t p=0, w=as4?4:2;
    while (p + 2 <= v.size()) {
        int typ = v[p++], cnt = v[p++];
        if (p + cnt*w > v.size()) break;
        if (typ == 1) {
            uint32_t x = as4 ? u32(v,p) : u16(v,p); out.push_back(x); p += cnt*w;
        } else {
            for (int i=0;i<cnt;i++,p+=w) out.push_back(as4 ? u32(v,p) : u16(v,p));
        }
    }
    return out;
}

static Attrs attrs(const Bytes& b, size_t off, size_t len) {
    Attrs a; size_t p=off, end=off+len;
    while (p + 3 <= end) {
        uint8_t flags=b[p++], code=b[p++];
        size_t l=b[p++];
        if (flags & 0x10) { if (p >= end) break; l = (l << 8) | b[p++]; }
        if (p + l > end) break;
        Bytes v(b.begin()+p, b.begin()+p+l); p += l;
        if (code == 1 && !v.empty()) a.origin = v[0];
        else if (code == 2) a.as_path = parse_path(v, false);
        else if (code == 4 && v.size() >= 4) a.med = u32(v,0);
        else if (code == 5 && v.size() >= 4) a.local_pref = int(u32(v,0));
        else if (code == 14 && v.size() >= 5) {
            int afi = u16(v,0); size_t q=4+v[3]; if (q < v.size()) { q++; while (q < v.size()) { int pl=v[q++]; int n=(pl+7)/8; if(q+n>v.size()) break; a.reach.push_back({afi, Bytes(v.begin()+q, v.begin()+q+n)}); q+=n; } }
        } else if (code == 15 && v.size() >= 3) {
            int afi = u16(v,0); size_t q=3; while (q < v.size()) { int pl=v[q++]; int n=(pl+7)/8; if(q+n>v.size()) break; a.unreach.push_back({afi, Bytes(v.begin()+q, v.begin()+q+n)}); q+=n; }
        }
    }
    return a;
}

static bool better(const Cand& a, const Cand& b) {
    if (a.local_pref != b.local_pref) return a.local_pref > b.local_pref;
    if (a.as_path.size() != b.as_path.size()) return a.as_path.size() < b.as_path.size();
    if (a.origin != b.origin) return a.origin < b.origin;
    if (a.med.value_or(0) != b.med.value_or(0)) return a.med.value_or(0) < b.med.value_or(0);
    if (a.ibgp != b.ibgp) return !a.ibgp;
    if (a.peer_address != b.peer_address) return a.peer_address < b.peer_address;
    return a.sequence < b.sequence;
}

int main(int argc, char** argv) {
    if (argc != 3) return 2;
    std::ifstream in(argv[1], std::ios::binary); Bytes data((std::istreambuf_iterator<char>(in)), {});
    std::vector<Peer> peers; std::map<std::string, std::vector<Cand>> routes;
    uint64_t seq=0; int seen=0, applied=0, ignored=0, malformed=0;
    size_t pos=0;
    while (pos < data.size()) {
        if (pos + 12 > data.size()) { malformed++; break; }
        uint32_t ts=u32(data,pos); uint16_t type=u16(data,pos+4), sub=u16(data,pos+6); uint32_t len=u32(data,pos+8); pos += 12; seen++;
        if (pos + len > data.size()) { malformed++; break; }
        Bytes p(data.begin()+pos, data.begin()+pos+len); pos += len;
        try {
            if (type == 13 && sub == 1) {
                size_t q=4; int vl=u16(p,q); q += 2 + vl; int n=u16(p,q); q += 2; peers.clear();
                for (int i=0;i<n;i++) { uint8_t t=p[q++]; std::string id=ip4(p,q); q+=4; bool v6=t&1, as4=t&2; std::string addr=v6?ip6(p,q):ip4(p,q); q += v6?16:4; uint32_t asn=as4?u32(p,q):u16(p,q); q += as4?4:2; peers.push_back({i,asn,id,addr,bool(t&4)}); }
                applied++;
            } else if (type == 13 && (sub == 2 || sub == 4)) {
                int afi = sub == 2 ? 1 : 2; size_t q=4; int plen=p[q++]; int nb=(plen+7)/8; Bytes pr(p.begin()+q,p.begin()+q+nb); q+=nb; std::string pref=pfx(afi,pr,plen); int ec=u16(p,q); q+=2;
                for (int i=0;i<ec;i++) { int pi=u16(p,q); q+=2; uint32_t ots=u32(p,q); q+=4; int al=u16(p,q); q+=2; Attrs a=attrs(p,q,al); q+=al; if (pi >= 0 && pi < (int)peers.size()) routes[pref].push_back({peers[pi].asn,peers[pi].address,peers[pi].ibgp,a.local_pref,a.origin,a.med,a.as_path,ots,++seq}); }
                applied++;
            } else if (type == 16 && sub == 1) {
                size_t q=0; uint32_t pas=u32(p,q); q+=4; q+=4; q+=2; int afi=u16(p,q); q+=2; std::string paddr = afi==1?ip4(p,q):ip6(p,q); q += afi==1?4:16; q += afi==1?4:16; if (p[q++] != 2) throw std::runtime_error("x"); int wl=u16(p,q); q+=2; std::vector<std::string> wd; size_t we=q+wl; while(q<we){int pl=p[q++]; int nb=(pl+7)/8; Bytes pr(p.begin()+q,p.begin()+q+nb); q+=nb; wd.push_back(pfx(1,pr,pl));} int al=u16(p,q); q+=2; Attrs a=attrs(p,q,al); q+=al; for(auto& s:wd){ auto& v=routes[s]; v.erase(std::remove_if(v.begin(),v.end(),[&](const Cand& c){return c.peer_address==paddr && c.peer_as==pas;}),v.end()); }
                while(q<p.size()){int pl=p[q++]; int nb=(pl+7)/8; Bytes pr(p.begin()+q,p.begin()+q+nb); q+=nb; routes[pfx(1,pr,pl)].push_back({pas,paddr,false,a.local_pref,a.origin,a.med,a.as_path,ts,++seq});}
                applied++;
            } else ignored++;
        } catch (...) { malformed++; }
    }
    for (auto it=routes.begin(); it!=routes.end();) { if (it->second.empty()) it=routes.erase(it); else ++it; }
    std::ofstream out(argv[2]);
    out << "{\"input\":\"" << argv[1] << "\",\"records\":{\"seen\":"<<seen<<",\"applied\":"<<applied<<",\"ignored\":"<<ignored<<",\"malformed\":"<<malformed<<"},\"peers\":[";
    for(size_t i=0;i<peers.size();i++){ if(i) out<<','; auto& p=peers[i]; out << "{\"index\":"<<p.index<<",\"as\":"<<p.asn<<",\"bgp_id\":\""<<p.id<<"\",\"address\":\""<<p.address<<"\",\"ibgp\":"<<(p.ibgp?"true":"false")<<"}"; }
    out << "],\"routes\":["; bool first=true; int selected=0;
    for(auto& [pref, vec]: routes){ std::sort(vec.begin(),vec.end(),better); if(!first) out<<','; first=false; selected++; auto emit=[&](const Cand& c){ out << "{\"peer_as\":"<<c.peer_as<<",\"peer_address\":\""<<c.peer_address<<"\",\"local_pref\":"<<c.local_pref<<",\"as_path\":["; for(size_t i=0;i<c.as_path.size();i++){if(i)out<<','; out<<c.as_path[i];} out << "],\"as_path_len\":"<<c.as_path.size()<<",\"origin\":\""<<origin_name(c.origin)<<"\",\"med\":"; if(c.med) out<<*c.med; else out<<"null"; out << ",\"source\":\""<<(c.ibgp?"ibgp":"ebgp")<<"\",\"timestamp\":"<<c.timestamp<<",\"sequence\":"<<c.sequence<<"}";}; out << "{\"prefix\":\""<<pref<<"\",\"selected\":"; emit(vec[0]); out << ",\"candidates\":["; for(size_t i=0;i<vec.size();i++){if(i)out<<','; emit(vec[i]);} out << "]}"; }
    out << "],\"selected_count\":"<<selected<<"}\n";
}
