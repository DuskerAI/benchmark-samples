package aptplan;

import java.util.*;

public class AptPackage {
    public String name;
    public String version;
    public String architecture;
    public String snapshot;
    public String suite;
    public boolean trusted;
    public String depends = "";
    public String provides = "";
    public String conflicts = "";
    public String breaksField = "";

    public String key() {
        return name + "=" + version + "@" + snapshot + "/" + architecture;
    }

    public List<String> providesNames() {
        List<String> out = new ArrayList<>();
        for (String part : provides.split(",")) {
            String s = part.trim();
            if (s.isEmpty()) continue;
            int idx = s.indexOf(' ');
            out.add(idx >= 0 ? s.substring(0, idx).trim() : s);
        }
        return out;
    }
}
