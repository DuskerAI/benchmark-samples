package aptplan;

import java.util.*;
import java.util.regex.*;

public class Relation {
    public String name;
    public String op = "";
    public String version = "";
    public String archExpr = "";

    public static List<List<Relation>> parseDepends(String raw) {
        List<List<Relation>> groups = new ArrayList<>();
        if (raw == null || raw.isBlank()) return groups;
        for (String group : raw.split(",")) {
            List<Relation> alternatives = new ArrayList<>();
            for (String alt : group.split("\\|")) {
                Relation r = parseOne(alt.trim());
                if (r != null) alternatives.add(r);
            }
            if (!alternatives.isEmpty()) groups.add(alternatives);
        }
        return groups;
    }

    public static Set<String> parseNameSet(String raw) {
        Set<String> out = new TreeSet<>();
        if (raw == null || raw.isBlank()) return out;
        for (String part : raw.split(",")) {
            Relation r = parseOne(part.trim());
            if (r != null) out.add(r.name);
        }
        return out;
    }

    private static Relation parseOne(String text) {
        if (text == null || text.isBlank()) return null;
        Relation r = new Relation();
        Matcher arch = Pattern.compile("\\[([^]]+)\\]").matcher(text);
        if (arch.find()) {
            r.archExpr = arch.group(1).trim();
            text = arch.replaceAll("").trim();
        }
        Matcher ver = Pattern.compile("\\((>=|<=|=|>|<)\\s*([^)]*)\\)").matcher(text);
        if (ver.find()) {
            r.op = ver.group(1);
            r.version = ver.group(2).trim();
            text = ver.replaceAll("").trim();
        }
        r.name = text.trim();
        return r.name.isEmpty() ? null : r;
    }

    public boolean appliesToArch(String arch) {
        if (archExpr == null || archExpr.isBlank()) return true;
        for (String token : archExpr.split("\\s+")) {
            token = token.trim();
            if (token.isEmpty()) continue;
            if (token.startsWith("!")) {
                if (token.substring(1).equals(arch)) return false;
            } else if (token.equals(arch)) {
                return true;
            }
        }
        return archExpr.contains("!");
    }
}
