package aptplan;

public class VersionUtil {
    public static int compare(String a, String b) {
        return a.compareTo(b);
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
}
