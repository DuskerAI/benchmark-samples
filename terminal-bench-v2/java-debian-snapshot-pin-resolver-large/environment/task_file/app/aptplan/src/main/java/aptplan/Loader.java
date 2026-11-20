package aptplan;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.regex.*;

public class Loader {
    public static Request loadRequest(Path requestPath) throws IOException {
        String s = Files.readString(requestPath);
        Request r = new Request();
        r.requestId = stringField(s, "request_id");
        r.architecture = stringField(s, "architecture");
        r.requested.addAll(arrayField(s, "requested"));
        r.held.addAll(arrayField(s, "held"));
        r.forbiddenSuites.addAll(arrayField(s, "forbidden_suites"));
        return r;
    }

    private static String stringField(String s, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"").matcher(s);
        if (!m.find()) throw new IllegalArgumentException("missing request field " + key);
        return m.group(1);
    }

    private static List<String> arrayField(String s, String key) {
        Matcher m = Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*\\[(.*?)\\]", Pattern.DOTALL).matcher(s);
        if (!m.find()) return List.of();
        List<String> out = new ArrayList<>();
        Matcher item = Pattern.compile("\\\"([^\\\"]*)\\\"").matcher(m.group(1));
        while (item.find()) out.add(item.group(1));
        return out;
    }

    public static List<Preference> loadPreferences(Path prefDir) throws IOException {
        List<Preference> prefs = new ArrayList<>();
        if (!Files.exists(prefDir)) return prefs;
        List<Path> files = new ArrayList<>();
        try (var stream = Files.list(prefDir)) {
            stream.filter(Files::isRegularFile).sorted().forEach(files::add);
        }
        int order = 0;
        for (Path f : files) {
            for (Map<String, String> stanza : parseStanzas(Files.readString(f))) {
                Preference p = new Preference();
                p.pkg = stanza.getOrDefault("Package", "*");
                p.snapshot = stanza.getOrDefault("Snapshot", "");
                p.suite = stanza.getOrDefault("Suite", "");
                p.priority = Integer.parseInt(stanza.getOrDefault("Pin-Priority", "500"));
                p.order = order++;
                prefs.add(p);
            }
        }
        return prefs;
    }

    public static List<AptPackage> loadPackages(Path root) throws IOException {
        List<AptPackage> out = new ArrayList<>();
        Path snapshots = root.resolve("snapshots");
        try (var dirs = Files.list(snapshots)) {
            for (Path snapDir : dirs.filter(Files::isDirectory).sorted().toList()) {
                Map<String, String> release = parseRelease(snapDir.resolve("Release"));
                String snapshot = release.getOrDefault("Snapshot", snapDir.getFileName().toString());
                String suite = release.getOrDefault("Suite", snapshot);
                boolean trusted = release.getOrDefault("Trusted", "no").equalsIgnoreCase("yes");
                try (var files = Files.list(snapDir)) {
                    for (Path file : files.filter(p -> p.getFileName().toString().equals("Packages")).sorted().toList()) {
                        for (Map<String, String> st : parseStanzas(Files.readString(file))) {
                            AptPackage p = new AptPackage();
                            p.name = st.getOrDefault("Package", "");
                            p.version = st.getOrDefault("Version", "");
                            p.architecture = st.getOrDefault("Architecture", "");
                            p.depends = st.getOrDefault("Depends", "");
                            p.provides = st.getOrDefault("Provides", "");
                            p.conflicts = st.getOrDefault("Conflicts", "");
                            p.breaksField = st.getOrDefault("Breaks", "");
                            p.snapshot = snapshot;
                            p.suite = suite;
                            p.trusted = trusted;
                            if (!p.name.isEmpty()) out.add(p);
                        }
                    }
                }
            }
        }
        return out;
    }

    private static Map<String, String> parseRelease(Path p) throws IOException {
        if (!Files.exists(p)) return Map.of();
        List<Map<String, String>> st = parseStanzas(Files.readString(p));
        return st.isEmpty() ? Map.of() : st.get(0);
    }

    public static List<Map<String, String>> parseStanzas(String text) {
        List<Map<String, String>> out = new ArrayList<>();
        Map<String, String> cur = new LinkedHashMap<>();
        String last = null;
        for (String line : text.split("\\R", -1)) {
            if (line.isBlank()) {
                if (!cur.isEmpty()) { out.add(cur); cur = new LinkedHashMap<>(); last = null; }
                continue;
            }
            if ((line.startsWith(" ") || line.startsWith("\t")) && last != null) {
                cur.put(last, cur.get(last) + "\n" + line.trim());
                continue;
            }
            int idx = line.indexOf(':');
            if (idx > 0) {
                last = line.substring(0, idx).trim();
                cur.put(last, line.substring(idx + 1).trim());
            }
        }
        if (!cur.isEmpty()) out.add(cur);
        return out;
    }
}
