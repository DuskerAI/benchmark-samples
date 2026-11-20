import subprocess
from pathlib import Path


def rebuild_tool() -> None:
    subprocess.run(["make", "-C", "/opt/nftlab", "clean", "install"], check=True)


def run_compile(source: str, tmp_path: Path) -> str:
    src = tmp_path / "policy.nfto"
    out = tmp_path / "compiled.txt"
    src.write_text(source)
    subprocess.run(["/usr/local/sbin/nft-policy-compile", str(src), str(out)], check=True)
    return out.read_text()


def run_compare(left: str, right: str, tmp_path: Path) -> str:
    lpath = tmp_path / "left.nfto"
    rpath = tmp_path / "right.nfto"
    out = tmp_path / "answer.txt"
    lpath.write_text(left)
    rpath.write_text(right)
    subprocess.run(["/usr/local/sbin/nft-policy-compare", str(lpath), str(rpath), str(out)], check=True)
    return out.read_text().strip()


def test_compile_contract(tmp_path: Path) -> None:
    """The compiler emits deterministic canonical firewall policy text."""
    rebuild_tool()
    source = """
chain ip nat prerouting hook prerouting priority -100 policy accept
chain inet filter input hook input priority 0 policy drop
set ops = { 2001:0db8:0:0:0:0:0:0001, 2001:db8::2-2001:0db8::0003 }
set v4 = { 192.0.2.4-192.0.2.6, 192.0.2.1, 192.0.2.2-192.0.2.3 }
rule ip nat prerouting ct new saddr @v4 dport 80 dnat:10.10.0.5:8080
rule inet filter input ct related,new,established saddr @ops dport { 443, 22-23, 24 } accept
""".strip()
    expected = """chain family=inet table=filter chain=input hook=input priority=0 policy=drop
chain family=ip table=nat chain=prerouting hook=prerouting priority=-100 policy=accept
rule family=inet table=filter chain=input hook=input priority=0 pos=1 ct=new,established,related saddr=2001:db8::1-2001:db8::3 dport=22-24,443 action=accept
rule family=ip table=nat chain=prerouting hook=prerouting priority=-100 pos=1 ct=new saddr=192.0.2.1-192.0.2.6 dport=80 action=dnat:10.10.0.5:8080
"""
    assert run_compile(source, tmp_path) == expected


def test_compile_multiple_positions_in_one_chain(tmp_path: Path) -> None:
    """Rules in the same chain keep first-match order with increasing positions."""
    rebuild_tool()
    source = """
chain inet filter input hook input priority 0 policy drop
chain inet filter forward hook forward priority 0 policy drop
rule inet filter input ct new saddr 10.0.0.1 dport 80 accept
rule inet filter input ct established saddr 10.0.0.2 dport 81 drop
rule inet filter input saddr @LATE dport { 443, 444 } accept
rule inet filter forward dport 53 drop
set late = { 10.0.0.3, 10.0.0.4 }
""".strip()
    expected = """chain family=inet table=filter chain=forward hook=forward priority=0 policy=drop
chain family=inet table=filter chain=input hook=input priority=0 policy=drop
rule family=inet table=filter chain=forward hook=forward priority=0 pos=1 ct=* saddr=* dport=53 action=drop
rule family=inet table=filter chain=input hook=input priority=0 pos=1 ct=new saddr=10.0.0.1 dport=80 action=accept
rule family=inet table=filter chain=input hook=input priority=0 pos=2 ct=established saddr=10.0.0.2 dport=81 action=drop
rule family=inet table=filter chain=input hook=input priority=0 pos=3 ct=* saddr=10.0.0.3-10.0.0.4 dport=443-444 action=accept
"""
    assert run_compile(source, tmp_path) == expected


def test_compare_equivalent_forms(tmp_path: Path) -> None:
    """Equivalent firewall inputs compare equal after canonical compilation."""
    rebuild_tool()
    left = """
chain ip nat prerouting hook prerouting priority -100 policy accept
chain inet filter input hook input priority 0 policy drop
set ops = { 2001:0db8:0:0:0:0:0:0001, 2001:db8::2-2001:0db8::0003 }
set v4 = { 192.0.2.4-192.0.2.6, 192.0.2.1, 192.0.2.2-192.0.2.3 }
rule ip nat prerouting ct new saddr @v4 dport 80 dnat:10.10.0.5:8080
rule inet filter input ct related,new,established saddr @ops dport { 443, 22-23, 24 } accept
""".strip()
    right = """
# reviewed during the maintenance window
chain inet filter input hook input priority 0 policy drop

set v4 = { 192.0.2.1-192.0.2.6 }
chain ip nat prerouting hook prerouting priority -100 policy accept
set ops = { 2001:db8::3, 2001:0db8::0001-2001:db8::2 }
rule inet filter input ct new,established,related saddr @ops dport { 22-24, 443 } accept
rule ip nat prerouting ct new saddr @v4 dport 80 dnat:10.10.0.5:8080
""".strip()
    assert run_compare(left, right, tmp_path) == "equivalent"


def test_compile_disjoint_interval_sets(tmp_path: Path) -> None:
    """Disjoint address and port spans coalesce without collapsing gaps."""
    rebuild_tool()
    source = """
chain ip filter input hook input priority 0 policy drop
set audit = { 198.51.100.10, 198.51.100.12-198.51.100.14, 198.51.100.11, 198.51.100.20, 198.51.100.22-198.51.100.23, 198.51.100.21 }
rule ip filter input saddr @audit dport { 100, 101-103, 105, 104, 110-111, 112 } accept
""".strip()
    expected = """chain family=ip table=filter chain=input hook=input priority=0 policy=drop
rule family=ip table=filter chain=input hook=input priority=0 pos=1 ct=* saddr=198.51.100.10-198.51.100.14,198.51.100.20-198.51.100.23 dport=100-105,110-112 action=accept
"""
    assert run_compile(source, tmp_path) == expected


def test_compile_cidr_prefixes_coalesce_with_ranges(tmp_path: Path) -> None:
    """CIDR prefixes join adjacent address spans before canonical output."""
    rebuild_tool()
    source = """
chain ip filter input hook input priority 0 policy drop
chain ip6 filter edge hook input priority -5 policy drop
set Corp = { 198.51.100.4/30, 198.51.100.8-198.51.100.10, 198.51.100.11 }
set v6audit = { 2001:0db8:0:1::8/126, 2001:db8:0:1::c-2001:0db8:0:1::000f }
rule ip filter input saddr @corp dport { 53, 54-55 } accept
rule ip6 filter edge saddr @V6AUDIT dport 443 drop
""".strip()
    expected = """chain family=ip table=filter chain=input hook=input priority=0 policy=drop
chain family=ip6 table=filter chain=edge hook=input priority=-5 policy=drop
rule family=ip table=filter chain=input hook=input priority=0 pos=1 ct=* saddr=198.51.100.4-198.51.100.11 dport=53-55 action=accept
rule family=ip6 table=filter chain=edge hook=input priority=-5 pos=1 ct=* saddr=2001:db8:0:1::8-2001:db8:0:1::f dport=443 action=drop
"""
    assert run_compile(source, tmp_path) == expected


def test_compare_cidr_prefix_equivalence(tmp_path: Path) -> None:
    """Compare treats CIDR input and explicit inclusive ranges identically."""
    rebuild_tool()
    prefix_form = """
chain ip filter forward hook forward priority 0 policy drop
chain ip6 filter forward6 hook forward priority 0 policy drop
rule ip filter forward saddr { 203.0.113.64/29, 203.0.113.72/30, 203.0.113.76-203.0.113.79 } dport 8443 accept
rule ip6 filter forward6 saddr { 2001:db8:5::20/125, 2001:db8:5::28-2001:db8:5::2f } dport 9443 drop
""".strip()
    range_form = """
chain ip6 filter forward6 hook forward priority 0 policy drop
rule ip6 filter forward6 saddr 2001:db8:5::20-2001:db8:5::2f dport 9443 drop
chain ip filter forward hook forward priority 0 policy drop
rule ip filter forward saddr 203.0.113.64-203.0.113.79 dport 8443 accept
""".strip()
    assert run_compare(prefix_form, range_form, tmp_path) == "equivalent"


def test_compile_nested_sets_and_late_chain_metadata(tmp_path: Path) -> None:
    """Nested set references and late chain declarations still compile canonically."""
    rebuild_tool()
    source = """
set east = { 198.51.100.1, 198.51.100.2-198.51.100.3 }
set border = { @WEST, @east, 198.51.100.8/31 }
rule ip filter audit ct established,new saddr @BORDER dport { 444, 443, 445-446 } accept
chain ip filter audit hook input priority -20 policy drop
set west = { 198.51.100.4-198.51.100.7 }
""".strip()
    expected = """chain family=ip table=filter chain=audit hook=input priority=-20 policy=drop
rule family=ip table=filter chain=audit hook=input priority=-20 pos=1 ct=new,established saddr=198.51.100.1-198.51.100.9 dport=443-446 action=accept
"""
    assert run_compile(source, tmp_path) == expected


def test_compare_nested_sets_late_chains_and_flat_ranges(tmp_path: Path) -> None:
    """Compare resolves nested sets and delayed chain metadata before equivalence."""
    rebuild_tool()
    nested = """
set north = { 2001:db8:10::1-2001:db8:10::3 }
set south = { 2001:0db8:0010::0004/126 }
set combined = { @south, @NORTH }
rule ip6 filter review ct invalid,related saddr @combined dport { 65000, 65001-65002 } drop
chain ip6 filter review hook forward priority 5 policy drop
""".strip()
    flat = """
chain ip6 filter review hook forward priority 5 policy drop
rule ip6 filter review ct related,invalid saddr 2001:db8:10::1-2001:db8:10::7 dport 65000-65002 drop
""".strip()
    assert run_compare(nested, flat, tmp_path) == "equivalent"


def test_forward_set_references_are_canonical(tmp_path: Path) -> None:
    """Set references resolve independently of declaration order and case."""
    rebuild_tool()
    source = """
chain ip6 filter edge hook input priority -5 policy drop
rule ip6 filter edge ct INVALID,new saddr @EDGE dport { 1003, 1000-1002, 1002 } accept
set Edge = { 2001:0db8:0:0:0:0:0:0005-2001:db8::6, 2001:db8::4, 2001:db8::7 }
""".strip()
    expected = """chain family=ip6 table=filter chain=edge hook=input priority=-5 policy=drop
rule family=ip6 table=filter chain=edge hook=input priority=-5 pos=1 ct=new,invalid saddr=2001:db8::4-2001:db8::7 dport=1000-1003 action=accept
"""
    assert run_compile(source, tmp_path) == expected


def test_compare_forward_and_early_sets(tmp_path: Path) -> None:
    """Compare treats late and early set declarations as the same policy."""
    rebuild_tool()
    late = """
chain ip6 filter edge hook input priority -5 policy drop
rule ip6 filter edge ct invalid,new saddr @EDGE dport { 1003, 1000-1002 } accept
set Edge = { 2001:db8::4-2001:db8::7 }
""".strip()
    early = """
set edge = { 2001:0db8::0004, 2001:db8::5-2001:0db8::0007 }
chain ip6 filter edge hook input priority -5 policy drop
rule ip6 filter edge ct new,invalid saddr @edge dport 1000-1003 accept
""".strip()
    assert run_compare(late, early, tmp_path) == "equivalent"


def test_compare_ordered_actions(tmp_path: Path) -> None:
    """The equivalence command preserves first-match firewall action order."""
    rebuild_tool()
    first = """
chain ip nat prerouting hook prerouting priority -100 policy accept
rule ip nat prerouting saddr 192.0.2.1 dport 80 dnat:10.0.0.1:80
rule ip nat prerouting saddr 192.0.2.1 dport 80 dnat:10.0.0.2:80
""".strip()
    second = """
chain ip nat prerouting hook prerouting priority -100 policy accept
rule ip nat prerouting saddr 192.0.2.1 dport 80 dnat:10.0.0.2:80
rule ip nat prerouting saddr 192.0.2.1 dport 80 dnat:10.0.0.1:80
""".strip()
    assert run_compare(first, second, tmp_path) == "different"


def test_compare_ignores_source_spacing(tmp_path: Path) -> None:
    """Source layout does not affect equivalent compiled policy positions."""
    rebuild_tool()
    compact = """
chain inet filter forward hook forward priority 0 policy drop
rule inet filter forward ct established saddr 10.0.0.1 dport 443 accept
rule inet filter forward ct new saddr 10.0.0.2 dport 8443 drop
""".strip()
    spaced = """
# ticket fw-734

chain inet filter forward hook forward priority 0 policy drop

rule inet filter forward ct established saddr 10.0.0.1 dport 443 accept

rule inet filter forward ct new saddr 10.0.0.2 dport 8443 drop
""".strip()
    assert run_compare(compact, spaced, tmp_path) == "equivalent"


def test_compile_state_and_sort_tiebreakers(tmp_path: Path) -> None:
    """Compile output covers chain tiebreakers, port ranges, and all ct states."""
    rebuild_tool()
    source = """
chain inet filter zeta hook input priority 10 policy drop
chain inet filter alpha hook forward priority 0 policy accept
chain inet filter aardvark hook input priority 0 policy drop
chain inet filter gamma hook input priority -10 policy drop
chain inet nat prerouting hook prerouting priority -100 policy accept
chain inet filter beta hook input priority 0 policy drop
rule inet filter zeta ct invalid,related,new,established dport { 80, 81, 443, 82-84 } accept
rule inet filter aardvark dport 25 accept
rule inet filter gamma dport 110 accept
rule inet filter beta dport { 1000-1002, 1003, 1005 } drop
rule inet filter alpha ct invalid dport 53 accept
rule inet nat prerouting dport 8080 dnat:10.0.0.7:80
""".strip()
    expected = """chain family=inet table=filter chain=alpha hook=forward priority=0 policy=accept
chain family=inet table=filter chain=gamma hook=input priority=-10 policy=drop
chain family=inet table=filter chain=aardvark hook=input priority=0 policy=drop
chain family=inet table=filter chain=beta hook=input priority=0 policy=drop
chain family=inet table=filter chain=zeta hook=input priority=10 policy=drop
chain family=inet table=nat chain=prerouting hook=prerouting priority=-100 policy=accept
rule family=inet table=filter chain=alpha hook=forward priority=0 pos=1 ct=invalid saddr=* dport=53 action=accept
rule family=inet table=filter chain=gamma hook=input priority=-10 pos=1 ct=* saddr=* dport=110 action=accept
rule family=inet table=filter chain=aardvark hook=input priority=0 pos=1 ct=* saddr=* dport=25 action=accept
rule family=inet table=filter chain=beta hook=input priority=0 pos=1 ct=* saddr=* dport=1000-1003,1005 action=drop
rule family=inet table=filter chain=zeta hook=input priority=10 pos=1 ct=new,established,related,invalid saddr=* dport=80-84,443 action=accept
rule family=inet table=nat chain=prerouting hook=prerouting priority=-100 pos=1 ct=* saddr=* dport=8080 action=dnat:10.0.0.7:80
"""
    assert run_compile(source, tmp_path) == expected


def test_compile_ignores_unrecognized_lines(tmp_path: Path) -> None:
    """Unrecognized source lines do not appear in canonical output."""
    rebuild_tool()
    source = """
garbage line from a copied shell transcript
chain ip filter input hook input priority 0 policy drop
ticket fw-9001 says this is not policy syntax
rule ip filter input ct new saddr 203.0.113.10 dport 443 accept
random trailing note
""".strip()
    expected = """chain family=ip table=filter chain=input hook=input priority=0 policy=drop
rule family=ip table=filter chain=input hook=input priority=0 pos=1 ct=new saddr=203.0.113.10 dport=443 action=accept
"""
    assert run_compile(source, tmp_path) == expected


def test_compile_transitive_sets_and_metadata_order(tmp_path: Path) -> None:
    """Transitive set references and late chain metadata are resolved together."""
    rebuild_tool()
    source = """
rule inet filter review ct related,new saddr @OUTER dport { 9000-9001, 8999 } accept
set inner = { 2001:0db8:20::4/126, 2001:db8:20::8-2001:db8:20::9 }
chain inet filter review hook forward priority -15 policy drop
set middle = { @INNER, 2001:db8:20::1-2001:0db8:0020::0003 }
set outer = { @middle, 2001:db8:20::a }
""".strip()
    expected = """chain family=inet table=filter chain=review hook=forward priority=-15 policy=drop
rule family=inet table=filter chain=review hook=forward priority=-15 pos=1 ct=new,related saddr=2001:db8:20::1-2001:db8:20::a dport=8999-9001 action=accept
"""
    assert run_compile(source, tmp_path) == expected


def test_compare_complete_chain_metadata_and_policy(tmp_path: Path) -> None:
    """Equivalence compares the complete canonical record, including chain policy."""
    rebuild_tool()
    drop_policy = """
chain inet filter input hook input priority 0 policy drop
rule inet filter input ct new saddr 10.10.0.1 dport 443 accept
""".strip()
    accept_policy = """
chain inet filter input hook input priority 0 policy accept
rule inet filter input ct new saddr 10.10.0.1 dport 443 accept
""".strip()
    assert run_compare(drop_policy, accept_policy, tmp_path) == "different"


def test_compare_deeply_normalized_equivalent_policies(tmp_path: Path) -> None:
    """Compare uses transitive sets, CIDR expansion, and canonical state ordering."""
    rebuild_tool()
    left = """
set west = { 198.51.100.32/29 }
set east = { 198.51.100.40-198.51.100.45, 198.51.100.46, 198.51.100.47 }
set campus = { @WEST, @east }
chain ip filter forward hook forward priority 3 policy drop
rule ip filter forward ct invalid,established,new saddr @campus dport { 6000, 6001-6003 } drop
""".strip()
    right = """
chain ip filter forward hook forward priority 3 policy drop
rule ip filter forward ct new,established,invalid saddr 198.51.100.32-198.51.100.47 dport 6000-6003 drop
""".strip()
    assert run_compare(left, right, tmp_path) == "equivalent"


def test_compile_destination_addresses_and_verdict_transfers(tmp_path: Path) -> None:
    """Destination filters and two-word verdict transfers are canonicalized together."""
    rebuild_tool()
    source = """
set exits = { 2001:0db8:30::8/126, 2001:db8:30::c-2001:0db8:30::000d }
rule inet filter dispatch ct established,new daddr @EXITS saddr { 198.51.100.64/30, 198.51.100.68-198.51.100.70 } dport { 443, 444-445 } jump Review
chain inet filter review hook forward priority 0 policy drop
chain inet filter dispatch hook forward priority -5 policy drop
""".strip()
    expected = """chain family=inet table=filter chain=dispatch hook=forward priority=-5 policy=drop
chain family=inet table=filter chain=review hook=forward priority=0 policy=drop
rule family=inet table=filter chain=dispatch hook=forward priority=-5 pos=1 ct=new,established saddr=198.51.100.64-198.51.100.70 daddr=2001:db8:30::8-2001:db8:30::d dport=443-445 action=jump:review
"""
    assert run_compile(source, tmp_path) == expected


def test_compare_destination_address_forms_and_goto_actions(tmp_path: Path) -> None:
    """Compare includes destination addresses and verdict-transfer targets."""
    left = """
chain ip filter dispatch hook input priority 0 policy drop
set dst = { 203.0.113.32/29, 203.0.113.40-203.0.113.47 }
rule ip filter dispatch daddr @dst dport { 8080, 8081-8082 } goto Inspect
""".strip()
    right = """
rule ip filter dispatch dport 8080-8082 daddr 203.0.113.32-203.0.113.47 goto inspect
chain ip filter dispatch hook input priority 0 policy drop
""".strip()
    wrong_target = """
chain ip filter dispatch hook input priority 0 policy drop
rule ip filter dispatch daddr 203.0.113.32-203.0.113.47 dport 8080-8082 goto other
""".strip()
    rebuild_tool()
    assert run_compare(left, right, tmp_path) == "equivalent"
    assert run_compare(left, wrong_target, tmp_path) == "different"


def test_compile_source_ports_with_destination_sets(tmp_path: Path) -> None:
    """Source-port spans are canonicalized with destination set expansion."""
    rebuild_tool()
    source = """
set remote = { 2001:0db8:40::10/126, 2001:db8:40::14-2001:0db8:40::0017 }
rule inet filter egress ct new daddr @REMOTE sport { 1024-1026, 1027, 2048 } dport { 443, 444-445 } accept
chain inet filter egress hook output priority 1 policy drop
""".strip()
    expected = """chain family=inet table=filter chain=egress hook=output priority=1 policy=drop
rule family=inet table=filter chain=egress hook=output priority=1 pos=1 ct=new saddr=* daddr=2001:db8:40::10-2001:db8:40::17 sport=1024-1027,2048 dport=443-445 action=accept
"""
    assert run_compile(source, tmp_path) == expected


def test_compare_source_ports_are_behavioral(tmp_path: Path) -> None:
    """Equivalence includes canonical source-port spans, not only destinations."""
    left = """
chain ip filter egress hook output priority 0 policy drop
rule ip filter egress saddr 198.51.100.10 sport { 4000-4002, 4003 } dport 8443 accept
""".strip()
    same = """
rule ip filter egress dport 8443 sport 4000-4003 saddr 198.51.100.10 accept
chain ip filter egress hook output priority 0 policy drop
""".strip()
    different = """
chain ip filter egress hook output priority 0 policy drop
rule ip filter egress saddr 198.51.100.10 sport 4000-4002 dport 8443 accept
""".strip()
    rebuild_tool()
    assert run_compare(left, same, tmp_path) == "equivalent"
    assert run_compare(left, different, tmp_path) == "different"


def test_compile_interfaces_protocols_and_marks(tmp_path: Path) -> None:
    """Interfaces, protocols, marks, addresses, and ports canonicalize together."""
    rebuild_tool()
    source = """
chain inet mangle preroute hook prerouting priority -150 policy accept
set staff = { 10.10.0.4/31, 10.10.0.6, 10.10.0.8-10.10.0.9 }
rule inet mangle preroute ct established,new iif { wan2, wan1, wan1 } oif dmz proto { udp, tcp, tcp } saddr @staff daddr { 2001:db8:55::8/126, 2001:0db8:55::000c } sport { 1024-1025, 1026 } dport { 443, 444-445 } mark { 0x10-0x12, 18, 25 } jump Review
chain inet mangle review hook prerouting priority -140 policy drop
""".strip()
    expected = """chain family=inet table=mangle chain=preroute hook=prerouting priority=-150 policy=accept
chain family=inet table=mangle chain=review hook=prerouting priority=-140 policy=drop
rule family=inet table=mangle chain=preroute hook=prerouting priority=-150 pos=1 ct=new,established iif=wan1,wan2 oif=dmz proto=tcp,udp saddr=10.10.0.4-10.10.0.6,10.10.0.8-10.10.0.9 daddr=2001:db8:55::8-2001:db8:55::c sport=1024-1026 dport=443-445 mark=16-18,25 action=jump:review
"""
    assert run_compile(source, tmp_path) == expected


def test_compare_reordered_interface_mark_forms(tmp_path: Path) -> None:
    """Compare treats reordered interface, protocol, and mark forms behaviorally."""
    rebuild_tool()
    left = """
chain ip filter egress hook output priority 0 policy drop
rule ip filter egress oif { uplinkB, uplinkA } proto { icmp, udp } mark { 7, 0x08-0x09 } saddr 198.51.100.1 dport 53 masquerade
""".strip()
    same = """
rule ip filter egress mark 0x7-9 proto { udp, icmp } oif { uplinkA, uplinkB } dport 53 saddr 198.51.100.1 masquerade
chain ip filter egress hook output priority 0 policy drop
""".strip()
    different = """
chain ip filter egress hook output priority 0 policy drop
rule ip filter egress oif { uplinkA, uplinkB } proto udp mark 7-9 saddr 198.51.100.1 dport 53 masquerade
""".strip()
    assert run_compare(left, same, tmp_path) == "equivalent"
    assert run_compare(left, different, tmp_path) == "different"


def test_compile_late_metadata_with_proto_mark_and_destination(tmp_path: Path) -> None:
    """Late chain metadata combines with advanced match normalization."""
    rebuild_tool()
    source = """
rule ip6 nat translate proto { icmpv6, tcp } daddr @DST mark { 0x2, 3-4 } dport { 8444, 8443 } snat:2001:db8:ffff::1:443
set dst = { 2001:db8:90::10/125, 2001:db8:90::18-2001:db8:90::1f }
chain ip6 nat translate hook postrouting priority 100 policy accept
""".strip()
    expected = """chain family=ip6 table=nat chain=translate hook=postrouting priority=100 policy=accept
rule family=ip6 table=nat chain=translate hook=postrouting priority=100 pos=1 ct=* iif=* oif=* proto=tcp,icmpv6 saddr=* daddr=2001:db8:90::10-2001:db8:90::1f dport=8443-8444 mark=2-4 action=snat:2001:db8:ffff::1:443
"""
    assert run_compile(source, tmp_path) == expected


def test_compile_uid_and_tcpflags_with_late_sets(tmp_path: Path) -> None:
    """User-id spans and TCP flags canonicalize with late set resolution."""
    rebuild_tool()
    source = """
rule inet filter broker proto { udp, tcp } tcpflags { ack, syn, fin, syn } saddr @STAFF daddr @DST sport { 1024-1025, 1026 } dport { 443, 444-445 } uid { 1002, 1000-1001, 1003 } mark { 0x20, 33-34 } accept
set dst = { 2001:db8:aa::8/126, 2001:db8:aa::c-2001:db8:aa::f }
chain inet filter broker hook forward priority -30 policy drop
set staff = { 198.51.100.80/30, 198.51.100.84-198.51.100.87 }
""".strip()
    expected = """chain family=inet table=filter chain=broker hook=forward priority=-30 policy=drop
rule family=inet table=filter chain=broker hook=forward priority=-30 pos=1 ct=* iif=* oif=* proto=tcp,udp tcpflags=fin,syn,ack saddr=198.51.100.80-198.51.100.87 daddr=2001:db8:aa::8-2001:db8:aa::f sport=1024-1026 dport=443-445 uid=1000-1003 mark=32-34 action=accept
"""
    assert run_compile(source, tmp_path) == expected


def test_compare_uid_and_flag_equivalence_is_behavioral(tmp_path: Path) -> None:
    """Equivalence includes user-id intervals and TCP-flag semantics."""
    rebuild_tool()
    left = """
chain ip filter egress hook output priority 0 policy drop
rule ip filter egress proto tcp tcpflags { syn, ack } saddr 203.0.113.10 uid { 500, 501-502 } dport 8443 accept
""".strip()
    same = """
rule ip filter egress uid 500-502 dport 8443 saddr 203.0.113.10 tcpflags { ack, syn } proto tcp accept
chain ip filter egress hook output priority 0 policy drop
""".strip()
    different_uid = """
chain ip filter egress hook output priority 0 policy drop
rule ip filter egress proto tcp tcpflags { syn, ack } saddr 203.0.113.10 uid 500-501 dport 8443 accept
""".strip()
    different_flags = """
chain ip filter egress hook output priority 0 policy drop
rule ip filter egress proto tcp tcpflags syn saddr 203.0.113.10 uid 500-502 dport 8443 accept
""".strip()
    assert run_compare(left, same, tmp_path) == "equivalent"
    assert run_compare(left, different_uid, tmp_path) == "different"
    assert run_compare(left, different_flags, tmp_path) == "different"


def test_compile_typed_references_symbolic_priority_and_zones(tmp_path: Path) -> None:
    """Context-dependent references, zones, and symbolic priorities compile together."""
    rebuild_tool()
    source = """
rule inet filter ingress iif @EDGE oif @INSIDE proto { udp, tcp } saddr @SERVICE daddr @DST sport @SERVICE dport @SERVICE mark { 0x2a, 43 } accept
ports service = { 443, 444-445, @extra }
set service = { 198.51.100.120/30, 198.51.100.124-198.51.100.126 }
zone edge = { wan2, @backup, wan1 }
chain inet filter ingress hook input priority filter policy drop
ports extra = { 446, 450-451 }
zone backup = { wan0, wan2 }
set dst = { 2001:db8:70::10/126, 2001:0db8:70::14-2001:db8:70::17 }
zone inside = { lan2, lan1, lan1 }
""".strip()
    expected = """chain family=inet table=filter chain=ingress hook=input priority=0 policy=drop
rule family=inet table=filter chain=ingress hook=input priority=0 pos=1 ct=* iif=wan0,wan1,wan2 oif=lan1,lan2 proto=tcp,udp saddr=198.51.100.120-198.51.100.126 daddr=2001:db8:70::10-2001:db8:70::17 sport=443-446,450-451 dport=443-446,450-451 mark=42-43 action=accept
"""
    assert run_compile(source, tmp_path) == expected


def test_compare_same_name_references_are_field_scoped(tmp_path: Path) -> None:
    """The same reference name resolves through address, port, or zone context."""
    left = """
rule inet filter cross iif @shared saddr @shared sport @shared dport @shared accept
ports shared = { 1000-1001, 1002 }
set shared = { 10.0.5.1, 10.0.5.2-10.0.5.3 }
zone shared = { dmzB, dmzA }
chain inet filter cross hook forward priority filter policy drop
""".strip()
    same = """
chain inet filter cross hook forward priority 0 policy drop
rule inet filter cross sport 1000-1002 dport 1000-1002 saddr 10.0.5.1-10.0.5.3 iif { dmzA, dmzB } accept
""".strip()
    wrong_context = """
chain inet filter cross hook forward priority 0 policy drop
rule inet filter cross sport 1000-1002 dport 1000-1002 saddr 10.0.5.1-10.0.5.3 iif shared accept
""".strip()
    rebuild_tool()
    assert run_compare(left, same, tmp_path) == "equivalent"
    assert run_compare(left, wrong_context, tmp_path) == "different"


def test_compile_transitive_port_sets_preserve_numeric_gaps(tmp_path: Path) -> None:
    """Nested port references coalesce adjacent spans without hiding gaps."""
    rebuild_tool()
    source = """
ports common = { 22, 23-24, @extra }
ports extra = { 25, 30, 31-32 }
rule ip filter svc dport @COMMON sport { @extra, 29 } accept
chain ip filter svc hook input priority filter policy drop
""".strip()
    expected = """chain family=ip table=filter chain=svc hook=input priority=0 policy=drop
rule family=ip table=filter chain=svc hook=input priority=0 pos=1 ct=* saddr=* sport=25,29-32 dport=22-25,30-32 action=accept
"""
    assert run_compile(source, tmp_path) == expected


def test_compare_symbolic_priorities_remain_behavioral(tmp_path: Path) -> None:
    """Priority aliases canonicalize to integers but still affect chain order."""
    aliases = """
chain ip nat pre hook prerouting priority dstnat policy accept
chain ip mangle premark hook prerouting priority mangle policy accept
rule ip nat pre dport 80 dnat:10.0.0.2:80
rule ip mangle premark mark 0x10 dport 80 accept
""".strip()
    integers = """
chain ip mangle premark hook prerouting priority -150 policy accept
chain ip nat pre hook prerouting priority -100 policy accept
rule ip mangle premark mark 16 dport 80 accept
rule ip nat pre dport 80 dnat:10.0.0.2:80
""".strip()
    wrong_priority = """
chain ip mangle premark hook prerouting priority -100 policy accept
chain ip nat pre hook prerouting priority -100 policy accept
rule ip mangle premark mark 16 dport 80 accept
rule ip nat pre dport 80 dnat:10.0.0.2:80
""".strip()
    rebuild_tool()
    assert run_compare(aliases, integers, tmp_path) == "equivalent"
    assert run_compare(aliases, wrong_priority, tmp_path) == "different"


def test_compile_constants_priority_offsets_and_subtractive_sets(tmp_path: Path) -> None:
    """Constants, priority offsets, and except intervals combine in canonical output."""
    rebuild_tool()
    source = """
define LOW = 1000
define HIGH = 1007
define GAP = 1003-1004
define MARK_LO = 0x20
define MARK_HI = 0x25
chain inet filter audit hook forward priority filter+7 policy drop
set campus = { 198.51.100.0/29 except 198.51.100.3-198.51.100.4, 198.51.100.4 }
ports svc = { $LOW-$HIGH except $GAP, 1004, 1010 }
rule inet filter audit ct new saddr @campus dport @svc uid { 500-505 except 502-503, 503 } mark { $MARK_LO-$MARK_HI except 0x22-0x23, 0x23 } accept
""".strip()
    expected = """chain family=inet table=filter chain=audit hook=forward priority=7 policy=drop
rule family=inet table=filter chain=audit hook=forward priority=7 pos=1 ct=new saddr=198.51.100.0-198.51.100.2,198.51.100.4-198.51.100.7 dport=1000-1002,1004-1007,1010 uid=500-501,503-505 mark=32-33,35-37 action=accept
"""
    assert run_compile(source, tmp_path) == expected


def test_compare_subtractive_references_and_constants_are_behavioral(tmp_path: Path) -> None:
    """Compare treats equivalent subtractive and flat interval policies equally."""
    rebuild_tool()
    subtractive = """
define BASE = 203.0.113.16/28
define CUT = 203.0.113.20-203.0.113.25
chain ip filter perimeter hook input priority mangle-5 policy drop
set visible = { $BASE except $CUT, 203.0.113.25 }
ports app = { 8000-8008 except 8002-8006, 8004-8005 }
rule ip filter perimeter saddr @visible sport @app dport { 443, 444-446 except 445 } goto Review
""".strip()
    flat = """
chain ip filter perimeter hook input priority -155 policy drop
rule ip filter perimeter saddr { 203.0.113.16-203.0.113.19, 203.0.113.25-203.0.113.31 } sport { 8000-8001, 8004-8005, 8007-8008 } dport { 443-444, 446 } goto review
""".strip()
    different = """
chain ip filter perimeter hook input priority -155 policy drop
rule ip filter perimeter saddr { 203.0.113.16-203.0.113.19, 203.0.113.26-203.0.113.31 } sport { 8000-8001, 8004-8005, 8007-8008 } dport { 443-444, 446 } goto review
""".strip()
    assert run_compare(subtractive, flat, tmp_path) == "equivalent"
    assert run_compare(subtractive, different, tmp_path) == "different"
