#!/bin/bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path
p = Path('/app/replaygate/src/replaygate.c')
s = p.read_text()
s = s.replace('if(strict&&i>0&&out->m[i-1].k->rn>out->m[i].k->rn)return 0;', 'if(strict&&i>0&&cmp_key(out->m[i-1].k,out->m[i].k)>=0)return 0;')
s = s.replace('for(size_t i=0;i<crit->an;i++){ if(crit->a[i].t!=T_UINT||!crit_known(crit->a[i].i)){free_val(&m);return 0;} for(size_t j=i+1;j<crit->an;j++) if(crit->a[j].t==T_UINT&&crit->a[j].i==crit->a[i].i){free_val(&m);return 0;} if(!map_int(&m,crit->a[i].i)){free_val(&m);return 0;} } memcpy(h->kid,kid->p,kid->n);', 'int c100=0,c101=0; for(size_t i=0;i<crit->an;i++){ if(crit->a[i].t!=T_UINT||!crit_known(crit->a[i].i)){free_val(&m);return 0;} if(crit->a[i].i==100)c100=1; if(crit->a[i].i==101)c101=1; for(size_t j=i+1;j<crit->an;j++) if(crit->a[j].t==T_UINT&&crit->a[j].i==crit->a[i].i){free_val(&m);return 0;} if(!map_int(&m,crit->a[i].i)){free_val(&m);return 0;} } if(!c100||!c101){free_val(&m);return 0;} memcpy(h->kid,kid->p,kid->n);')
s = s.replace('if(k->kidn==h->kidn&&!memcmp(k->kid,h->kid,h->kidn)&&h->epoch>=k->nb&&h->epoch<=k->na){ if(mac_ok(k,data,dn,sig,sn)) return 1; } } (void)op;', 'if(k->kidn==h->kidn&&!memcmp(k->kid,h->kid,h->kidn)&&h->epoch>=k->nb&&h->epoch<=k->na&&(!op||has_op(k,op))){ if(mac_ok(k,data,dn,sig,sn)) return 1; } }')
s = s.replace('static void maybe_tmp(const char*path){ char tmp[PATH_MAX]; snprintf(tmp,sizeof(tmp),"%s.tmp",path); unlink(tmp); }', 'static void maybe_tmp(const char*path){ char tmp[PATH_MAX]; snprintf(tmp,sizeof(tmp),"%s.tmp",path); Ledger base={0},cand={0}; int base_ok=1; if(access(path,F_OK)==0) base_ok=load_ledger(path,&base); if(access(tmp,F_OK)==0){ if(base_ok&&load_ledger(tmp,&cand)&&extends(&cand,&base)) rename(tmp,path); else unlink(tmp); } ledger_free(&base); ledger_free(&cand); }')
p.write_text(s)
PY

make -C /app/replaygate clean all
