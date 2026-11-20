from __future__ import annotations

from functools import cmp_to_key

from sqlglot import exp, planner
from sqlglot.errors import ExecuteError
from sqlglot.executor.table import Table
from sqlglot.transforms import eliminate_window_clause


def plan_windows(expression, ctes):
    """Separate grouping, row-preserving windows, and post-window visibility."""
    expression = eliminate_window_clause(expression.copy())
    used = {identifier.name for identifier in expression.find_all(exp.Identifier)}

    def fresh():
        name = f"_window_{len(used)}"
        while name in used:
            name += "_"
        used.add(name)
        return name

    relation = fresh()
    materialized = {}

    def capture(node):
        if node not in materialized:
            materialized[node.copy()] = fresh()
        return exp.column(materialized[node], relation, quoted=True)

    group = expression.args.get("group")
    group_keys = set(group.expressions) if group else set()

    def prepare(node):
        parent = node.parent
        window_function = isinstance(parent, exp.Window) or (
            isinstance(parent, exp.Filter) and isinstance(parent.parent, exp.Window)
        )
        if isinstance(node, exp.AggFunc) and not window_function:
            return capture(node)
        if node in group_keys or isinstance(node, exp.Column) and node.table:
            return capture(node)
        return node

    # Transform only expressions visible after grouping; FROM/WHERE/HAVING keep
    # their original bindings. Traversal prunes captured aggregate operands.
    expression.set("expressions", [node.transform(prepare) for node in expression.expressions])
    for key in ("qualify", "order"):
        clause = expression.args.get(key)
        if clause is not None:
            expression.set(key, clause.transform(prepare))

    before = expression.copy()
    for key in ("qualify", "order", "limit", "offset", "distinct", "windows"):
        before.set(key, None)
    before.set(
        "expressions",
        [exp.alias_(node.copy(), name, quoted=True) for node, name in materialized.items()]
        or [exp.alias_(exp.Literal.number(1), fresh(), quoted=True)],
    )
    prior = planner.Step.from_expression(before, ctes)
    prior.name = relation
    stage = planner.Window()
    stage.name = relation
    stage.add_dependency(prior)

    def replace_window(node):
        if isinstance(node, exp.Window):
            name = next((name for name, value in stage.windows.items() if value == node), None)
            if name is None:
                name = fresh()
                stage.windows[name] = node.copy()
            return exp.column(name, relation, quoted=True)
        return node

    outputs = [node.transform(replace_window) for node in expression.expressions]
    after = exp.select(*outputs).from_(exp.to_table(relation).as_(relation))
    for key in ("distinct", "order", "limit", "offset"):
        clause = expression.args.get(key)
        if clause is not None:
            after.set(key, clause.transform(replace_window))
    qualify = expression.args.get("qualify")
    if qualify is not None:
        after.set("where", exp.Where(this=qualify.this.transform(replace_window)))
    return planner.Step.from_expression(after, {relation: stage})


def execute_windows(executor, step, context):
    # The upstream step has materialized source expressions. Keep its physical
    # rows unchanged; each window computes a permutation of stable row indices.
    rows = list(context.table.rows)
    values = []
    for window in step.windows.values():
        values.append(WindowEvaluator(executor, context, rows, window).evaluate())
    table = Table(
        (*context.columns, *step.windows),
        [row + tuple(result[i] for result in values) for i, row in enumerate(rows)],
    )
    return executor.context({step.name: table})


class WindowEvaluator:
    def __init__(self, executor, context, rows, window):
        self.executor, self.context, self.rows = executor, context, rows
        self.window = window
        self.order = window.args.get("order")
        self.ordered = self.order.expressions if self.order else []
        self.spec = window.args.get("spec")
        self.kind = str(self.spec.args.get("kind") or "RANGE").upper() if self.spec else "RANGE"
        self.function = window.this
        self.filter = None
        if isinstance(self.function, exp.Filter):
            self.filter = executor.generate(self.function.expression.this)
            self.function = self.function.this
        self.partition_codes = executor.generate_tuple(window.args.get("partition_by"))
        self.order_codes = executor.generate_tuple([ordered.this for ordered in self.ordered])
        self.arguments = {}
        self.validate()

    @staticmethod
    def number(node, *, integer=False, positive=False):
        if not isinstance(node, exp.Literal) or not node.is_number:
            raise ExecuteError("Window offset must be a non-negative numeric literal")
        value = node.to_py()
        if value < (1 if positive else 0) or integer and int(value) != value:
            raise ExecuteError("Invalid window offset")
        return int(value) if integer or isinstance(value, int) else float(value)

    def validate(self):
        function = self.function
        supported = (
            exp.Sum,
            exp.Count,
            exp.Min,
            exp.Max,
            exp.Avg,
            exp.RowNumber,
            exp.Rank,
            exp.DenseRank,
            exp.PercentRank,
            exp.CumeDist,
            exp.Ntile,
            exp.Lag,
            exp.Lead,
            exp.FirstValue,
            exp.LastValue,
            exp.NthValue,
        )
        if not isinstance(function, supported) or function.find(exp.Distinct):
            raise ExecuteError("Unsupported native window function")
        if self.filter is not None and not isinstance(
            function, (exp.Sum, exp.Count, exp.Min, exp.Max, exp.Avg)
        ):
            raise ExecuteError("FILTER requires an aggregate window function")
        if self.kind not in {"ROWS", "RANGE", "GROUPS"}:
            raise ExecuteError("Unsupported window frame unit")
        if self.spec:
            for boundary in ("start", "end"):
                node = self.spec.args.get(boundary)
                if isinstance(node, exp.Expr):
                    self.number(node, integer=self.kind != "RANGE")
                    if self.kind == "RANGE" and len(self.ordered) != 1:
                        raise ExecuteError("Offset RANGE requires one ordering expression")
        if isinstance(function, (exp.Lag, exp.Lead)) and function.args.get("offset") is not None:
            self.number(function.args["offset"], integer=True)
        if isinstance(function, exp.NthValue):
            self.number(function.args["offset"], integer=True, positive=True)
        if isinstance(function, exp.Ntile):
            self.number(function.this, integer=True, positive=True)

    def argument(self, node, row_index):
        if node is None:
            return None
        code = self.arguments.get(node)
        if code is None:
            code = self.arguments[node] = self.executor.generate(node)
        self.context.set_row(self.rows[row_index])
        return self.context.eval(code)

    def compare(self, left, right):
        for a, b, ordered in zip(left, right, self.ordered):
            if a == b:
                continue
            if a is None or b is None:
                return -1 if (a is None) == bool(ordered.args.get("nulls_first")) else 1
            value = (a > b) - (a < b)
            return -value if ordered.args.get("desc") else value
        return 0

    def evaluate(self):
        partitions = {}
        keys = {}
        for i, row in enumerate(self.rows):
            self.context.set_row(row)
            partitions.setdefault(self.context.eval_tuple(self.partition_codes), []).append(i)
            keys[i] = self.context.eval_tuple(self.order_codes)
        result = [None] * len(self.rows)
        for indices in partitions.values():
            indices.sort(key=cmp_to_key(lambda a, b: self.compare(keys[a], keys[b])))
            peers = []
            group_of = []
            for i, index in enumerate(indices):
                if not peers or keys[index] != keys[indices[i - 1]]:
                    peers.append([i, i])
                peers[-1][1] = i
                group_of.append(len(peers) - 1)
            for i, index in enumerate(indices):
                frame = self.frame(i, indices, keys, peers, group_of)
                result[index] = self.value(i, indices, frame, peers, group_of)
        return result

    def frame(self, i, indices, keys, peers, group_of):
        length = len(indices)
        group = group_of[i]

        def bound(start):
            field = "start" if start else "end"
            if self.spec:
                value = self.spec.args.get(field) or "CURRENT ROW"
                side = str(self.spec.args.get(f"{field}_side") or "").upper()
            else:
                value, side = ("UNBOUNDED", "PRECEDING") if start else ("CURRENT ROW", "")
            if str(value).upper() == "UNBOUNDED":
                return 0 if side == "PRECEDING" else length - 1
            current = str(value).upper() == "CURRENT ROW"
            offset = 0 if current else self.number(value, integer=self.kind != "RANGE")
            delta = -offset if side == "PRECEDING" else offset
            if self.kind == "ROWS":
                return i + delta
            if self.kind == "GROUPS":
                target = group + delta
                if target < 0:
                    return 0 if start else -1
                if target >= len(peers):
                    return length if start else length - 1
                return peers[target][0 if start else 1]
            if current or not self.ordered or not isinstance(keys[indices[i]][0], (int, float)):
                return peers[group][0 if start else 1]
            desc = bool(self.ordered[0].args.get("desc"))
            target = keys[indices[i]][0] + (-delta if desc else delta)
            matches = []
            for j, index in enumerate(indices):
                value = keys[index][0]
                if isinstance(value, (int, float)) and (
                    (value <= target if desc else value >= target)
                    if start
                    else (value >= target if desc else value <= target)
                ):
                    matches.append(j)
            return (matches[0] if start else matches[-1]) if matches else (length if start else -1)

        start, end = bound(True), bound(False)
        exclude = (
            str(self.spec.args.get("exclude") or "NO OTHERS").upper() if self.spec else "NO OTHERS"
        )
        return [
            j
            for j in range(max(0, start), min(length, end + 1))
            if not (
                exclude == "CURRENT ROW"
                and j == i
                or exclude == "GROUP"
                and group_of[j] == group
                or exclude == "TIES"
                and group_of[j] == group
                and j != i
            )
        ]

    def value(self, i, indices, frame, peers, group_of):
        function = self.function
        length = len(indices)
        first, last = peers[group_of[i]]
        if isinstance(function, exp.RowNumber):
            return i + 1
        if isinstance(function, exp.Rank):
            return first + 1
        if isinstance(function, exp.DenseRank):
            return group_of[i] + 1
        if isinstance(function, exp.PercentRank):
            return first / (length - 1) if length > 1 else 0.0
        if isinstance(function, exp.CumeDist):
            return (last + 1) / length
        if isinstance(function, exp.Ntile):
            buckets = self.number(function.this, integer=True, positive=True)
            small, large_count = divmod(length, buckets)
            cutoff = (small + 1) * large_count
            return i // (small + 1) + 1 if i < cutoff else large_count + (i - cutoff) // small + 1
        if isinstance(function, (exp.Lag, exp.Lead)):
            offset = (
                self.number(function.args["offset"], integer=True)
                if function.args.get("offset") is not None
                else 1
            )
            target = i + (offset if isinstance(function, exp.Lead) else -offset)
            return (
                self.argument(function.this, indices[target])
                if 0 <= target < length
                else self.argument(function.args.get("default"), indices[i])
            )
        if isinstance(function, (exp.FirstValue, exp.LastValue, exp.NthValue)):
            target = -1 if isinstance(function, exp.LastValue) else 0
            if isinstance(function, exp.NthValue):
                target = self.number(function.args["offset"], integer=True, positive=True) - 1
            return (
                self.argument(function.this, indices[frame[target]])
                if frame and target < len(frame)
                else None
            )
        selected = []
        for j in frame:
            index = indices[j]
            self.context.set_row(self.rows[index])
            if self.filter is None or self.context.eval(self.filter):
                selected.append(self.argument(function.this, index))
        return self.executor.env[function.key.upper()](selected)
