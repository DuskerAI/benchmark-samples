#!/usr/bin/env bash
set -euo pipefail
cd /app/aptplan

cat > src/main/java/aptplan/VersionUtil.java <<'JAVA'
package aptplan;

public class VersionUtil {
    public static int compare(String a, String b) {
        Parts pa = split(a);
        Parts pb = split(b);
        if (pa.epoch != pb.epoch) return Integer.compare(pa.epoch, pb.epoch);
        int c = verrevcmp(pa.upstream, pb.upstream);
        if (c != 0) return c;
        return verrevcmp(pa.revision, pb.revision);
    }

    public static boolean satisfies(String version, String op, String wanted) {
        if (op == null || op.isEmpty()) return true;
        int c = compare(version, wanted);
        return switch (op) {
            case ">=" -> c >= 0;
            case ">" -> c > 0;
            case "=" -> c == 0;
            case "<=" -> c <= 0;
            case "<" -> c < 0;
            default -> true;
        };
    }

    private record Parts(int epoch, String upstream, String revision) {}

    private static Parts split(String v) {
        int epoch = 0;
        String rest = v;
        int colon = v.indexOf(':');
        if (colon >= 0) {
            epoch = Integer.parseInt(v.substring(0, colon));
            rest = v.substring(colon + 1);
        }
        int dash = rest.lastIndexOf('-');
        if (dash >= 0) return new Parts(epoch, rest.substring(0, dash), rest.substring(dash + 1));
        return new Parts(epoch, rest, "");
    }

    private static int order(char c) {
        if (c == 0) return 0;
        if (c == '~') return -1;
        if (Character.isLetter(c)) return c;
        return c + 256;
    }

    private static int verrevcmp(String a, String b) {
        int ia = 0, ib = 0;
        while (ia < a.length() || ib < b.length()) {
            while ((ia < a.length() && !Character.isDigit(a.charAt(ia))) ||
                   (ib < b.length() && !Character.isDigit(b.charAt(ib)))) {
                char ca = ia < a.length() && !Character.isDigit(a.charAt(ia)) ? a.charAt(ia) : 0;
                char cb = ib < b.length() && !Character.isDigit(b.charAt(ib)) ? b.charAt(ib) : 0;
                int oa = order(ca), ob = order(cb);
                if (oa != ob) return Integer.compare(oa, ob);
                if (ca != 0) ia++;
                if (cb != 0) ib++;
            }
            while (ia < a.length() && a.charAt(ia) == '0') ia++;
            while (ib < b.length() && b.charAt(ib) == '0') ib++;
            int ja = ia, jb = ib;
            while (ja < a.length() && Character.isDigit(a.charAt(ja))) ja++;
            while (jb < b.length() && Character.isDigit(b.charAt(jb))) jb++;
            String da = a.substring(ia, ja), db = b.substring(ib, jb);
            if (da.length() != db.length()) return Integer.compare(da.length(), db.length());
            int c = da.compareTo(db);
            if (c != 0) return c < 0 ? -1 : 1;
            ia = ja; ib = jb;
        }
        return 0;
    }
}
JAVA

cat > src/main/java/aptplan/Resolver.java <<'JAVA'
package aptplan;

import java.util.*;

public class Resolver {
    private final List<AptPackage> packages;
    private final List<Preference> preferences;
    private final Request request;
    private final Map<String, Decision> selected = new LinkedHashMap<>();
    private final List<AptPackage> rejected = new ArrayList<>();

    public Resolver(List<AptPackage> packages, List<Preference> preferences, Request request) {
        this.packages = packages;
        this.preferences = preferences;
        this.request = request;
    }

    public Plan resolve() {
        for (String name : request.requested) {
            Relation r = new Relation();
            r.name = name;
            String resolved = resolveRelation(r, new TreeSet<>(), true);
            if (resolved == null) throw new IllegalStateException("unresolved requested package " + name);
        }
        Set<String> selectedKeys = new TreeSet<>();
        for (Decision d : selected.values()) selectedKeys.add(d.pkg.key());
        for (AptPackage p : packages) {
            if (!selectedKeys.contains(p.key())) rejected.add(p);
        }
        rejected.sort(Comparator.comparing((AptPackage p) -> p.name).thenComparing(p -> p.version).thenComparing(p -> p.snapshot));
        return new Plan(request, selected, rejected, snapshotCount());
    }

    private String resolveRelation(Relation rel, Set<String> stack, boolean requestedReason) {
        if (!rel.appliesToArch(request.architecture)) return null;
        for (Decision d : selected.values()) {
            if (satisfies(d.pkg, rel)) return d.pkg.name;
        }
        for (AptPackage cand : candidates(rel)) {
            if (stack.contains(cand.name)) continue;
            Map<String, Decision> selectedBefore = new LinkedHashMap<>(selected);
            if (trySelect(cand, requestedReason ? "requested" : "dependency", extend(stack, cand.name))) {
                return cand.name;
            }
            selected.clear();
            selected.putAll(selectedBefore);
        }
        return null;
    }

    private boolean trySelect(AptPackage cand, String reason, Set<String> stack) {
        if (conflictsWithSelected(cand)) return false;
        Decision dec = new Decision(cand, reason);
        selected.put(cand.name, dec);
        for (List<Relation> group : Relation.parseDepends(cand.depends)) {
            String resolved = null;
            for (Relation alt : group) {
                if (!alt.appliesToArch(request.architecture)) { resolved = ""; break; }
                resolved = resolveRelation(alt, stack, false);
                if (resolved != null) break;
            }
            if (resolved == null) {
                selected.remove(cand.name);
                return false;
            }
            if (!resolved.isEmpty()) dec.dependencies.add(resolved);
        }
        return true;
    }

    private Set<String> extend(Set<String> stack, String name) {
        Set<String> out = new TreeSet<>(stack);
        out.add(name);
        return out;
    }

    private List<AptPackage> candidates(Relation rel) {
        List<AptPackage> out = new ArrayList<>();
        for (AptPackage p : packages) {
            if (!eligible(p)) continue;
            if (satisfies(p, rel)) out.add(p);
        }
        out.sort((a, b) -> {
            int pa = priority(a), pb = priority(b);
            if (pa != pb) return Integer.compare(pb, pa);
            int vc = VersionUtil.compare(b.version, a.version);
            if (vc != 0) return vc;
            int sc = a.snapshot.compareTo(b.snapshot);
            if (sc != 0) return sc;
            int nc = a.name.compareTo(b.name);
            if (nc != 0) return nc;
            return a.architecture.compareTo(b.architecture);
        });
        return out;
    }

    private boolean eligible(AptPackage p) {
        if (!p.architecture.equals(request.architecture) && !p.architecture.equals("all")) return false;
        if (!p.trusted) return false;
        if (request.forbiddenSuites.contains(p.suite)) return false;
        if (request.held.contains(p.name)) return false;
        return priority(p) >= 0;
    }

    private boolean satisfies(AptPackage p, Relation rel) {
        if (p.name.equals(rel.name)) return VersionUtil.satisfies(p.version, rel.op, rel.version);
        return p.providesNames().contains(rel.name);
    }

    private boolean conflictsWithSelected(AptPackage cand) {
        Set<String> candBad = new TreeSet<>();
        candBad.addAll(Relation.parseNameSet(cand.conflicts));
        candBad.addAll(Relation.parseNameSet(cand.breaksField));
        for (Decision d : selected.values()) {
            AptPackage other = d.pkg;
            Set<String> otherBad = new TreeSet<>();
            otherBad.addAll(Relation.parseNameSet(other.conflicts));
            otherBad.addAll(Relation.parseNameSet(other.breaksField));
            if (candBad.contains(other.name) || otherBad.contains(cand.name)) return true;
        }
        return false;
    }

    private int priority(AptPackage p) {
        Preference best = null;
        for (Preference pref : preferences) {
            if (!pref.matches(p)) continue;
            if (best == null || pref.specificity() > best.specificity() ||
                (pref.specificity() == best.specificity() && pref.order >= best.order)) {
                best = pref;
            }
        }
        return best == null ? 500 : best.priority;
    }

    private int snapshotCount() {
        Set<String> s = new TreeSet<>();
        for (AptPackage p : packages) s.add(p.snapshot);
        return s.size();
    }
}
JAVA

/app/aptplan/bin/build.sh
java -jar /app/aptplan/build/aptplan.jar \
  --root /var/lib/aptmirror \
  --request /etc/aptplan/request.json \
  --preferences /etc/aptplan/preferences.d \
  --out /app/out
python3 - <<'PYCHECK'
import json
from pathlib import Path
for p in [Path('/app/out/install_plan.json'), Path('/app/out/audit.ndjson')]:
    if not p.exists():
        raise SystemExit(f'missing {p}')
json.loads(Path('/app/out/install_plan.json').read_text())
for line in Path('/app/out/audit.ndjson').read_text().splitlines():
    if line.strip():
        json.loads(line)
PYCHECK
