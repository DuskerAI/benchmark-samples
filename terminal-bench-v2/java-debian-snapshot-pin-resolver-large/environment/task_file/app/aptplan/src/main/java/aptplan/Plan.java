package aptplan;

import java.util.*;

public class Plan {
    public final Request request;
    public final Map<String, Decision> selected;
    public final List<AptPackage> rejected;
    public final int snapshotCount;

    public Plan(Request request, Map<String, Decision> selected, List<AptPackage> rejected, int snapshotCount) {
        this.request = request;
        this.selected = selected;
        this.rejected = rejected;
        this.snapshotCount = snapshotCount;
    }
}
