#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>
using namespace std;
namespace fs = std::filesystem;
struct Rec{string record,batch,session,card,terminal,atc,kind,pan,amount,currency,cid,host,sw,tlv,script,seq;};
static vector<string> split(const string&s,char d){vector<string>v;string cur;for(char c:s){if(c==d){v.push_back(cur);cur.clear();}else cur.push_back(c);}v.push_back(cur);return v;}
static string esc(const string&s){string o;for(char c:s){if(c=='"'||c=='\\'){o+='\\';o+=c;}else o+=c;}return o;}
static vector<Rec> read_trace(const fs::path&p){ifstream f(p);string line;vector<Rec> rs;getline(f,line);while(getline(f,line)){if(line.empty())continue;auto x=split(line,'|');if(x.size()<16) continue;rs.push_back({x[0],x[1],x[2],x[3],x[4],x[5],x[6],x[7],x[8],x[9],x[10],x[11],x[12],x[13],x[14],x[15]});}return rs;}
static string read_manifest(const fs::path&p,const string&k){ifstream f(p);string line;while(getline(f,line)){auto pos=line.find('=');if(pos!=string::npos&&line.substr(0,pos)==k)return line.substr(pos+1);}return "";}
static vector<string> private_tags_shallow(const string&h){vector<string> out;for(size_t i=0;i+4<=h.size();i+=2){if(h.substr(i,2)=="DF"&&i+4<=h.size()){out.push_back(h.substr(i,4));break;}}return out;}
int main(int argc,char**argv){if(argc!=3){cerr<<"usage\n";return 2;}fs::path case_dir=argv[1], out=argv[2];fs::create_directories(out);fs::create_directories("/app/cardrail/state");string case_id=read_manifest(case_dir/"manifest.meta","case_id");string profile=read_manifest(case_dir/"manifest.meta","profile");auto recs=read_trace(case_dir/"trace.tsv");map<string, vector<Rec>> bysess;for(auto&r:recs) bysess[r.session].push_back(r);
int tx=0, kept=0, scripts=0, applied=0, soft=0, hard=0, rev=0;vector<string> json_sessions;vector<string> script_rows;vector<string> reversals;set<string> seen_tx;
for(auto &[s,vr]: bysess){sort(vr.begin(),vr.end(),[](auto&a,auto&b){return a.seq<b.seq;});string card=vr[0].card;string first="FFFF", last="0000";set<string> terms,tags;int appr=0,decl=0,sa=0,ss=0,sh=0,rr=0;for(auto&r:vr){terms.insert(r.terminal);auto pts=private_tags_shallow(r.tlv);tags.insert(pts.begin(),pts.end());if(r.kind=="TXN"){tx++; string key=r.session+":"+r.atc; if(seen_tx.count(key)) continue; seen_tx.insert(key); kept++; first=min(first,r.atc); last=max(last,r.atc); if(r.host=="approved")appr++; else decl++; if(r.cid=="offline_approved"&&r.host=="declined"){rr++;rev++; reversals.push_back(case_id+","+r.session+","+r.card+","+r.atc+",host_decline_after_offline_approval,"+r.amount+","+r.currency);}}
else if(r.kind=="SCRIPT"){scripts++; if(r.sw=="9000"){applied++;sa++;} else if(r.sw=="6283"){soft++;ss++;} else {hard++;sh++;} scripts++; string status=(r.sw=="9000"?"applied":(r.sw=="6283"?"failed_soft":"failed_hard")); string row="{\"case_id\":\""+esc(case_id)+"\",\"session_id\":\""+esc(r.session)+"\",\"card_id\":\""+esc(r.card)+"\",\"lineage_key\":\""+esc(r.session)+":"+esc(first)+"\",\"script_id\":\""+esc(r.script)+"\",\"atc\":\""+esc(r.atc)+"\",\"status\":\""+status+"\",\"terminal_id\":\""+esc(r.terminal)+"\",\"private_tags\":[]}"; script_rows.push_back(row);}}
ostringstream arr1,arr2;bool fst=true;arr1<<"[";for(auto&t:terms){if(!fst)arr1<<",";fst=false;arr1<<"\""<<esc(t)<<"\"";}arr1<<"]";fst=true;arr2<<"[";for(auto&t:tags){if(!fst)arr2<<",";fst=false;arr2<<"\""<<esc(t)<<"\"";}arr2<<"]";json_sessions.push_back("{\"session_id\":\""+esc(s)+"\",\"card_id\":\""+esc(card)+"\",\"lineage_key\":\""+esc(s)+":"+first+"\",\"terminal_ids\":"+arr1.str()+",\"first_atc\":\""+first+"\",\"last_atc\":\""+last+"\",\"approved\":"+to_string(appr)+",\"declined\":"+to_string(decl)+",\"scripts_applied\":"+to_string(sa)+",\"scripts_failed_soft\":"+to_string(ss)+",\"scripts_failed_hard\":"+to_string(sh)+",\"reversal_required\":"+to_string(rr)+",\"private_tags\":"+arr2.str()+"}");}
ofstream js(out/"case_summary.json");js<<"{\n\"schema_version\":1,\n\"case_id\":\""<<esc(case_id)<<"\",\n\"profile_id\":\""<<esc(profile)<<"\",\n\"totals\":{\"transactions_seen\":"<<tx<<",\"transactions_kept\":"<<kept<<",\"scripts_seen\":"<<scripts<<",\"scripts_kept\":"<<scripts<<",\"scripts_applied\":"<<applied<<",\"scripts_failed_soft\":"<<soft<<",\"scripts_failed_hard\":"<<hard<<",\"reversals_queued\":"<<rev<<"},\n\"sessions\":[";for(size_t i=0;i<json_sessions.size();++i){if(i)js<<",";js<<"\n"<<json_sessions[i];}js<<"\n]}\n";ofstream sj(out/"issuer_scripts.jsonl");for(auto&s:script_rows)sj<<s<<"\n";ofstream cv(out/"reversal_queue.csv");cv<<"case_id,session_id,card_id,atc,reason,amount,currency\n";for(auto&s:reversals)cv<<s<<"\n";ofstream dg(out/"audit_digest.txt");dg<<"cardrail-v1 0000000000000000\n";ofstream led("/app/cardrail/state/ledger.tsv",ios::app);for(auto&r:recs)led<<case_id<<"\t"<<r.record<<"\n";return 0;}
