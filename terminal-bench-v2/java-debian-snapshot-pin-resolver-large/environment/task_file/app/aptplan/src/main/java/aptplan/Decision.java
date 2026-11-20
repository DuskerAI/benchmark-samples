package aptplan;

import java.util.*;

public class Decision {
    public AptPackage pkg;
    public String reason;
    public Set<String> dependencies = new TreeSet<>();

    public Decision(AptPackage pkg, String reason) {
        this.pkg = pkg;
        this.reason = reason;
    }
}
