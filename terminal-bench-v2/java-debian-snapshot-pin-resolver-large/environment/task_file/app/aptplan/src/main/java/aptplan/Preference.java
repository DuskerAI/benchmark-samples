package aptplan;

public class Preference {
    public String pkg = "*";
    public String snapshot = "";
    public String suite = "";
    public int priority = 500;
    public int order = 0;

    public boolean matches(AptPackage p) {
        boolean pkgOk = pkg.equals("*") || pkg.equals(p.name);
        boolean snapOk = snapshot.isEmpty() || snapshot.equals(p.snapshot);
        boolean suiteOk = suite.isEmpty() || suite.equals(p.suite);
        return pkgOk && snapOk && suiteOk;
    }

    public int specificity() {
        int s = 0;
        if (!pkg.equals("*")) s += 4;
        if (!snapshot.isEmpty()) s += 2;
        if (!suite.isEmpty()) s += 1;
        return s;
    }
}
