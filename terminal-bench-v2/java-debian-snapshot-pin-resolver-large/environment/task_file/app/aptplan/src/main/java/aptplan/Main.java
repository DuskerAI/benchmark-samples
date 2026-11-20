package aptplan;

import java.nio.file.*;
import java.util.*;

public class Main {
    public static void main(String[] args) throws Exception {
        Map<String, String> opts = parseArgs(args);
        Path root = Path.of(required(opts, "--root"));
        Path requestPath = Path.of(required(opts, "--request"));
        Path prefDir = Path.of(required(opts, "--preferences"));
        Path out = Path.of(required(opts, "--out"));
        Request request = Loader.loadRequest(requestPath);
        List<AptPackage> packages = Loader.loadPackages(root);
        List<Preference> prefs = Loader.loadPreferences(prefDir);
        Plan plan = new Resolver(packages, prefs, request).resolve();
        JsonWriter.write(plan, out);
    }

    private static Map<String, String> parseArgs(String[] args) {
        Map<String, String> out = new LinkedHashMap<>();
        for (int i = 0; i < args.length; i += 2) {
            if (i + 1 >= args.length) throw new IllegalArgumentException("missing value for " + args[i]);
            out.put(args[i], args[i + 1]);
        }
        return out;
    }
    private static String required(Map<String, String> opts, String key) {
        String v = opts.get(key);
        if (v == null || v.isBlank()) throw new IllegalArgumentException("missing " + key);
        return v;
    }
}
