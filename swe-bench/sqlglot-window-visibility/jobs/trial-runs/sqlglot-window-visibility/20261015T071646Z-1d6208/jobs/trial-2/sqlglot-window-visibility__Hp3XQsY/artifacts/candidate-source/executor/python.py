import bisect
import collections
import decimal
import itertools
import math

from sqlglot import exp, planner, tokens
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import ExecuteError
from sqlglot.executor.context import Context
from sqlglot.executor.env import ENV
from sqlglot.executor.table import RowReader, Table
from sqlglot.generators.python import PythonGenerator
from sqlglot.optimizer.scope import build_scope

SUBQUERY_NODES = (exp.Subquery, exp.Exists, exp.All, exp.Any)


class PythonExecutor:
    def __init__(self, env=None, tables=None):
        self.generator = Python().generator(identify=True, comments=False)
        self.env = {**ENV, **(env or {})}
        self.tables = tables or {}
        self._subquery_plans = {}
        self._plan_names_by_sql = {}
        self._ctes = None
        self._outer_scope = None
        self.env.update(
            SUBQUERY_COMPARISON=self._subquery_comparison,
            SUBQUERY_EXISTS=self._subquery_exists,
            SUBQUERY_SCALAR=self._subquery_scalar,
        )

    def execute(self, plan, outer_scope=None):
        ctes, scope = self._ctes, self._outer_scope
        self._ctes, self._outer_scope = plan.ctes, outer_scope
        try:
            return self._execute(plan)
        finally:
            self._ctes, self._outer_scope = ctes, scope

    def _execute(self, plan):
        finished = set()
        queue = set(plan.leaves)
        contexts = {}

        while queue:
            node = queue.pop()
            try:
                context = self.context(
                    {
                        name: table
                        for dep in node.dependencies
                        for name, table in contexts[dep].tables.items()
                    }
                )

                if isinstance(node, planner.Scan):
                    contexts[node] = self.scan(node, context)
                elif isinstance(node, planner.Aggregate):
                    contexts[node] = self.aggregate(node, context)
                elif isinstance(node, planner.Join):
                    contexts[node] = self.join(node, context)
                elif isinstance(node, planner.Window):
                    contexts[node] = self.window(node, context)
                elif isinstance(node, planner.Sort):
                    contexts[node] = self.sort(node, context)
                elif isinstance(node, planner.SetOperation):
                    contexts[node] = self.set_operation(node, context)
                else:
                    raise NotImplementedError

                if node.offset:
                    table = contexts[node].tables[node.name]
                    table.rows = table.rows[node.offset :]

                finished.add(node)

                for dep in node.dependents:
                    if all(d in contexts for d in dep.dependencies):
                        queue.add(dep)

                for dep in node.dependencies:
                    if all(d in finished for d in dep.dependents):
                        contexts.pop(dep)
            except Exception as e:
                raise ExecuteError(f"Step '{node.id}' failed: {e}") from e

        root = plan.root
        return contexts[root].tables[root.name]

    def generate(self, expression):
        """Convert a SQL expression into literal Python code and compile it into bytecode."""
        if not expression:
            return None

        expression = self._replace_subqueries(expression)
        sql = self.generator.generate(expression)
        return compile(sql, sql, "eval", optimize=2)

    def _replace_subqueries(self, expression):
        if not expression.find(*SUBQUERY_NODES):
            return expression

        expression = expression.copy()

        while True:
            subquery = expression.find(*SUBQUERY_NODES)

            if subquery is None:
                return expression

            target, replacement = self._compile_subquery(subquery)

            if target is expression:
                expression = replacement
            else:
                target.replace(replacement)

    def _compile_subquery(self, subquery):
        query = subquery.this.unnest()
        scope = build_scope(query)

        outer_columns = list(scope.external_columns if scope else [])

        plan = self._register_subquery(query)
        parent = subquery.parent

        if isinstance(subquery, exp.Exists):
            return subquery, exp.func(
                "SUBQUERY_EXISTS", plan, exp.var("scope"), *outer_columns, copy=False
            )

        if len(query.selects) != 1:
            raise ExecuteError(
                f"Subquery used as an expression returned {len(query.selects)} columns"
            )

        if isinstance(subquery, (exp.All, exp.Any)):
            return self._compile_quantified(parent, subquery.key.upper(), plan, outer_columns)

        if isinstance(parent, exp.In) and subquery is parent.args.get("query"):
            return self._compile_quantified(parent, "ANY", plan, outer_columns, op="EQ")

        return subquery, exp.func(
            "SUBQUERY_SCALAR", plan, exp.var("scope"), *outer_columns, copy=False
        )

    def _compile_quantified(self, comparison, quantifier, plan, outer_columns, op=None):
        if not isinstance(comparison, (exp.Binary, exp.In)):
            raise ExecuteError(f"Unsupported {quantifier} subquery: expected a comparison")

        return comparison, exp.func(
            "SUBQUERY_COMPARISON",
            comparison.this,
            plan,
            exp.var("scope"),
            exp.Literal.string(op or comparison.key.upper()),
            exp.Literal.string(quantifier),
            *outer_columns,
            copy=False,
        )

    def _register_subquery(self, query):
        if self._ctes is not None and not query.args.get("with_"):
            query.set("with_", self._ctes)

        sql = query.sql()
        name = self._plan_names_by_sql.get(sql)

        if name is None:
            name = self._plan_names_by_sql[sql] = f"_sq_{len(self._subquery_plans)}"
            self._subquery_plans[name] = (planner.Plan(query), {})

        return exp.Literal.string(name)

    def _subquery_table(self, plan_name, scope, args):
        plan, cache = self._subquery_plans[plan_name]

        try:
            return cache[args]
        except KeyError:
            pass
        except TypeError:  # an unhashable correlated value can't be memoized
            cache = None

        table = self.execute(plan, scope)

        if cache is not None:
            cache[args] = table

        return table

    def _subquery_exists(self, plan_name, scope, *args):
        return bool(self._subquery_table(plan_name, scope, args).rows)

    def _subquery_scalar(self, plan_name, scope, *args):
        rows = self._subquery_table(plan_name, scope, args).rows

        if len(rows) > 1:
            raise ExecuteError("More than one row returned by a subquery used as an expression")

        return rows[0][0] if rows else None

    def _subquery_comparison(self, value, plan_name, scope, op, quantifier, *args):
        compare = self.env[op]
        is_any = quantifier == "ANY"
        saw_null = False

        for row in self._subquery_table(plan_name, scope, args).rows:
            result = compare(value, row[0])

            if result is None:
                saw_null = True
            elif bool(result) is is_any:
                return is_any

        return None if saw_null else not is_any

    def generate_tuple(self, expressions):
        """Convert an array of SQL expressions into tuple of Python byte code."""
        if not expressions:
            return tuple()
        return tuple(self.generate(expression) for expression in expressions)

    def context(self, tables):
        return Context(tables, env=self.env, outer=self._outer_scope)

    def table(self, expressions):
        return Table(
            expression.alias_or_name if isinstance(expression, exp.Expr) else expression
            for expression in expressions
        )

    def scan(self, step, context):
        source = step.source

        if source and isinstance(source, exp.Expr):
            source = source.name or source.alias

        if source is None:
            context, table_iter = self.static()
        elif source in context:
            if not step.projections and not step.condition:
                return self.context({step.name: context.tables[source]})
            table_iter = context.table_iter(source)
        else:
            context, table_iter = self.scan_table(step)

        return self.context({step.name: self._project_and_filter(context, step, table_iter)})

    def _project_and_filter(self, context, step, table_iter):
        sink = self.table(step.projections if step.projections else context.columns)
        condition = self.generate(step.condition)
        projections = self.generate_tuple(step.projections)

        for reader in table_iter:
            if len(sink) >= step.offset + step.limit:
                break

            if condition and not context.eval(condition):
                continue

            if projections:
                sink.append(context.eval_tuple(projections))
            else:
                sink.append(reader.row)

        return sink

    def static(self):
        return self.context({}), [RowReader(())]

    def scan_table(self, step):
        table = self.tables.find(step.source)
        context = self.context({step.source.alias_or_name: table})
        return context, iter(table)

    def join(self, step, context):
        source = step.source_name

        source_table = context.tables[source]
        source_context = self.context({source: source_table})
        column_ranges = {source: range(0, len(source_table.columns))}

        for name, join in step.joins.items():
            table = context.tables[name]
            start = max(r.stop for r in column_ranges.values())
            column_ranges[name] = range(start, len(table.columns) + start)
            join_context = self.context({name: table})
            condition = self.generate(join["condition"])
            condition_context = (
                self.context(
                    {
                        name: Table(
                            source_context.columns + join_context.columns,
                            column_range=column_range,
                        )
                        for name, column_range in column_ranges.items()
                    }
                )
                if condition
                else None
            )

            if join.get("source_key"):
                table = self.hash_join(
                    join, source_context, join_context, condition, condition_context
                )
            else:
                table = self.nested_loop_join(
                    join, source_context, join_context, condition, condition_context
                )

            source_context = self.context(
                {
                    name: Table(table.columns, table.rows, column_range)
                    for name, column_range in column_ranges.items()
                }
            )
        if not step.condition and not step.projections:
            return source_context

        sink = self._project_and_filter(
            source_context,
            step,
            (reader for reader, _ in iter(source_context)),
        )

        if step.projections:
            return self.context({step.name: sink})
        else:
            return self.context(
                {
                    name: Table(table.columns, sink.rows, table.column_range)
                    for name, table in source_context.tables.items()
                }
            )

    @staticmethod
    def _join_matches(row, condition, condition_context):
        if not condition:
            return True

        condition_context.set_row(row)
        return condition_context.eval(condition) is True

    def nested_loop_join(self, join, source_context, join_context, condition, condition_context):
        table = Table(source_context.columns + join_context.columns)
        source_rows = source_context.table.rows
        join_rows = join_context.table.rows
        matched_source = set()
        matched_join = set()

        for source_index, source_row in enumerate(source_rows):
            for join_index, join_row in enumerate(join_rows):
                row = source_row + join_row
                if self._join_matches(row, condition, condition_context):
                    table.append(row)
                    matched_source.add(source_index)
                    matched_join.add(join_index)

        self._append_unmatched_join_rows(
            table, join, source_rows, join_rows, matched_source, matched_join
        )

        return table

    def hash_join(self, join, source_context, join_context, condition, condition_context):
        source_key = self.generate_tuple(join["source_key"])
        join_key = self.generate_tuple(join["join_key"])
        results = collections.defaultdict(lambda: ([], []))

        for index, (reader, ctx) in enumerate(source_context):
            key = ctx.eval_tuple(source_key)
            if all(value is not None for value in key):
                results[key][0].append((index, reader.row))
        for index, (reader, ctx) in enumerate(join_context):
            key = ctx.eval_tuple(join_key)
            if all(value is not None for value in key):
                results[key][1].append((index, reader.row))

        table = Table(source_context.columns + join_context.columns)
        matched_source = set()
        matched_join = set()

        for source_group, join_group in results.values():
            for (source_index, source_row), (join_index, join_row) in itertools.product(
                source_group, join_group
            ):
                row = source_row + join_row
                if self._join_matches(row, condition, condition_context):
                    table.append(row)
                    matched_source.add(source_index)
                    matched_join.add(join_index)

        self._append_unmatched_join_rows(
            table,
            join,
            source_context.table.rows,
            join_context.table.rows,
            matched_source,
            matched_join,
        )

        return table

    @staticmethod
    def _append_unmatched_join_rows(
        table, join, source_rows, join_rows, matched_source, matched_join
    ):
        side = join.get("side")
        if side in ("LEFT", "FULL"):
            join_nulls = (None,) * (len(table.columns) - len(source_rows[0]) if source_rows else 0)
            for index, row in enumerate(source_rows):
                if index not in matched_source:
                    table.append(row + join_nulls)

        if side in ("RIGHT", "FULL"):
            source_width = len(table.columns) - (len(join_rows[0]) if join_rows else 0)
            source_nulls = (None,) * source_width
            for index, row in enumerate(join_rows):
                if index not in matched_join:
                    table.append(source_nulls + row)

    def aggregate(self, step, context):
        group_by = self.generate_tuple(step.group.values())
        aggregations = self.generate_tuple(step.aggregations)
        operands = self.generate_tuple(step.operands)

        if operands:
            operand_columns = self.table(step.operands).columns
            operand_rows = [ctx.eval_tuple(operands) for _, ctx in context]
            width = len(context.columns)
            columns = context.columns + operand_columns
            rows = [a + b for a, b in zip(context.table.rows, operand_rows)]

            def extend(table):
                column_range = table.column_range
                return Table(
                    columns,
                    rows,
                    range(column_range.start, column_range.stop + len(operand_columns))
                    if column_range
                    else None,
                )

            context = self.context(
                {
                    None: Table(columns, rows, range(width, width + len(operand_columns))),
                    **{name: extend(table) for name, table in context.tables.items()},
                }
            )

        context.sort(group_by)

        group = None
        start = 0
        end = 1
        length = len(context.table)
        table = self.table(list(step.group) + step.aggregations)

        def add_row():
            table.append(group + context.eval_tuple(aggregations))

        if length:
            for i in range(length):
                context.set_index(i)
                key = context.eval_tuple(group_by)
                group = key if group is None else group
                end += 1
                if key != group:
                    context.set_range(start, end - 2)
                    add_row()
                    group = key
                    start = end - 2
                if not step.condition and len(table.rows) >= step.offset + step.limit:
                    break
                if i == length - 1:
                    context.set_range(start, end - 1)
                    add_row()
        elif step.limit > 0 and not group_by:
            context.set_range(0, 0)
            table.append(context.eval_tuple(aggregations))

        context = self.context({step.name: table, **{name: table for name in context.tables}})

        if step.projections or step.condition:
            return self.context(
                {step.name: self._project_and_filter(context, step, context.table_iter(step.name))}
            )
        return context

    def window(self, step, context):
        table = context.table
        rows = table.rows
        index = {column: i for i, column in enumerate(table.columns)}
        layouts = {}
        results = []

        for function in step.functions:
            key = (function.partition, function.order)

            if key not in layouts:
                layouts[key] = self._window_partitions(rows, index, *key)

            results.append(self._window_function(function, rows, index, layouts[key]))

        return self.context(
            {
                step.name: Table(
                    table.columns + tuple(function.name for function in step.functions),
                    [row + tuple(result[i] for result in results) for i, row in enumerate(rows)],
                )
            }
        )

    def _window_partitions(self, rows, index, partition, order):
        ordered = self.env["ORDERED"]
        partition_indices = [index[column] for column in partition]
        order_indices = [(index[column], desc, nulls_first) for column, desc, nulls_first in order]
        groups = {}

        for i, row in enumerate(rows):
            groups.setdefault(tuple(row[c] for c in partition_indices), []).append(i)

        def sort_key(i):
            return tuple(
                ordered(rows[i][c], desc, nulls_first) for c, desc, nulls_first in order_indices
            )

        partitions = []

        for members in groups.values():
            if order_indices:
                members.sort(key=sort_key)

            peers = [tuple(rows[i][c] for c, _, _ in order_indices) for i in members]
            keys = [rows[i][order_indices[0][0]] for i in members] if order_indices else []
            partitions.append(
                WindowPartition(members, peers, keys, order[0][1] if order else False)
            )

        return partitions

    def _window_function(self, function, rows, index, partitions):
        kind = function.kind
        result = [None] * len(rows)
        arg = [row[index[function.arg]] for row in rows] if function.arg else None
        default = [row[index[function.default]] for row in rows] if function.default else None
        mask = [row[index[function.filter]] for row in rows] if function.filter else None
        frame = function.frame
        aggregate = None

        if kind == "AGG":
            aggregate = self.env.get(function.func)

            if aggregate is None:
                raise ExecuteError(f"Unsupported window function {function.func}")

        for part in partitions:
            members = part.rows
            n = len(members)
            cache = {}

            if kind == "ROWNUMBER":
                for pos, i in enumerate(members):
                    result[i] = pos + 1
            elif kind == "RANK":
                for pos, i in enumerate(members):
                    result[i] = part.peer_start[pos] + 1
            elif kind == "DENSERANK":
                for pos, i in enumerate(members):
                    result[i] = part.group[pos] + 1
            elif kind == "PERCENTRANK":
                for pos, i in enumerate(members):
                    result[i] = part.peer_start[pos] / (n - 1) if n > 1 else 0.0
            elif kind == "CUMEDIST":
                for pos, i in enumerate(members):
                    result[i] = (part.peer_end[pos] + 1) / n
            elif kind == "NTILE":
                buckets = function.offset
                size, extra = divmod(n, buckets)
                big = extra * (size + 1)

                for pos, i in enumerate(members):
                    result[i] = (
                        pos // (size + 1) + 1 if pos < big else extra + (pos - big) // size + 1
                    )
            elif kind in ("LAG", "LEAD"):
                shift = -function.offset if kind == "LAG" else function.offset

                for pos, i in enumerate(members):
                    target = pos + shift

                    if 0 <= target < n:
                        result[i] = arg[members[target]]
                    elif default is not None:
                        result[i] = default[i]
            else:
                range_keys = part.range_keys() if frame.has_range_offset else None

                for pos, i in enumerate(members):
                    lo, hi = self._frame_range(part, frame, pos, range_keys)
                    positions = self._frame_positions(part, frame, pos, lo, hi)

                    if kind == "AGG":
                        cache_key = (lo, hi) if frame.exclude is None else None

                        if cache_key in cache:
                            result[i] = cache[cache_key]
                            continue

                        values = [
                            arg[members[q]] for q in positions if mask is None or mask[members[q]]
                        ]
                        result[i] = aggregate(values)

                        if cache_key is not None:
                            cache[cache_key] = result[i]
                    elif kind == "FIRSTVALUE":
                        result[i] = arg[members[positions[0]]] if positions else None
                    elif kind == "LASTVALUE":
                        result[i] = arg[members[positions[-1]]] if positions else None
                    elif kind == "NTHVALUE":
                        if len(positions) >= function.offset:
                            result[i] = arg[members[positions[function.offset - 1]]]
                    else:
                        raise ExecuteError(f"Unsupported window function {kind}")

        return result

    @staticmethod
    def _frame_range(part, frame, pos, range_keys):
        """Inclusive range of partition positions in the frame, before any exclusion."""
        n = len(part.rows)
        start, end = frame.start, frame.end
        so, eo = frame.start_offset, frame.end_offset

        def shift(kind, offset, origin):
            if kind == "PRECEDING":
                return origin - offset
            if kind == "FOLLOWING":
                return origin + offset
            return origin

        if frame.kind == "ROWS":
            lo = 0 if start == "UNBOUNDED_PRECEDING" else shift(start, so, pos)
            hi = n - 1 if end == "UNBOUNDED_FOLLOWING" else shift(end, eo, pos)
        elif frame.kind == "GROUPS":
            groups = len(part.group_start)
            group = part.group[pos]

            if start == "UNBOUNDED_PRECEDING":
                lo = 0
            else:
                target = max(shift(start, so, group), 0)
                lo = part.group_start[target] if target < groups else n

            if end == "UNBOUNDED_FOLLOWING":
                hi = n - 1
            else:
                target = min(shift(end, eo, group), groups - 1)
                hi = part.group_end[target] if target >= 0 else -1
        else:
            current = part.keys[pos] if range_keys else None
            numeric = _numeric(current)
            lo = (
                0
                if start == "UNBOUNDED_PRECEDING"
                else part.peer_start[pos]
                if start == "CURRENT" or not numeric
                else bisect.bisect_left(
                    range_keys.values,
                    range_keys.sign * _number(current) + (-so if start == "PRECEDING" else so),
                    range_keys.lo,
                    range_keys.hi,
                )
            )
            hi = (
                n - 1
                if end == "UNBOUNDED_FOLLOWING"
                else part.peer_end[pos]
                if end == "CURRENT" or not numeric
                else bisect.bisect_right(
                    range_keys.values,
                    range_keys.sign * _number(current) + (-eo if end == "PRECEDING" else eo),
                    range_keys.lo,
                    range_keys.hi,
                )
                - 1
            )

        return max(lo, 0), min(hi, n - 1)

    @staticmethod
    def _frame_positions(part, frame, pos, lo, hi):
        if lo > hi:
            return ()

        exclude = frame.exclude

        if exclude is None:
            return range(lo, hi + 1)

        first, last = part.peer_start[pos], part.peer_end[pos]

        if exclude == "CURRENT ROW":
            return [q for q in range(lo, hi + 1) if q != pos]
        if exclude == "GROUP":
            return [q for q in range(lo, hi + 1) if not first <= q <= last]

        return [q for q in range(lo, hi + 1) if q == pos or not first <= q <= last]

    def sort(self, step, context):
        projections = self.generate_tuple(step.projections)
        projection_columns = [p.alias_or_name for p in step.projections]
        all_columns = list(context.columns) + projection_columns
        sink = self.table(all_columns)
        for reader, ctx in context:
            sink.append(reader.row + ctx.eval_tuple(projections))

        sort_ctx = self.context(
            {
                None: sink,
                **{table: sink for table in context.tables},
            }
        )
        sort_ctx.sort(self.generate_tuple(step.key))

        if not math.isinf(step.limit):
            sort_ctx.table.rows = sort_ctx.table.rows[0 : step.offset + step.limit]

        rows = sort_ctx.table.rows

        if projection_columns:
            rows = [row[len(context.columns) : len(all_columns)] for row in rows]

        output = Table(projection_columns or context.columns, rows=rows)
        return self.context({step.name: output})

    def set_operation(self, step, context):
        left = context.tables[step.left]
        right = context.tables[step.right]

        sink = self.table(left.columns)

        if issubclass(step.op, exp.Intersect):
            right_counts = collections.Counter(right.rows)
            seen = set()
            for row in left.rows:
                if right_counts[row] and (not step.distinct or row not in seen):
                    sink.append(row)
                    seen.add(row)
                    if not step.distinct:
                        right_counts[row] -= 1
        elif issubclass(step.op, exp.Except):
            right_counts = collections.Counter(right.rows)
            seen = set()
            for row in left.rows:
                if right_counts[row] and not step.distinct:
                    right_counts[row] -= 1
                elif not right_counts[row] and (not step.distinct or row not in seen):
                    sink.append(row)
                    seen.add(row)
        elif issubclass(step.op, exp.Union) and step.distinct:
            sink.rows = list(set(left.rows).union(set(right.rows)))
        else:
            sink.rows = left.rows + right.rows

        if not math.isinf(step.limit):
            sink.rows = sink.rows[0 : step.offset + step.limit]

        return self.context({step.name: sink})


def _numeric(value):
    return isinstance(value, (int, float, decimal.Decimal))


def _number(value):
    return float(value) if isinstance(value, decimal.Decimal) else value


class RangeKeys:
    """Sign-adjusted, ascending keys of a partition, searched for RANGE offset boundaries."""

    def __init__(self, values, lo, hi, sign):
        self.values = values
        self.lo = lo
        self.hi = hi
        self.sign = sign


class WindowPartition:
    """The ordered rows of one window partition and its peer groups."""

    def __init__(self, rows, peers, keys, desc):
        self.rows = rows
        self.keys = keys
        self.peer_start = []
        self.peer_end = []
        self.group = []
        self.group_start = []
        self.group_end = []

        for pos, peer in enumerate(peers):
            if pos and peer == peers[pos - 1]:
                self.group_end[-1] = pos
            else:
                self.group_start.append(pos)
                self.group_end.append(pos)

            self.group.append(len(self.group_start) - 1)

        self.peer_start = [self.group_start[g] for g in self.group]
        self.peer_end = [self.group_end[g] for g in self.group]
        self.desc = desc
        self._range_keys = None

    def range_keys(self):
        if self._range_keys is None:
            n = len(self.rows)
            lo = 0

            while lo < n and self.keys[lo] is None:
                lo += 1

            hi = n

            while hi > lo and self.keys[hi - 1] is None:
                hi -= 1

            sign = -1 if self.desc else 1
            values = [sign * _number(key) if _numeric(key) else 0 for key in self.keys]
            self._range_keys = RangeKeys(values, lo, hi, sign)

        return self._range_keys


class Python(Dialect):
    class Tokenizer(tokens.Tokenizer):
        STRING_ESCAPES = ["\\"]

    Generator = PythonGenerator
