package aptplan;

import java.io.*;
import java.nio.file.*;
import java.util.*;

public class JsonWriter {
    public static void write(Plan plan, Path outDir) throws IOException {
        Files.createDirectories(outDir);
        Files.writeString(outDir.resolve("install_plan.json"), installPlan(plan));
        Files.writeString(outDir.resolve("audit.ndjson"), audit(plan));
    }

    private static String installPlan(Plan plan) {
        StringBuilder sb = new StringBuilder();
        List<Decision> selected = new ArrayList<>(plan.selected.values());
        selected.sort(Comparator.comparing(d -> d.pkg.name));
        List<AptPackage> rejected = new ArrayList<>(plan.rejected);
        rejected.sort(Comparator.comparing((AptPackage p) -> p.name).thenComparing(p -> p.version).thenComparing(p -> p.snapshot));
        sb.append("{\n");
        field(sb, "request_id", plan.request.requestId, true, 1);
        field(sb, "architecture", plan.request.architecture, true, 1);
        indent(sb, 1).append("\"selected\": [\n");
        for (int i = 0; i < selected.size(); i++) {
            Decision d = selected.get(i);
            indent(sb, 2).append("{\n");
            field(sb, "package", d.pkg.name, true, 3);
            field(sb, "version", d.pkg.version, true, 3);
            field(sb, "architecture", d.pkg.architecture, true, 3);
            field(sb, "source_snapshot", d.pkg.snapshot, true, 3);
            field(sb, "reason", d.reason, true, 3);
            indent(sb, 3).append("\"dependencies\": "); stringArray(sb, new ArrayList<>(d.dependencies)); sb.append("\n");
            indent(sb, 2).append("}"); if (i + 1 < selected.size()) sb.append(','); sb.append('\n');
        }
        indent(sb, 1).append("],\n");
        indent(sb, 1).append("\"rejected\": [\n");
        for (int i = 0; i < rejected.size(); i++) {
            AptPackage p = rejected.get(i);
            indent(sb, 2).append("{\n");
            field(sb, "package", p.name, true, 3);
            field(sb, "version", p.version, true, 3);
            field(sb, "architecture", p.architecture, true, 3);
            field(sb, "source_snapshot", p.snapshot, true, 3);
            field(sb, "reason", "not-selected", false, 3);
            indent(sb, 2).append("}"); if (i + 1 < rejected.size()) sb.append(','); sb.append('\n');
        }
        indent(sb, 1).append("],\n");
        indent(sb, 1).append("\"summary\": {\n");
        indent(sb, 2).append("\"selected_count\": ").append(selected.size()).append(",\n");
        indent(sb, 2).append("\"rejected_count\": ").append(rejected.size()).append(",\n");
        indent(sb, 2).append("\"snapshot_count\": ").append(plan.snapshotCount).append(",\n");
        indent(sb, 2).append("\"has_conflicts\": false\n");
        indent(sb, 1).append("}\n");
        sb.append("}\n");
        return sb.toString();
    }

    private static String audit(Plan plan) {
        StringBuilder sb = new StringBuilder();
        List<Decision> selected = new ArrayList<>(plan.selected.values());
        selected.sort(Comparator.comparing(d -> d.pkg.name));
        for (Decision d : selected) {
            sb.append("{\"package\":").append(q(d.pkg.name))
              .append(",\"decision\":\"selected\",\"reason\":").append(q(d.reason))
              .append(",\"details\":").append(q(d.pkg.key())).append("}\n");
        }
        List<AptPackage> rejected = new ArrayList<>(plan.rejected);
        rejected.sort(Comparator.comparing((AptPackage p) -> p.name).thenComparing(p -> p.version).thenComparing(p -> p.snapshot));
        for (int i = 0; i < rejected.size(); i++) {
            AptPackage p = rejected.get(i);
            sb.append("{\"package\":").append(q(p.name))
              .append(",\"decision\":\"rejected\",\"reason\":\"not-selected\"")
              .append(",\"details\":").append(q(p.key())).append("}\n");
        }
        return sb.toString();
    }

    private static void field(StringBuilder sb, String key, String value, boolean comma, int n) {
        indent(sb, n).append(q(key)).append(": ").append(q(value));
        if (comma) sb.append(',');
        sb.append('\n');
    }
    private static StringBuilder indent(StringBuilder sb, int n) { return sb.append("  ".repeat(n)); }
    private static String q(String s) { return "\"" + escape(s == null ? "" : s) + "\""; }
    private static String escape(String s) { return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n"); }
    private static void stringArray(StringBuilder sb, List<String> xs) {
        Collections.sort(xs);
        sb.append('[');
        for (int i = 0; i < xs.size(); i++) { if (i > 0) sb.append(", "); sb.append(q(xs.get(i))); }
        sb.append(']');
    }
}
