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
            AptPackage best = null;
            for (AptPackage p : packages) {
                if (!p.name.equals(name)) continue;
                if (!p.architecture.equals(request.architecture) && !p.architecture.equals("all")) continue;
                if (best == null || VersionUtil.compare(p.version, best.version) > 0) best = p;
            }
            if (best != null) selected.put(best.name, new Decision(best, "requested"));
        }
        for (AptPackage p : packages) {
            if (!selected.containsKey(p.name)) rejected.add(p);
        }
        return new Plan(request, selected, rejected, snapshotCount());
    }

    private int snapshotCount() {
        Set<String> s = new TreeSet<>();
        for (AptPackage p : packages) s.add(p.snapshot);
        return s.size();
    }
}
