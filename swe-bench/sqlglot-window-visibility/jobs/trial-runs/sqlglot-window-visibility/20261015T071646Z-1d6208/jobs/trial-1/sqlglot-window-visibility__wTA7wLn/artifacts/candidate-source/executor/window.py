from __future__ import annotations

import math
import typing as t

from sqlglot import exp
from sqlglot.errors import ExecuteError
from sqlglot.executor.table import Table

if t.TYPE_CHECKING:
    from sqlglot.executor.python import PythonExecutor

# Frame endpoints, ordered by where they sit relative to the current row.
UNBOUNDED_PRECEDING, PRECEDING, CURRENT_ROW, FOLLOWING, UNBOUNDED_FOLLOWING = range(5)

Bound = tuple[int, t.Any]


def _is_number(value: t.Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class _Partition:
    """The rows of one partition in window order, with their peer groups."""

    def __init__(self, ids: list[int], keys: list[tuple]) -> None:
        self.ids = ids
        self.keys = keys  # ordering values by position
        self.size = len(ids)
        self.peer_start: list[int] = []
        self.peer_end: list[int] = []
        self.group: list[int] = []
        self.group_start: list[int] = []
        self.group_end: list[int] = []

        for position, key in enumerate(keys):
            if position and key == keys[position - 1]:
                self.group.append(self.group[-1])
                self.peer_start.append(self.peer_start[-1])
            else:
                self.group.append(len(self.group_start))
                self.group_start.append(position)
                self.peer_start.append(position)

        for position in range(self.size - 1, -1, -1):
            if position + 1 < self.size and self.group[position + 1] == self.group[position]:
                self.peer_end.append(self.peer_end[-1])
            else:
                self.peer_end.append(position)
        self.peer_end.reverse()

        self.group_end = [self.peer_end[start] for start in self.group_start]


class _FrameReader:
    """Presents the rows of a frame to compiled aggregate code as column vectors."""

    def __init__(self, columns: dict[str, int], rows: list[tuple]) -> None:
        self.columns = columns
        self.rows = rows
        self.indices: t.Iterable[int] = ()

    def __getitem__(self, column: str) -> list:
        index = self.columns[column]
        return [self.rows[i][index] for i in self.indices]


class WindowEvaluator:
    """
    Evaluates window functions for a relation whose columns already hold every operand.

    Aggregates are called through the executor's function environment, so custom aggregate and
    scalar functions registered there apply inside windows too.
    """

    def __init__(self, executor: PythonExecutor, table: Table) -> None:
        self.executor = executor
        self.columns = {name: i for i, name in enumerate(table.columns)}
        self.rows = table.rows
        self.env = dict(executor.env)

    def evaluate(self, window: exp.Window) -> list:
        function = window.this
        predicate: int | None = None

        if isinstance(function, exp.Filter):
            predicate = self._column(function.expression.this)
            function = function.this
            if not isinstance(function, exp.AggFunc) or isinstance(function, _WINDOW_ONLY):
                raise ExecuteError("FILTER is only supported for aggregate window functions")

        if isinstance(function, exp.RespectNulls):
            function = function.this
        if isinstance(function, exp.IgnoreNulls):
            raise ExecuteError("IGNORE NULLS is not supported in window functions")
        if function.find(exp.Distinct):
            raise ExecuteError("DISTINCT is not supported in window functions")

        partition = [self._column(e) for e in window.args.get("partition_by") or []]
        order = window.args.get("order")
        ordering = [
            (self._column(o.this), bool(o.args.get("desc")), bool(o.args.get("nulls_first")))
            for o in (order.expressions if order else [])
        ]
        frame = self._frame(window.args.get("spec"), ordering)
        compute = self._function(function, frame, predicate)

        partitions: dict[tuple, list[int]] = {}
        for i, row in enumerate(self.rows):
            partitions.setdefault(tuple(row[c] for c in partition), []).append(i)

        results: list[t.Any] = [None] * len(self.rows)
        ordered = self.env["ORDERED"]

        for ids in partitions.values():
            values = {i: tuple(self.rows[i][c] for c, _, _ in ordering) for i in ids}

            if ordering:
                keys = {
                    i: tuple(
                        ordered(self.rows[i][c], desc, nulls_first)
                        for c, desc, nulls_first in ordering
                    )
                    for i in ids
                }
                ids = sorted(ids, key=keys.__getitem__)

            part = _Partition(ids, [values[i] for i in ids])
            cache: dict = {}

            for position, i in enumerate(ids):
                results[i] = compute(part, position, cache)

        return results

    def _column(self, node: exp.Expr) -> int:
        if not isinstance(node, exp.Column):
            raise ExecuteError(f"Unsupported window operand: {node.sql()}")
        return self.columns[node.name]

    def _constant(self, node: exp.Expr | None, description: str) -> t.Any:
        if node is None or node.find(exp.Column, exp.Subquery):
            raise ExecuteError(f"{description} must be a constant")

        return eval(self.executor.generate(node), self.env)

    def _integer(self, node: exp.Expr | None, description: str, minimum: int) -> int:
        value = self._constant(node, description)

        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ExecuteError(f"{description} must be an integer >= {minimum}")

        return value

    def _bound(self, kind: str, value: t.Any, side: t.Any) -> Bound:
        side = side.upper() if isinstance(side, str) else side

        if isinstance(value, str):
            value = value.upper()
            if value == "CURRENT ROW":
                return CURRENT_ROW, None
            if value == "UNBOUNDED":
                return (UNBOUNDED_PRECEDING if side == "PRECEDING" else UNBOUNDED_FOLLOWING), None
            raise ExecuteError(f"Unsupported window frame boundary: {value}")

        if side not in ("PRECEDING", "FOLLOWING"):
            raise ExecuteError("Window frame offsets must be PRECEDING or FOLLOWING")

        if kind == "RANGE":
            offset = self._constant(value, "RANGE frame offset")
            if not _is_number(offset) or offset < 0 or not math.isfinite(offset):
                raise ExecuteError("RANGE frame offset must be a non-negative number")
        else:
            offset = self._integer(value, f"{kind} frame offset", 0)

        return (PRECEDING if side == "PRECEDING" else FOLLOWING), offset

    def _frame(self, spec: exp.WindowSpec | None, ordering: list[tuple]) -> t.Callable:
        if spec is None:
            kind, start, end, exclude = (
                "RANGE",
                (UNBOUNDED_PRECEDING, None),
                (CURRENT_ROW, None),
                "",
            )
        else:
            kind = str(spec.args["kind"]).upper()
            if kind not in ("ROWS", "RANGE", "GROUPS"):
                raise ExecuteError(f"Unsupported window frame type: {kind}")

            start = self._bound(kind, spec.args.get("start"), spec.args.get("start_side"))
            end = (
                self._bound(kind, spec.args.get("end"), spec.args.get("end_side"))
                if spec.args.get("end") is not None
                else (CURRENT_ROW, None)
            )
            exclude = spec.args.get("exclude")
            exclude = exclude.name.upper().replace(" ", "") if exclude else ""

            if exclude not in ("", "CURRENTROW", "GROUP", "TIES", "NOOTHERS"):
                raise ExecuteError(f"Unsupported window frame exclusion: {exclude}")
            if start[0] == UNBOUNDED_FOLLOWING or end[0] == UNBOUNDED_PRECEDING:
                raise ExecuteError("Invalid window frame: UNBOUNDED FOLLOWING cannot start it")
            if start[0] > end[0]:
                raise ExecuteError("Invalid window frame: it ends before it starts")

            if kind == "RANGE" and (
                PRECEDING in (start[0], end[0]) or FOLLOWING in (start[0], end[0])
            ):
                if len(ordering) != 1:
                    raise ExecuteError("RANGE with an offset requires exactly one ORDER BY term")

        _, desc, nulls_first = ordering[0] if ordering else (None, False, False)

        def rows(bound: Bound, position: int, part: _Partition) -> int:
            kind_, offset = bound
            if kind_ == UNBOUNDED_PRECEDING:
                return 0
            if kind_ == UNBOUNDED_FOLLOWING:
                return part.size - 1
            if kind_ == CURRENT_ROW:
                return position
            return position - offset if kind_ == PRECEDING else position + offset

        def groups(bound: Bound, position: int, part: _Partition) -> int:
            kind_, offset = bound
            last = len(part.group_start) - 1
            if kind_ == UNBOUNDED_PRECEDING:
                return 0
            if kind_ == UNBOUNDED_FOLLOWING:
                return last
            current = part.group[position]
            if kind_ == CURRENT_ROW:
                return current
            return current - offset if kind_ == PRECEDING else current + offset

        def search(predicate: t.Callable[[int], bool], size: int) -> int:
            """The first position in [0, size] for which a False..True predicate holds."""
            low, high = 0, size
            while low < high:
                middle = (low + high) // 2
                if predicate(middle):
                    high = middle
                else:
                    low = middle + 1
            return low

        def range_offset(bound: Bound, position: int, part: _Partition, is_start: bool) -> int:
            current = part.keys[position][0]
            if not _is_number(current):
                return part.peer_start[position] if is_start else part.peer_end[position]

            kind_, offset = bound
            target = current - offset if (kind_ == PRECEDING) != desc else current + offset

            keys = part.keys

            def reaches(q: int) -> bool:
                """Whether row q sorts at or after the target; NULLs sort by NULLS FIRST/LAST."""
                key = keys[q][0]
                if not _is_number(key):
                    return not nulls_first
                return key <= target if desc else key >= target

            def within(q: int) -> bool:
                """Whether row q sorts at or before the target."""
                key = keys[q][0]
                if not _is_number(key):
                    return nulls_first
                return key >= target if desc else key <= target

            if is_start:
                return search(reaches, part.size)
            return search(lambda q: not within(q), part.size) - 1

        def bounds(position: int, part: _Partition) -> tuple[int, int]:
            if kind == "ROWS":
                low, high = rows(start, position, part), rows(end, position, part)
                return max(low, 0), min(high, part.size - 1)

            if kind == "GROUPS":
                low, high = groups(start, position, part), groups(end, position, part)
                low, high = max(low, 0), min(high, len(part.group_start) - 1)
                if low > high:
                    return 0, -1
                return part.group_start[low], part.group_end[high]

            if start[0] in (PRECEDING, FOLLOWING):
                low = range_offset(start, position, part, True)
            elif start[0] == CURRENT_ROW:
                low = part.peer_start[position]
            else:
                low = 0

            if end[0] in (PRECEDING, FOLLOWING):
                high = range_offset(end, position, part, False)
            elif end[0] == CURRENT_ROW:
                high = part.peer_end[position]
            else:
                high = part.size - 1

            return low, high

        def frame(position: int, part: _Partition) -> tuple[tuple, t.Sequence[int]]:
            """The frame's cache key and its positions, with the exclusion applied."""
            low, high = bounds(position, part)
            positions: t.Sequence[int] = range(low, high + 1)
            key: tuple = (low, high)

            if exclude == "CURRENTROW":
                key = (low, high, position)
                positions = [q for q in positions if q != position]
            elif exclude in ("GROUP", "TIES"):
                first, last = part.peer_start[position], part.peer_end[position]
                keep = position if exclude == "TIES" else None
                key = (low, high, first, last, keep)
                positions = [q for q in positions if q == keep or not first <= q <= last]

            return key, positions

        return frame

    def _function(self, function: exp.Expr, frame: t.Callable, predicate: int | None) -> t.Callable:
        rows = self.rows

        if isinstance(function, exp.RowNumber):
            return lambda part, p, cache: p + 1
        if isinstance(function, exp.Rank):
            return lambda part, p, cache: part.peer_start[p] + 1
        if isinstance(function, exp.DenseRank):
            return lambda part, p, cache: part.group[p] + 1
        if isinstance(function, exp.PercentRank):
            return lambda part, p, cache: (
                (part.peer_start[p]) / (part.size - 1) if part.size > 1 else 0.0
            )
        if isinstance(function, exp.CumeDist):
            return lambda part, p, cache: (part.peer_end[p] + 1) / part.size

        if isinstance(function, exp.Ntile):
            buckets = self._integer(function.this, "NTILE bucket count", 1)

            def ntile(part: _Partition, p: int, cache: dict) -> int:
                size, extra = divmod(part.size, buckets)
                boundary = extra * (size + 1)
                if p < boundary:
                    return p // (size + 1) + 1
                return extra + (p - boundary) // size + 1

            return ntile

        if isinstance(function, (exp.Lag, exp.Lead)):
            offset = (
                self._integer(function.args["offset"], "LAG/LEAD offset", 0)
                if function.args.get("offset") is not None
                else 1
            )
            offset = -offset if isinstance(function, exp.Lag) else offset
            value = self._column(function.this)
            default = function.args.get("default")
            default = self._column(default) if default is not None else None

            def navigate(part: _Partition, p: int, cache: dict) -> t.Any:
                target = p + offset
                if 0 <= target < part.size:
                    return rows[part.ids[target]][value]
                return None if default is None else rows[part.ids[p]][default]

            return navigate

        if isinstance(function, (exp.FirstValue, exp.LastValue, exp.NthValue)):
            value = self._column(function.this)
            nth = (
                self._integer(function.args["offset"], "NTH_VALUE offset", 1)
                if isinstance(function, exp.NthValue)
                else 1
            )
            from_end = isinstance(function, exp.LastValue) or (
                isinstance(function, exp.NthValue) and function.args.get("from_first") is False
            )

            def pick(part: _Partition, p: int, cache: dict) -> t.Any:
                _, positions = frame(p, part)
                if nth > len(positions):
                    return None
                position = positions[-nth] if from_end else positions[nth - 1]
                return rows[part.ids[position]][value]

            return pick

        if not isinstance(function, exp.AggFunc):
            raise ExecuteError(f"Unsupported window function: {function.sql()}")

        code = self.executor.generate(function)
        tables = {c.table for c in function.find_all(exp.Column)}
        reader = _FrameReader(self.columns, rows)
        env = self.env

        def aggregate(part: _Partition, p: int, cache: dict) -> t.Any:
            key, positions = frame(p, part)

            if key not in cache:
                ids = [part.ids[q] for q in positions]
                if predicate is not None:
                    ids = [i for i in ids if rows[i][predicate]]
                reader.indices = ids
                env["scope"] = {table: reader for table in tables}
                cache[key] = eval(code, env)

            return cache[key]

        return aggregate


_WINDOW_ONLY = (
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
