#!/bin/bash
set -euo pipefail

cat > /opt/nftlab/src/nft_policy.py <<'PY'
#!/usr/bin/env python3
import ipaddress, re, sys
from pathlib import Path

CT_ORDER = ["new", "established", "related", "invalid"]
PROTO_ORDER = ["tcp", "udp", "icmp", "icmpv6"]
TCPFLAG_ORDER = ["fin", "syn", "rst", "psh", "ack", "urg"]
KEYS = {"ct", "iif", "oif", "proto", "tcpflags", "saddr", "daddr", "sport", "dport", "uid", "mark", "jump", "goto", "accept", "drop", "masquerade", "dnat", "snat"}
PRIORITIES = {"dstnat": -100, "mangle": -150, "filter": 0, "srcnat": 100}


def is_action(tok):
    k = tok.lower()
    return k in {'accept', 'drop', 'masquerade'} or k.startswith('dnat:') or k.startswith('snat:')


def toks(line):
    line = line.split('#', 1)[0].strip()
    return re.findall(r"[^\s{},=]+", line)


def substitute(tok, defines, seen=None):
    seen = seen or set()
    out = tok
    for name in sorted(defines, key=len, reverse=True):
        key = '$' + name
        if key in out.lower() and name not in seen:
            vals = [substitute(v, defines, seen | {name}) for v in defines[name]]
            repl = ' '.join(vals)
            out = re.sub(re.escape(key), repl, out, flags=re.IGNORECASE)
    return out


def expand_defines(tokens, defines):
    out = []
    for tok in tokens:
        repl = substitute(tok, defines)
        out.extend(repl.split())
    return out


def interval_token(x):
    x = x.lower().strip()
    if '/' in x:
        n = ipaddress.ip_network(x, strict=False)
        return (n.version, int(n.network_address), int(n.broadcast_address))
    if '-' in x:
        a, b = x.split('-', 1)
        aa, bb = ipaddress.ip_address(a), ipaddress.ip_address(b)
        return (aa.version, int(aa), int(bb))
    a = ipaddress.ip_address(x)
    return (a.version, int(a), int(a))


def canon_ip(ver, n):
    return str(ipaddress.ip_address(n)).lower()


def merge_intervals(items):
    items = sorted(items)
    out = []
    for ver, lo, hi in items:
        if out and out[-1][0] == ver and lo <= out[-1][2] + 1:
            out[-1] = (ver, out[-1][1], max(out[-1][2], hi))
        else:
            out.append((ver, lo, hi))
    parts = []
    for ver, lo, hi in out:
        s = canon_ip(ver, lo)
        if hi != lo:
            s += '-' + canon_ip(ver, hi)
        parts.append(s)
    return ','.join(parts) or '*'


def subtract_ranges(base, cuts):
    current = sorted(base)
    for cver, clo, chi in sorted(cuts):
        next_items = []
        for ver, lo, hi in current:
            if ver != cver or chi < lo or clo > hi:
                next_items.append((ver, lo, hi))
                continue
            if lo < clo:
                next_items.append((ver, lo, clo - 1))
            if chi < hi:
                next_items.append((ver, chi + 1, hi))
        current = next_items
    return current


def port_interval(x):
    x = x.lower().strip()
    base = 16 if x.startswith('0x') else 10
    if '-' in x:
        a, b = x.split('-', 1)
        return (int(a, 0), int(b, 0))
    return (int(x, base), int(x, base))


def merge_numeric(tokens):
    items = sorted(tokens)
    out = []
    for lo, hi in items:
        if out and lo <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return ','.join(str(lo) if lo == hi else f'{lo}-{hi}' for lo, hi in out) or '*'


def numeric_values(tokens):
    items = []
    subtracting = False
    for x in tokens:
        if x.lower() == 'except':
            subtracting = True
            continue
        rng = port_interval(x)
        if subtracting:
            items = [(lo, hi) for _, lo, hi in subtract_ranges([(4, lo, hi) for lo, hi in items], [(4, rng[0], rng[1])])]
            subtracting = False
        else:
            items.append(rng)
    return items


def expand_tokens(tokens, table, seen=None):
    seen = seen or set()
    out = []
    for x in tokens:
        if not x:
            continue
        if x.startswith('@'):
            name = x[1:].lower()
            if name in seen:
                continue
            out.extend(expand_tokens(table.get(name, []), table, seen | {name}))
        else:
            out.append(x)
    return out


def norm_numeric_ref(tokens, table):
    return merge_numeric(numeric_values(expand_tokens([x.lower() for x in tokens], table))) if tokens else '*'


def canon_order(tokens, order):
    vals = {x.lower() for x in tokens if x}
    return ','.join(x for x in order if x in vals) or '*'


def canon_if(tokens):
    return ','.join(sorted(set(tokens))) or '*'


def canon_zone(tokens, zones):
    return canon_if(expand_tokens(tokens, zones)) if tokens else '*'


def expand_addr(tokens, sets, seen=None):
    seen = seen or set()
    vals = []
    for x in tokens:
        if not x:
            continue
        if x.startswith('@'):
            name = x[1:].lower()
            if name in seen:
                continue
            vals.extend(expand_addr(sets.get(name, []), sets, seen | {name}))
        else:
            vals.append(interval_token(x))
    return vals


def addr_values(tokens, sets):
    vals = []
    subtracting = False
    for x in tokens:
        if x.lower() == 'except':
            subtracting = True
            continue
        if x.startswith('@'):
            expanded = addr_values(sets.get(x[1:].lower(), []), sets)
        else:
            expanded = expand_addr([x], sets)
        if subtracting:
            vals = subtract_ranges(vals, expanded)
            subtracting = False
        else:
            vals.extend(expanded)
    return vals


def norm_addr(tokens, sets):
    return merge_intervals(addr_values(tokens, sets)) if tokens else '*'


def parse_priority(tok):
    key = tok.lower()
    if key in PRIORITIES:
        return PRIORITIES[key]
    m = re.fullmatch(r'(dstnat|mangle|filter|srcnat)([+-]\d+)', key)
    if m:
        return PRIORITIES[m.group(1)] + int(m.group(2))
    return int(tok)


def read_policy(path):
    raw = []
    defines = {}
    for line in Path(path).read_text().splitlines():
        t = toks(line)
        if t:
            if t[0].lower() == 'define' and len(t) >= 3:
                defines[t[1].lower()] = expand_defines(t[2:], defines)
            else:
                raw.append(expand_defines(t, defines))
    chains = []
    set_raw = {}
    port_raw = {}
    zone_raw = {}
    rule_raw = []
    seq = 0
    for t in raw:
        seq += 1
        k = t[0].lower()
        if k == 'chain' and len(t) >= 10:
            chains.append(dict(family=t[1].lower(), table=t[2].lower(), chain=t[3].lower(), hook=t[5].lower(), priority=parse_priority(t[7]), policy=t[9].lower(), seq=seq))
        elif k == 'set' and len(t) >= 3:
            set_raw[t[1].lower()] = [x.lower() for x in t[2:]]
        elif k == 'ports' and len(t) >= 3:
            port_raw[t[1].lower()] = [x.lower() for x in t[2:]]
        elif k == 'zone' and len(t) >= 3:
            zone_raw[t[1].lower()] = t[2:]
        elif k == 'rule' and len(t) >= 5:
            rule_raw.append((seq, t))
    cmap = {(c['family'], c['table'], c['chain']): c for c in chains}
    rules = []
    for seq, t in rule_raw:
        r = dict(family=t[1].lower(), table=t[2].lower(), chain=t[3].lower(), ct='*', iif='*', oif='*', proto='*', tcpflags='*', saddr='*', daddr='*', sport='*', dport='*', uid='*', mark='*', action=t[-1].lower(), seq=seq)
        i = 4
        n = len(t)
        while i < n:
            k = t[i].lower()
            if is_action(k):
                r['action'] = k; break
            if k in {'jump', 'goto'} and i + 1 < n:
                r['action'] = f"{k}:{t[i+1].lower()}"; break
            j = i + 1
            while j < n and t[j].lower() not in KEYS and not is_action(t[j]):
                j += 1
            vals = t[i+1:j]
            if k == 'ct': r['ct'] = canon_order(vals, CT_ORDER)
            elif k == 'iif': r['iif'] = canon_zone(vals, zone_raw)
            elif k == 'oif': r['oif'] = canon_zone(vals, zone_raw)
            elif k == 'proto': r['proto'] = canon_order(vals, PROTO_ORDER)
            elif k == 'tcpflags': r['tcpflags'] = canon_order(vals, TCPFLAG_ORDER)
            elif k == 'saddr': r['saddr'] = norm_addr([v.lower() for v in vals], set_raw)
            elif k == 'daddr': r['daddr'] = norm_addr([v.lower() for v in vals], set_raw)
            elif k == 'sport': r['sport'] = norm_numeric_ref(vals, port_raw)
            elif k == 'dport': r['dport'] = norm_numeric_ref(vals, port_raw)
            elif k == 'uid': r['uid'] = merge_numeric(numeric_values(vals))
            elif k == 'mark': r['mark'] = merge_numeric(numeric_values(vals))
            i = j if j > i else i + 1
        c = cmap.get((r['family'], r['table'], r['chain']), dict(hook='none', priority=0, policy='accept'))
        r.update(hook=c['hook'], priority=c['priority'], policy=c['policy'])
        rules.append(r)
    return chains, rules


def chain_key(c):
    return (c['family'], c['table'], c['hook'], c['priority'], c['chain'])

def rule_key(r):
    return (r['family'], r['table'], r['hook'], r['priority'], r['chain'], r['seq'])


def compile_text(path):
    chains, rules = read_policy(path)
    lines = []
    for c in sorted(chains, key=chain_key):
        lines.append(f"chain family={c['family']} table={c['table']} chain={c['chain']} hook={c['hook']} priority={c['priority']} policy={c['policy']}")
    pos = {}
    for r in sorted(rules, key=rule_key):
        ck = (r['family'], r['table'], r['hook'], r['priority'], r['chain'])
        pos[ck] = pos.get(ck, 0) + 1
        parts = [f"rule family={r['family']} table={r['table']} chain={r['chain']} hook={r['hook']} priority={r['priority']} pos={pos[ck]}", f"ct={r['ct']}"]
        advanced = r['iif'] != '*' or r['oif'] != '*' or r['proto'] != '*'
        if advanced:
            parts += [f"iif={r['iif']}", f"oif={r['oif']}", f"proto={r['proto']}"]
        if r['tcpflags'] != '*': parts.append(f"tcpflags={r['tcpflags']}")
        parts.append(f"saddr={r['saddr']}")
        if r['daddr'] != '*': parts.append(f"daddr={r['daddr']}")
        if r['sport'] != '*': parts.append(f"sport={r['sport']}")
        parts.append(f"dport={r['dport']}")
        if r['uid'] != '*': parts.append(f"uid={r['uid']}")
        if r['mark'] != '*': parts.append(f"mark={r['mark']}")
        parts.append(f"action={r['action']}")
        lines.append(' '.join(parts))
    return '\n'.join(lines) + ('\n' if lines else '')


def main(argv):
    if len(argv) == 4 and argv[1] == 'compile':
        Path(argv[3]).write_text(compile_text(argv[2])); return 0
    if len(argv) == 5 and argv[1] == 'compare':
        Path(argv[4]).write_text('equivalent\n' if compile_text(argv[2]) == compile_text(argv[3]) else 'different\n'); return 0
    print('usage: nft-policy-tool compile INPUT OUTPUT | compare LEFT RIGHT OUTPUT')
    return 1

sys.exit(main(sys.argv))
PY
chmod 755 /opt/nftlab/src/nft_policy.py
cat > /opt/nftlab/Makefile <<'MK'
PREFIX ?= /usr/local
.PHONY: all install clean
all:
	@true
install: all
	@mkdir -p $(PREFIX)/sbin
	cp src/nft_policy.py $(PREFIX)/sbin/nft-policy-tool
	cp wrappers/nft-policy-compile $(PREFIX)/sbin/nft-policy-compile
	cp wrappers/nft-policy-compare $(PREFIX)/sbin/nft-policy-compare
	chmod 755 $(PREFIX)/sbin/nft-policy-tool $(PREFIX)/sbin/nft-policy-compile $(PREFIX)/sbin/nft-policy-compare
clean:
	rm -rf bin obj
MK
make -C /opt/nftlab clean install
