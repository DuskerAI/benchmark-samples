#include <algorithm>
#include <cctype>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
using namespace std;
namespace fs = std::filesystem;

struct Header { string name; string value; };
struct Frame { int seq; int stream; string type; string flags; vector<uint8_t> payload; };
struct Pending { bool active=false; vector<uint8_t> bytes; };
struct Finding { string capture; int stream; int ordinal; string type; string name; };
static vector<Header> dyn;
static size_t dynSize=0, maxDyn=4096;

static const vector<Header> stat = {
{"", ""},{":authority",""},{":method","GET"},{":method","POST"},{":path","/"},{":path","/index.html"},{":scheme","http"},{":scheme","https"},{":status","200"},{":status","204"},{":status","206"},{":status","304"},{":status","400"},{":status","404"},{":status","500"},{"accept-charset",""},{"accept-encoding","gzip, deflate"},{"accept-language",""},{"accept-ranges",""},{"accept",""},{"access-control-allow-origin",""},{"age",""},{"allow",""},{"authorization",""},{"cache-control",""},{"content-disposition",""},{"content-encoding",""},{"content-language",""},{"content-length",""},{"content-location",""},{"content-range",""},{"content-type",""},{"cookie",""},{"date",""},{"etag",""},{"expect",""},{"expires",""},{"from",""},{"host",""},{"if-match",""},{"if-modified-since",""},{"if-none-match",""},{"if-range",""},{"if-unmodified-since",""},{"last-modified",""},{"link",""},{"location",""},{"max-forwards",""},{"proxy-authenticate",""},{"proxy-authorization",""},{"range",""},{"referer",""},{"refresh",""},{"retry-after",""},{"server",""},{"set-cookie",""},{"strict-transport-security",""},{"transfer-encoding",""},{"user-agent",""},{"vary",""},{"via",""},{"www-authenticate",""}
};

static string esc(const string& s){ string r; for(char c:s){ if(c=='\\'||c=='"'){r+='\\';r+=c;} else if((unsigned char)c<32){r+="?";} else r+=c;} return r; }
static vector<uint8_t> hexv(const string& s){ vector<uint8_t> out; for(size_t i=0;i+1<s.size();i+=2) out.push_back((uint8_t)strtoul(s.substr(i,2).c_str(),nullptr,16)); return out; }
static bool hasEnd(const string& f){ return f.find("END_HEADERS")!=string::npos; }
static string lower(string s){ for(char& c:s)c=(char)tolower((unsigned char)c); return s; }
static size_t entrySize(const Header& h){ return h.name.size()+h.value.size()+32; }
static void evict(){ while(dynSize>maxDyn && !dyn.empty()){ dynSize-=entrySize(dyn.back()); dyn.pop_back(); } }
static void addDyn(const Header& h){ dyn.insert(dyn.begin(),h); dynSize+=entrySize(h); evict(); }
static uint64_t readInt(const vector<uint8_t>& b, size_t& p, int bits){ if(p>=b.size()) throw runtime_error("short integer"); uint8_t m=(1u<<bits)-1; uint64_t v=b[p]&m; p++; if(v==m && p<b.size()){ v += b[p++] & 127; } return v; }
static Header byIndex(uint64_t idx){ if(idx==0) throw runtime_error("zero index"); if(idx<stat.size()) return stat[idx]; size_t d=(size_t)(idx-stat.size()); if(d>=dyn.size()) throw runtime_error("bad index"); return dyn[d]; }
static string huff(const vector<uint8_t>& in){ string s; for(uint8_t c:in){ if(c==0xff) continue; s.push_back((char)c); } return s; }
static string strlit(const vector<uint8_t>& b, size_t& p){ if(p>=b.size()) throw runtime_error("short string"); bool hf=b[p]&0x80; uint64_t n=readInt(b,p,7); if(p+n>b.size()) throw runtime_error("short string data"); vector<uint8_t> data(b.begin()+p,b.begin()+p+n); p+=n; if(hf) return huff(data); return string(data.begin(), data.end()); }
static vector<Header> decode(const vector<uint8_t>& b){ vector<Header> out; size_t p=0; while(p<b.size()){ uint8_t x=b[p]; if(x&0x80){ uint64_t idx=readInt(b,p,7); out.push_back(byIndex(idx)); } else if((x&0xe0)==0x20){ uint64_t n=readInt(b,p,5); maxDyn=n; evict(); } else { bool inc=(x&0x40)!=0; bool never=(x&0x10)!=0; int bits=inc?6:4; uint64_t idx=readInt(b,p,bits); string name=idx?byIndex(idx).name:strlit(b,p); string val=strlit(b,p); Header h{name,val}; out.push_back(h); if(inc && !never) addDyn(h); } } return out; }
static vector<Frame> readFrames(const fs::path& path){ ifstream in(path); string line; vector<Frame> v; while(getline(in,line)){ if(line.empty()||line[0]=='#') continue; istringstream ss(line); Frame f; string hx; if(ss>>f.seq>>f.stream>>f.type>>f.flags>>hx){ f.payload=hexv(hx); v.push_back(f); } } sort(v.begin(),v.end(),[](auto&a,auto&b){return a.seq<b.seq;}); return v; }

int main(int argc,char**argv){ if(argc!=2){ cerr<<"config required\n"; return 2; } map<string,string> cfg; { ifstream in(argv[1]); string l; while(getline(in,l)){ auto p=l.find('='); if(p!=string::npos) cfg[l.substr(0,p)]=l.substr(p+1); }} string root=cfg["capture_root"], od=cfg["output_dir"]; maxDyn=stoull(cfg["max_table_size"]); fs::remove_all(od); fs::create_directories(od); ofstream ho(od+"/headers.jsonl"), dia(od+"/stream_diagnostics.jsonl"); vector<Finding> findings; map<string,int> summary; vector<fs::path> files; for(auto& e:fs::directory_iterator(root)) if(e.path().extension()==".h2c") files.push_back(e.path()); sort(files.begin(),files.end()); for(auto& path:files){ string cap=path.filename().string(); dyn.clear(); dynSize=0; map<int,Pending> pend; map<int,int> ord; for(auto& f:readFrames(path)){ try{ if(f.type=="HEADERS"){ pend[f.stream]=Pending{true,f.payload}; } else if(f.type=="CONTINUATION" && pend[f.stream].active){ pend[f.stream].bytes.insert(pend[f.stream].bytes.end(),f.payload.begin(),f.payload.end()); } else { throw runtime_error("fragment"); } if(hasEnd(f.flags)){ auto bytes=pend[f.stream].bytes; pend[f.stream].active=false; auto hs=decode(bytes); int o=ord[f.stream]++; set<string> pseudos; ho<<"{\"capture\":\""<<esc(cap)<<"\",\"stream\":"<<f.stream<<",\"ordinal\":"<<o<<",\"headers\":["; for(size_t i=0;i<hs.size();++i){ string orig=hs[i].name, nm=lower(orig); if(i)ho<<","; ho<<"{\"name\":\""<<esc(nm)<<"\",\"value\":\""<<esc(hs[i].value)<<"\"}"; if(orig!=nm){ findings.push_back({cap,f.stream,o,"uppercase-name",nm}); summary["uppercase-name"]++; } if(!nm.empty()&&nm[0]==':'){ if(pseudos.count(nm)){ findings.push_back({cap,f.stream,o,"duplicate-pseudo-header",nm}); summary["duplicate-pseudo-header"]++; } pseudos.insert(nm); } static set<string> forb={"connection","keep-alive","proxy-connection","transfer-encoding","upgrade"}; if(forb.count(nm)){ findings.push_back({cap,f.stream,o,"forbidden-header",nm}); summary["forbidden-header"]++; } } ho<<"]}\n"; } } catch(exception& e){ dia<<"{\"capture\":\""<<esc(cap)<<"\",\"stream\":"<<f.stream<<",\"level\":\"error\",\"code\":\"decode-error\",\"message\":\""<<esc(e.what())<<"\"}\n"; pend[f.stream].active=false; } } }
 ofstream so(od+"/security_findings.json"); so<<"{\"summary\":{"; bool first=true; for(auto& kv:summary){ if(!first)so<<","; first=false; so<<"\""<<kv.first<<"\":"<<kv.second; } so<<"},\"findings\":["; for(size_t i=0;i<findings.size();++i){ auto& f=findings[i]; if(i)so<<","; so<<"{\"capture\":\""<<esc(f.capture)<<"\",\"stream\":"<<f.stream<<",\"ordinal\":"<<f.ordinal<<",\"type\":\""<<f.type<<"\",\"name\":\""<<esc(f.name)<<"\"}"; } so<<"]}\n"; return 0; }
