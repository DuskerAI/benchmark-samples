package aptplan;

import java.util.*;

public class Request {
    public String requestId;
    public String architecture;
    public List<String> requested = new ArrayList<>();
    public Set<String> held = new TreeSet<>();
    public Set<String> forbiddenSuites = new TreeSet<>();
}
