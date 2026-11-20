#!/usr/bin/env bash
set -euo pipefail
cd /app/cardrail

# Inspect the tree before changing code. The contract files define the artifact
# formats and the existing source shows which binary the build script expects.
pwd
ls -la
ls -la reference src scripts cases profiles
grep -R "cardrail_replay\|lineage\|audit\|profile" -n reference src scripts || true

# Replace the broken implementation with a complete replay engine, then build
# and run a small smoke check against the visible case.
cat > src/main.cpp <<'CPP'
#include <algorithm>
#include <cctype>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <tuple>
#include <vector>
using namespace std;
namespace fs = std::filesystem;
struct Rec{string record,batch,session,card,terminal,atc,kind,pan,amount,currency,cid,host,sw,tlv,script,seq; bool continuation=false;};
struct Tx{Rec r; vector<string> tags; string eff_sw;};
struct Script{Rec r; vector<string> tags; string status;};
struct Profile{string id; bool allow_private=false; set<string> soft, hard;};
static vector<string> split(const string&s,char d){vector<string>v;string cur;for(char c:s){if(c==d){v.push_back(cur);cur.clear();}else cur.push_back(c);}v.push_back(cur);return v;}
static string esc(const string&s){string o;for(unsigned char c:s){if(c=='"'||c=='\\'){o+='\\';o+=char(c);}else if(c=='\n')o+="\\n";else o+=char(c);}return o;}
static int hexv(char c){if(c>='0'&&c<='9')return c-'0'; if(c>='A'&&c<='F')return c-'A'+10; if(c>='a'&&c<='f')return c-'a'+10; return 0;}
static int atcnum(const string&s){int v=0;for(char c:s)v=v*16+hexv(c);return v;}
static int seqnum(const string&s){try{return stoi(s);}catch(...){return 0;}}
static vector<unsigned char> bytes(const string&h){vector<unsigned char>b;for(size_t i=0;i+1<h.size();i+=2)b.push_back((hexv(h[i])<<4)|hexv(h[i+1]));return b;}
static void parse_tlv_rec(const vector<unsigned char>&b,size_t&pos,size_t end,set<string>&tags){while(pos<end){size_t start=pos;if(pos>=b.size())break;vector<unsigned char>tag;tag.push_back(b[pos++]);if((tag[0]&0x1f)==0x1f){while(pos<end&&pos<b.size()){unsigned char x=b[pos++];tag.push_back(x);if((x&0x80)==0)break;}}if(pos>=end||pos>=b.size())break;unsigned char lb=b[pos++];size_t len=0;if(lb&0x80){int n=lb&0x7f;for(int i=0;i<n&&pos<end&&pos<b.size();++i)len=(len<<8)|b[pos++];}else len=lb;size_t val_start=pos, val_end=min(end,pos+len);ostringstream ts;for(auto x:tag)ts<<uppercase<<hex<<setw(2)<<setfill('0')<<(int)x;string t=ts.str();if(t.rfind("DF",0)==0)tags.insert(t);if(!tag.empty()&&(tag[0]&0x20))parse_tlv_rec(b,pos,val_end,tags);pos=val_end;if(pos<=start)break;}}
static vector<string> private_tags(const string&h){auto b=bytes(h);size_t p=0;set<string>s;parse_tlv_rec(b,p,b.size(),s);return vector<string>(s.begin(),s.end());}
static vector<Rec> read_trace(const fs::path&p){ifstream f(p);string line;vector<Rec> rs;getline(f,line);while(getline(f,line)){if(line.empty())continue;auto x=split(line,'|');if(x.size()<16) continue;rs.push_back({x[0],x[1],x[2],x[3],x[4],x[5],x[6],x[7],x[8],x[9],x[10],x[11],x[12],x[13],x[14],x[15]});}return rs;}
static string read_kv(const fs::path&p,const string&k){ifstream f(p);string line;while(getline(f,line)){auto pos=line.find('=');if(pos!=string::npos&&line.substr(0,pos)==k)return line.substr(pos+1);pos=line.find('=');}return "";}
static string trim(string s){while(!s.empty()&&isspace((unsigned char)s.front()))s.erase(s.begin());while(!s.empty()&&isspace((unsigned char)s.back()))s.pop_back();if(s.size()>=2&&s.front()=='"'&&s.back()=='"')s=s.substr(1,s.size()-2);return s;}
static set<string> parse_array(string v){set<string> out;auto l=v.find('['), r=v.rfind(']');if(l==string::npos||r==string::npos||r<=l)return out;for(string item:split(v.substr(l+1,r-l-1),',')){item=trim(item);if(!item.empty())out.insert(item);}return out;}
static Profile read_profile(const string&hint){vector<fs::path> ps={"/etc/cardrail/profile.toml", fs::path("/app/cardrail/profiles")/(hint+".toml"), "/app/cardrail/profiles/default.toml"};for(auto&p:ps){ifstream f(p);if(!f)continue;Profile prof;string line;while(getline(f,line)){auto pos=line.find('=');if(pos==string::npos)continue;string k=trim(line.substr(0,pos)), v=trim(line.substr(pos+1));if(k=="profile_id")prof.id=v;else if(k=="allow_private_tags")prof.allow_private=(v=="true");else if(k=="soft_script_status")prof.soft=parse_array(v);else if(k=="hard_script_status")prof.hard=parse_array(v);}if(!prof.id.empty())return prof;}return {hint,false,{},{}};}
static bool is_cont(const string&sw){return sw.size()==4 && (sw.rfind("61",0)==0 || sw.rfind("6C",0)==0);} static bool is_success(const string&sw){return sw=="9000"||sw=="9100";}
static string script_status(const string&sw,const Profile&p){if(is_success(sw))return "applied"; if(p.soft.count(sw))return "failed_soft"; return "failed_hard";}
static uint64_t fnv(const string&s,uint64_t h=1469598103934665603ULL){for(unsigned char c:s){h^=c;h*=1099511628211ULL;}return h;}
static string join(const vector<string>&v,const string&d){string o;for(size_t i=0;i<v.size();++i){if(i)o+=d;o+=v[i];}return o;}
static string jarr(const vector<string>&v){string o="[";for(size_t i=0;i<v.size();++i){if(i)o+=",";o+="\""+esc(v[i])+"\"";}return o+"]";}
int main(int argc,char**argv){if(argc!=3){cerr<<"usage: cardrail_replay <case_dir> <output_dir>\n";return 2;}fs::path case_dir=argv[1], out=argv[2];fs::create_directories(out);fs::create_directories("/app/cardrail/state");string case_id=read_kv(case_dir/"manifest.meta","case_id");string hint=read_kv(case_dir/"manifest.meta","profile");Profile profile=read_profile(hint);auto recs=read_trace(case_dir/"trace.tsv");sort(recs.begin(),recs.end(),[](const Rec&a,const Rec&b){return make_tuple(a.card,a.session,a.atc,seqnum(a.seq),a.record)<make_tuple(b.card,b.session,b.atc,seqnum(b.seq),b.record);});
map<string,string> first_atc;for(auto&r:recs) if(!first_atc.count(r.card)&&r.kind=="TXN") first_atc[r.card]=r.atc;auto lineage=[&](const Rec&r){return r.card+":"+first_atc[r.card];};
vector<Tx> txs; vector<Script> scripts; set<string> seen_tx, seen_script, replayed_tx; int scripts_seen=0, tx_seen=0;
for(size_t i=0;i<recs.size();++i){Rec r=recs[i]; if(r.kind=="TXN"){tx_seen++; string key=lineage(r)+":"+r.atc; if(seen_tx.count(key)){replayed_tx.insert(key); continue;} seen_tx.insert(key); string eff=r.sw; if(is_cont(eff)){for(size_t j=i+1;j<recs.size();++j){if(recs[j].kind=="TXN"&&recs[j].card==r.card&&recs[j].session==r.session&&recs[j].atc==r.atc){eff=recs[j].sw; r.tlv += recs[j].tlv; break;}}} txs.push_back({r,private_tags(r.tlv),eff});}
else if(r.kind=="SCRIPT"){scripts_seen++; string key=lineage(r)+":"+r.atc+":"+r.script; if(seen_script.count(key)) continue; seen_script.insert(key); scripts.push_back({r,private_tags(r.tlv),script_status(r.sw,profile)});}}
sort(txs.begin(),txs.end(),[&](const Tx&a,const Tx&b){return make_tuple(a.r.card,first_atc[a.r.card],atcnum(a.r.atc),a.r.session,a.r.record)<make_tuple(b.r.card,first_atc[b.r.card],atcnum(b.r.atc),b.r.session,b.r.record);});
sort(scripts.begin(),scripts.end(),[&](const Script&a,const Script&b){return make_tuple(a.r.card,first_atc[a.r.card],atcnum(a.r.atc),a.r.script,a.r.record)<make_tuple(b.r.card,first_atc[b.r.card],atcnum(b.r.atc),b.r.script,b.r.record);});
struct Sess{string session,card,lineage,first,last;set<string>terms,tags;int approved=0,declined=0,sa=0,ss=0,sh=0,rr=0;};map<string,Sess> sm;for(auto&t:txs){auto &s=sm[t.r.session];s.session=t.r.session;s.card=t.r.card;s.lineage=lineage(t.r);s.terms.insert(t.r.terminal);s.first=s.first.empty()?t.r.atc:min(s.first,t.r.atc);s.last=max(s.last,t.r.atc);if(profile.allow_private)s.tags.insert(t.tags.begin(),t.tags.end());if(t.r.host=="approved")s.approved++;else s.declined++;}for(auto&sc:scripts){auto&s=sm[sc.r.session];s.session=sc.r.session;s.card=sc.r.card;s.lineage=lineage(sc.r);s.terms.insert(sc.r.terminal);if(profile.allow_private)s.tags.insert(sc.tags.begin(),sc.tags.end());if(sc.status=="applied")s.sa++;else if(sc.status=="failed_soft")s.ss++;else s.sh++;}
map<pair<string,string>,string> hard_script;for(auto&sc:scripts)if(sc.status=="failed_hard")hard_script[{lineage(sc.r),sc.r.atc}]="issuer_script_hard_failure";vector<vector<string>> revs;for(auto&t:txs){string reason="";auto it=hard_script.find({lineage(t.r),t.r.atc});if(it!=hard_script.end())reason=it->second;else if(t.r.cid=="offline_approved"&&t.r.host=="declined")reason="host_decline_after_offline_approval";else if(t.r.cid=="online_approved"&&t.r.host=="timeout")reason="host_timeout_after_online_approval";else if(replayed_tx.count(lineage(t.r)+":"+t.r.atc))reason="duplicate_atc_replay_attempt";if(!reason.empty()){revs.push_back({case_id,t.r.session,t.r.card,t.r.atc,reason,t.r.amount,t.r.currency});sm[t.r.session].rr++;}}
sort(revs.begin(),revs.end(),[](auto&a,auto&b){return tie(a[2],a[3],a[4],a[1])<tie(b[2],b[3],b[4],b[1]);});
int sa=0,ss=0,sh=0;for(auto&s:scripts){if(s.status=="applied")sa++;else if(s.status=="failed_soft")ss++;else sh++;}
ofstream js(out/"case_summary.json");js<<"{\n  \"schema_version\": 1,\n  \"case_id\": \""<<esc(case_id)<<"\",\n  \"profile_id\": \""<<esc(profile.id)<<"\",\n  \"totals\": {\"transactions_seen\": "<<tx_seen<<", \"transactions_kept\": "<<txs.size()<<", \"scripts_seen\": "<<scripts_seen<<", \"scripts_kept\": "<<scripts.size()<<", \"scripts_applied\": "<<sa<<", \"scripts_failed_soft\": "<<ss<<", \"scripts_failed_hard\": "<<sh<<", \"reversals_queued\": "<<revs.size()<<"},\n  \"sessions\": [";bool first=true;vector<Sess> sv;for(auto&[k,v]:sm)sv.push_back(v);sort(sv.begin(),sv.end(),[](auto&a,auto&b){return tie(a.card,a.lineage,a.session)<tie(b.card,b.lineage,b.session);});for(auto&s:sv){if(!first)js<<",";first=false;vector<string>terms(s.terms.begin(),s.terms.end()), tags(s.tags.begin(),s.tags.end());js<<"\n    {\"session_id\": \""<<esc(s.session)<<"\", \"card_id\": \""<<esc(s.card)<<"\", \"lineage_key\": \""<<esc(s.lineage)<<"\", \"terminal_ids\": "<<jarr(terms)<<", \"first_atc\": \""<<s.first<<"\", \"last_atc\": \""<<s.last<<"\", \"approved\": "<<s.approved<<", \"declined\": "<<s.declined<<", \"scripts_applied\": "<<s.sa<<", \"scripts_failed_soft\": "<<s.ss<<", \"scripts_failed_hard\": "<<s.sh<<", \"reversal_required\": "<<s.rr<<", \"private_tags\": "<<jarr(tags)<<"}";}js<<"\n  ]\n}\n";
ofstream sj(out/"issuer_scripts.jsonl");for(auto&sc:scripts){vector<string> outtags=profile.allow_private?sc.tags:vector<string>{};sj<<"{\"case_id\":\""<<esc(case_id)<<"\",\"session_id\":\""<<esc(sc.r.session)<<"\",\"card_id\":\""<<esc(sc.r.card)<<"\",\"lineage_key\":\""<<esc(lineage(sc.r))<<"\",\"script_id\":\""<<esc(sc.r.script)<<"\",\"atc\":\""<<sc.r.atc<<"\",\"status\":\""<<sc.status<<"\",\"terminal_id\":\""<<esc(sc.r.terminal)<<"\",\"private_tags\":"<<jarr(outtags)<<"}\n";}
ofstream cv(out/"reversal_queue.csv");cv<<"case_id,session_id,card_id,atc,reason,amount,currency\n";for(auto&r:revs)cv<<join(r,",")<<"\n";
string stream="summary|"+case_id+"|"+profile.id+"|"+to_string(txs.size())+"|"+to_string(scripts.size())+"|"+to_string(revs.size())+"\n";for(auto&s:sv){vector<string>tags(s.tags.begin(),s.tags.end());stream += "session|"+s.session+"|"+s.card+"|"+s.lineage+"|"+s.first+"|"+s.last+"|"+to_string(s.approved)+"|"+to_string(s.declined)+"|"+to_string(s.sa)+"|"+to_string(s.ss)+"|"+to_string(s.sh)+"|"+to_string(s.rr)+"|"+join(tags,",")+"\n";}for(auto&sc:scripts){vector<string> outtags=profile.allow_private?sc.tags:vector<string>{};stream += "script|"+sc.r.session+"|"+sc.r.card+"|"+lineage(sc.r)+"|"+sc.r.script+"|"+sc.r.atc+"|"+sc.status+"|"+join(outtags,",")+"\n";}for(auto&r:revs)stream += "reversal|"+r[1]+"|"+r[2]+"|"+r[3]+"|"+r[4]+"|"+r[5]+"|"+r[6]+"\n";uint64_t h=fnv(stream);ofstream dg(out/"audit_digest.txt");dg<<"cardrail-v1 "<<hex<<setw(16)<<setfill('0')<<nouppercase<<h<<"\n";
set<string> old;{ifstream lf("/app/cardrail/state/ledger.tsv");string line;while(getline(lf,line))old.insert(line);}ofstream led("/app/cardrail/state/ledger.tsv",ios::app);for(auto&t:txs){string k=case_id+"\t"+lineage(t.r)+"\tTXN\t"+t.r.atc+"\t"+t.r.record;if(!old.count(k)){led<<k<<"\n";old.insert(k);}}for(auto&sc:scripts){string k=case_id+"\t"+lineage(sc.r)+"\tSCRIPT\t"+sc.r.atc+"\t"+sc.r.script;if(!old.count(k)){led<<k<<"\n";old.insert(k);}}return 0;}

CPP
/app/cardrail/scripts/build.sh
rm -rf /app/cardrail/output/oracle-smoke /app/cardrail/state/ledger.tsv
/app/cardrail/bin/cardrail_replay /app/cardrail/cases/visible /app/cardrail/output/oracle-smoke
test -s /app/cardrail/output/oracle-smoke/case_summary.json
test -s /app/cardrail/output/oracle-smoke/audit_digest.txt
