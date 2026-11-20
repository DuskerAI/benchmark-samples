#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace std;
namespace fs = std::filesystem;

struct Field { uint16_t id; uint16_t len; bool enterprise; uint32_t pen; };
struct TemplateDef { uint16_t id; vector<Field> fields; bool options=false; };
struct Flow { string exporter; uint32_t domain=0; uint16_t tid=0; string src,dst,app; uint64_t packets=0,octets=0,sp=0,so=0,start=0,end=0; uint16_t sport=0,dport=0; uint8_t proto=0,sampler=0; };
struct Anomaly { string type, exporter, file, detail; uint32_t domain=0, seq=0; uint16_t tid=0; };
struct Life { string exporter,file,event; uint32_t domain=0,seq=0; uint16_t tid=0,count=0; };

static uint16_t u16(const vector<uint8_t>& b,size_t& p){ if(p+2>b.size()) throw runtime_error("short u16"); uint16_t v=(b[p]<<8)|b[p+1]; p+=2; return v; }
static uint32_t u32(const vector<uint8_t>& b,size_t& p){ if(p+4>b.size()) throw runtime_error("short u32"); uint32_t v=(uint32_t(b[p])<<24)|(uint32_t(b[p+1])<<16)|(uint32_t(b[p+2])<<8)|b[p+3]; p+=4; return v; }
static uint64_t u64(const vector<uint8_t>& b,size_t& p){ uint64_t v=0; for(int i=0;i<8;i++){ if(p>=b.size()) throw runtime_error("short u64"); v=(v<<8)|b[p++]; } return v; }
static string ip4(const vector<uint8_t>& b,size_t& p){ if(p+4>b.size()) throw runtime_error("short ip"); string s=to_string(b[p])+"."+to_string(b[p+1])+"."+to_string(b[p+2])+"."+to_string(b[p+3]); p+=4; return s; }
static string esc(const string& s){ string o; for(char c:s){ if(c=='"'||c=='\\') o+='\\'; o+=c; } return o; }
static uint64_t read_counter_value(const vector<uint8_t>& b,size_t& p,uint16_t len){ if(len==1){ int8_t v=(int8_t)b[p++]; return (uint64_t)v; } if(len==2) return u16(b,p); if(len==4) return u32(b,p); if(len==8) return u64(b,p); p+=len; return 0; }
static string read_text_value(const vector<uint8_t>& b,size_t& p,uint16_t len){ string s; for(uint16_t i=0;i<len && p<b.size();++i) s.push_back((char)b[p++]); return s; }

int main(int argc,char**argv){
    string cap="/var/lib/flowguard/captures", reg="/var/lib/flowguard/registry/exporters.tsv", out="/var/lib/flowguard/output";
    for(int i=1;i<argc;i++){ string a=argv[i]; if(a=="--capture-root"&&i+1<argc) cap=argv[++i]; else if(a=="--registry"&&i+1<argc) reg=argv[++i]; else if(a=="--output"&&i+1<argc) out=argv[++i]; else if(a=="--config"&&i+1<argc) ++i; }
    fs::create_directories(out);
    map<string,string> exporters; { ifstream f(reg); string line; getline(f,line); while(getline(f,line)){ stringstream ss(line); string a,b; getline(ss,a,'\t'); getline(ss,b,'\t'); exporters[a]=b; } }
    vector<fs::path> files; for(auto& e: fs::recursive_directory_iterator(cap)) if(e.is_regular_file() && e.path().extension()==".ipfix") files.push_back(e.path()); sort(files.begin(),files.end());
    map<uint16_t,TemplateDef> templates;
    map<string,uint32_t> last_seq;
    vector<Flow> flows; vector<Anomaly> anomalies; vector<Life> life; uint32_t messages=0;
    for(auto& path: files){
        string rel=fs::relative(path,cap).generic_string(); string first=rel.substr(0, rel.find('/')); string exporter=exporters.count(first)?exporters[first]:first;
        ifstream in(path, ios::binary); vector<uint8_t> b((istreambuf_iterator<char>(in)),{}); size_t p=0;
        while(p+16<=b.size()){
            size_t msg_start=p; uint16_t ver=u16(b,p), mlen=u16(b,p); (void)ver; uint32_t export_time=u32(b,p); (void)export_time; uint32_t seq=u32(b,p); uint32_t domain=u32(b,p); messages++;
            if(last_seq.count(exporter) && last_seq[exporter]+1!=seq){ anomalies.push_back({"sequence_gap",exporter,rel,"unexpected sequence",domain,seq,0}); }
            last_seq[exporter]=seq;
            size_t end=msg_start+mlen;
            while(p+4<=end){ uint16_t setid=u16(b,p), slen=u16(b,p); size_t send=p+slen-4;
                if(setid==2){ while(p+4<=send){ uint16_t tid=u16(b,p), fc=u16(b,p); if(fc==0){ templates.erase(tid); life.push_back({exporter,rel,"withdraw",domain,seq,tid,0}); continue; } TemplateDef td; td.id=tid; for(int i=0;i<fc;i++){ uint16_t raw=u16(b,p); uint16_t len=u16(b,p); bool ent=raw & 0x8000; uint32_t pen=ent ? u32(b,p) : 0; td.fields.push_back({uint16_t(raw & 0x7fff),len, ent,pen}); } life.push_back({exporter,rel,templates.count(tid)?"redefine":"define",domain,seq,tid,fc}); templates[tid]=td; } }
                else if(setid==3){ while(p+4<=send){ uint16_t tid=u16(b,p), sc=u16(b,p), fc=u16(b,p); TemplateDef td; td.id=tid; td.options=true; for(int i=0;i<sc+fc;i++){ uint16_t raw=u16(b,p); uint16_t len=u16(b,p); bool ent=raw & 0x8000; uint32_t pen=ent ? u32(b,p) : 0; td.fields.push_back({uint16_t(raw & 0x7fff),len,ent,pen}); } templates[tid]=td; life.push_back({exporter,rel,templates.count(tid)?"redefine":"define",domain,seq,tid,(uint16_t)(sc+fc)}); } }
                else if(setid>=256){ auto it=templates.find(setid); if(it==templates.end()){ anomalies.push_back({"stale_template_data",exporter,rel,"unknown template",domain,seq,setid}); p=send; continue; } auto td=it->second; while(p<send){ Flow fl; fl.exporter=exporter; fl.domain=domain; fl.tid=setid; bool ok=true; for(auto& f: td.fields){ try{ switch(f.id){ case 8: fl.src=ip4(b,p); break; case 12: fl.dst=ip4(b,p); break; case 4: fl.proto=b[p++]; break; case 7: fl.sport=u16(b,p); break; case 11: fl.dport=u16(b,p); break; case 2: fl.packets=read_counter_value(b,p,f.len); break; case 1: fl.octets=read_counter_value(b,p,f.len); break; case 152: fl.start=u64(b,p); break; case 153: fl.end=u64(b,p); break; case 48: fl.sampler=(uint8_t)read_counter_value(b,p,f.len); break; case 40001: fl.app=read_text_value(b,p,f.len); break; default: p+=f.len; break; }}catch(...){ ok=false; p=send; break; }} if(ok){ fl.sp=fl.packets; fl.so=fl.octets; flows.push_back(fl); } } }
                p=send;
            }
            p=end;
        }
    }
    sort(flows.begin(),flows.end(),[](const Flow&a,const Flow&b){ return tie(a.exporter,a.domain,a.start,a.src,a.dst,a.proto,a.sport,a.dport,a.tid)<tie(b.exporter,b.domain,b.start,b.src,b.dst,b.proto,b.sport,b.dport,b.tid); });
    ofstream fr(out+"/flow_records.jsonl"); for(auto& f:flows) fr<<"{\"exporter_id\":\""<<esc(f.exporter)<<"\",\"observation_domain_id\":"<<f.domain<<",\"template_id\":"<<f.tid<<",\"source_ip\":\""<<f.src<<"\",\"destination_ip\":\""<<f.dst<<"\",\"protocol\":"<<(int)f.proto<<",\"source_port\":"<<f.sport<<",\"destination_port\":"<<f.dport<<",\"packets\":"<<f.packets<<",\"octets\":"<<f.octets<<",\"sampled_packets\":"<<f.sp<<",\"sampled_octets\":"<<f.so<<",\"sampler_id\":"<<(int)f.sampler<<",\"flow_start\":"<<f.start<<",\"flow_end\":"<<f.end<<",\"application_name\":\""<<esc(f.app)<<"\"}\n";
    ofstream lc(out+"/template_lifecycle.csv"); lc<<"exporter_id,observation_domain_id,template_id,event,field_count,message_sequence,capture_file\n"; for(auto& l:life) lc<<l.exporter<<","<<l.domain<<","<<l.tid<<","<<l.event<<","<<l.count<<","<<l.seq<<","<<l.file<<"\n";
    ofstream an(out+"/anomaly_trace.jsonl"); for(auto& a:anomalies) an<<"{\"type\":\""<<a.type<<"\",\"exporter_id\":\""<<a.exporter<<"\",\"observation_domain_id\":"<<a.domain<<",\"sequence\":"<<a.seq<<",\"template_id\":"<<a.tid<<",\"capture_file\":\""<<a.file<<"\",\"detail\":\""<<a.detail<<"\"}\n";
    uint64_t sp=0,so=0; for(auto& f:flows){ sp+=f.sp; so+=f.so; }
    ofstream cs(out+"/collector_summary.json"); cs<<"{\"schema_version\":1,\"capture_files\":"<<files.size()<<",\"messages\":"<<messages<<",\"accepted_flows\":"<<flows.size()<<",\"rejected_flows\":0,\"sequence_gaps\":"<<anomalies.size()<<",\"sampled_packets\":"<<sp<<",\"sampled_octets\":"<<so<<",\"exporters\":[] }\n";
    ofstream mf(out+"/audit_manifest.tsv"); mf<<"artifact\tsha256\trows\ncollector_summary.json\tbroken\t1\nflow_records.jsonl\tbroken\t"<<flows.size()<<"\ntemplate_lifecycle.csv\tbroken\t"<<life.size()<<"\nanomaly_trace.jsonl\tbroken\t"<<anomalies.size()<<"\n";
    return 0;
}
