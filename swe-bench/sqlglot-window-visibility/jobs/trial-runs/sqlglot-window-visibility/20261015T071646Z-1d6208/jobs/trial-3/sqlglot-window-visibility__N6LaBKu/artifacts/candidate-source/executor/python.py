import collections
import itertools
import math
from bisect import bisect_left

from sqlglot import exp, planner, tokens
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import ExecuteError
from sqlglot.executor.context import Context
from sqlglot.executor.env import ENV
from sqlglot.executor.table import RowReader, Table
from sqlglot.executor.window import (
    CURRENT,
    GROUPS,
    OFFSET,
    RANGE,
    ROWS,
    UNBOUNDED,
    Bound,
    Frame,
    Partition,
    frame_ranges,
    frame_range,
    is_number,
    ntile_bucket,
)
from sqlglot.generators.python import PythonGenerator
from sqlglot.optimizer.scope import build_scope

SUBQUERY_NODES = (exp.Subquery, exp.Exists, exp.All, exp.Any)


def _integer(value, what, minimum):
    if value is None:
        return None

    if not is_number(value) or not math.isfinite(value) or value != int(value):
        raise ExecuteError(f"{what} must be an integer, got {value!r}")

    if value < minimum:
        raise ExecuteError(f"{what} must be at least {minimum}, got {value!r}")

    return int(value)


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
            operand_table = Table(self.table(step.operands).columns)

            for reader, ctx in context:
                operand_table.append(ctx.eval_tuple(operands))

            for i, (a, b) in enumerate(zip(context.table.rows, operand_table.rows)):
                context.table.rows[i] = a + b

            width = len(context.columns)
            context.add_columns(*operand_table.columns)

            operand_table = Table(
                context.columns,
                context.table.rows,
                range(width, width + len(operand_table.columns)),
            )

            context = self.context(
                {
                    None: operand_table,
                    **context.tables,
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
        size = len(table.rows)
        names = tuple(step.windows)
        results = [self._window_column(window, context, size) for window in step.windows.values()]

        columns = table.columns + names
        rows = [row + tuple(result[i] for result in results) for i, row in enumerate(table.rows)]
        width = len(table.columns)

        tables = {
            name: Table(columns, rows, source.column_range)
            for name, source in context.tables.items()
        }
        tables[step.table] = Table(columns, rows, range(width, width + len(names)))
        context = self.context(tables)

        if not step.condition and not step.projections:
            return context

        sink = self._project_and_filter(context, step, (reader for reader, _ in context))

        if step.projections:
            return self.context({step.name: sink})

        return self.context(
            {
                name: Table(source.columns, sink.rows, source.column_range)
                for name, source in context.tables.items()
            }
        )

    def _values(self, context, expression):
        code = self.generate(expression)
        return [ctx.eval(code) for _, ctx in context]

    def _constant(self, expression):
        return self.context({}).eval(self.generate(expression))

    def _integers(self, context, expression, size, what, minimum):
        """The value of an integer argument for every row, validated even if there are no rows."""
        if expression.find(exp.Column):
            values = self._values(context, expression)
        else:
            values = [self._constant(expression)] * size
            _integer(values[0] if values else self._constant(expression), what, minimum)

        return [_integer(value, what, minimum) for value in values]

    def _window_frame(self, spec, order_terms):
        if spec is None:
            return Frame()

        def text(value):
            return (value.name if isinstance(value, exp.Expr) else str(value or "")).upper()

        def bound(value, side):
            if value is None:
                return None

            if isinstance(value, exp.Expr) and not isinstance(value, exp.Var):
                if isinstance(value, exp.Interval):
                    raise ExecuteError("Interval window frame offsets are not supported")
                if value.find(exp.Column):
                    raise ExecuteError("Window frame offsets must be constant")
                return Bound(OFFSET, text(side) == "FOLLOWING", self._constant(value))

            name = text(value)

            if name == "UNBOUNDED":
                return Bound(UNBOUNDED, text(side) == "FOLLOWING")
            if name == "CURRENT ROW":
                return Bound(CURRENT)

            raise ExecuteError(f"Unsupported window frame bound: {name}")

        kind = text(spec.args.get("kind")) or RANGE
        start = bound(spec.args.get("start"), spec.args.get("start_side"))
        end = bound(spec.args.get("end"), spec.args.get("end_side"))

        if kind not in (ROWS, RANGE, GROUPS):
            raise ExecuteError(f"Unsupported window frame type: {kind}")

        for edge in (start, end):
            if edge is not None and edge.kind == OFFSET:
                if kind == RANGE:
                    if not is_number(edge.offset) or not math.isfinite(edge.offset):
                        raise ExecuteError("RANGE frame offsets must be finite numbers")
                    if edge.offset < 0:
                        raise ExecuteError("Window frame offsets must not be negative")
                else:
                    edge.offset = _integer(edge.offset, f"{kind} frame offset", 0)
                    if edge.offset is None:
                        raise ExecuteError("Window frame offsets must not be NULL")

        if kind == RANGE and OFFSET in (getattr(start, "kind", None), getattr(end, "kind", None)):
            if order_terms != 1:
                raise ExecuteError(
                    "A RANGE frame with an offset requires exactly one ORDER BY term"
                )

        exclude = text(spec.args.get("exclude")) or None

        if exclude not in (None, "CURRENT ROW", "GROUP", "TIES", "NO OTHERS"):
            raise ExecuteError(f"Unsupported window frame exclusion: {exclude}")

        return Frame(kind, start, end, exclude)

    def _window_column(self, window, context, size):
        function = window.this
        predicate = None

        while isinstance(function, (exp.Filter, exp.IgnoreNulls, exp.RespectNulls)):
            if isinstance(function, exp.IgnoreNulls):
                raise ExecuteError("IGNORE NULLS is not supported in window functions")

            if isinstance(function, exp.Filter):
                predicate = function.expression
                predicate = predicate.this if isinstance(predicate, exp.Where) else predicate

            function = function.this

        order = window.args.get("order")
        terms = order.expressions if order else []
        frame = self._window_frame(window.args.get("spec"), len(terms))

        special = (
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
        is_aggregate = not isinstance(function, special) and (
            isinstance(function, (exp.AggFunc, exp.Anonymous))
        )

        if predicate is not None and not is_aggregate:
            raise ExecuteError("FILTER is only supported for aggregate window functions")

        # validate and evaluate the arguments of the function itself
        keep = None
        values = None
        offsets = None
        defaults = None
        aggregate = None

        if is_aggregate:
            if isinstance(function, exp.Anonymous):
                name = function.name.upper()
                operand = function.expressions[0] if function.expressions else None
            else:
                name = function.key.upper()
                operand = function.this

            if isinstance(operand, exp.Distinct):
                raise ExecuteError("DISTINCT window aggregates are not supported")

            aggregate = self.env.get(name)

            if aggregate is None:
                raise ExecuteError(f"Unsupported window function: {name}")

            values = self._values(context, operand) if operand is not None else [1] * size

            if predicate is not None:
                keep = [
                    value is not None and bool(value) for value in self._values(context, predicate)
                ]
        elif isinstance(function, (exp.Lag, exp.Lead)):
            kind = function.key.upper()
            values = self._values(context, function.this)
            offset = function.args.get("offset")
            offsets = (
                self._integers(context, offset, size, f"{kind} offset", 0)
                if offset is not None
                else [1] * size
            )
            default = function.args.get("default")
            defaults = self._values(context, default) if default is not None else None
        elif isinstance(function, exp.NthValue):
            values = self._values(context, function.this)
            offsets = self._integers(context, function.args["offset"], size, "NTH_VALUE offset", 1)
        elif isinstance(function, (exp.FirstValue, exp.LastValue)):
            values = self._values(context, function.this)
        elif isinstance(function, exp.Ntile):
            buckets = function.this
            if buckets is None:
                raise ExecuteError("NTILE requires a bucket count")
            offsets = self._integers(context, buckets, size, "NTILE bucket count", 1)
        elif not isinstance(function, special):
            raise ExecuteError(f"Unsupported window function: {function.sql()}")

        # partition the relation and order each partition; windows never reorder the relation
        partition_columns = [
            self._values(context, e) for e in window.args.get("partition_by") or []
        ]
        order_columns = [self._values(context, term) for term in terms]
        order_keys = list(zip(*order_columns)) if order_columns else None
        groups = {}

        for index, key in enumerate(zip(*partition_columns) if partition_columns else [()] * size):
            groups.setdefault(key, []).append(index)

        range_keys = None
        desc = nulls_first = False

        if frame.kind == RANGE and frame.has_offset:
            range_keys = self._values(context, terms[0].this)
            desc = bool(terms[0].args.get("desc"))
            nulls_first = bool(terms[0].args.get("nulls_first"))

        result = [None] * size

        for rows in groups.values():
            if order_keys is not None:
                rows = sorted(rows, key=order_keys.__getitem__)

            partition = Partition(rows, order_keys)
            rows = partition.rows
            count = partition.size

            if isinstance(function, exp.RowNumber):
                for position, row in enumerate(rows):
                    result[row] = position + 1
            elif isinstance(function, (exp.Rank, exp.PercentRank)):
                for position, row in enumerate(rows):
                    rank = partition.starts[partition.group[position]] + 1
                    if isinstance(function, exp.Rank):
                        result[row] = rank
                    else:
                        result[row] = (rank - 1) / (count - 1) if count > 1 else 0.0
            elif isinstance(function, exp.DenseRank):
                for position, row in enumerate(rows):
                    result[row] = partition.group[position] + 1
            elif isinstance(function, exp.CumeDist):
                for position, row in enumerate(rows):
                    result[row] = partition.ends[partition.group[position]] / count
            elif isinstance(function, exp.Ntile):
                for position, row in enumerate(rows):
                    if offsets[row] is not None:
                        result[row] = ntile_bucket(offsets[row], count, position)
            elif isinstance(function, (exp.Lag, exp.Lead)):
                sign = -1 if isinstance(function, exp.Lag) else 1

                for position, row in enumerate(rows):
                    if offsets[row] is None:
                        continue

                    target = position + sign * offsets[row]

                    if 0 <= target < count:
                        result[row] = values[rows[target]]
                    elif defaults is not None:
                        result[row] = defaults[row]
            else:
                cache = {}
                cacheable = aggregate is not None and frame.exclude in (None, "NO OTHERS")
                ordered_values = [values[row] for row in rows]

                if keep is not None:
                    kept = [p for p in range(count) if keep[rows[p]]]
                    ordered_values = [ordered_values[p] for p in kept]
                else:
                    kept = None

                for position, row in enumerate(rows):
                    span = frame_range(frame, partition, position, range_keys, desc, nulls_first)

                    if cacheable and span in cache:
                        result[row] = cache[span]
                        continue

                    runs = frame_ranges(frame, partition, position, span)

                    if aggregate is not None:
                        frame_values = []

                        for start, end in runs:
                            if kept is not None:
                                start, end = bisect_left(kept, start), bisect_left(kept, end)
                            frame_values.extend(ordered_values[start:end])

                        result[row] = aggregate(frame_values)

                        if cacheable:
                            cache[span] = result[row]
                    elif runs:
                        if isinstance(function, exp.NthValue):
                            remaining = offsets[row]

                            if remaining is None:
                                continue

                            remaining -= 1

                            for start, end in runs:
                                if remaining < end - start:
                                    result[row] = values[rows[start + remaining]]
                                    break
                                remaining -= end - start
                        elif isinstance(function, exp.LastValue):
                            result[row] = values[rows[runs[-1][1] - 1]]
                        else:
                            result[row] = values[rows[runs[0][0]]]

        return result

    def sort(self, step, context):
        projections = self.generate_tuple(step.projections)
        projection_columns = [p.alias_or_name for p in step.projections]
        all_columns = list(context.columns) + projection_columns
        sink = self.table(all_columns)
        for reader, ctx in context:
            sink.append(reader.row + ctx.eval_tuple(projections))

        source_columns = range(len(context.columns))
        sort_tables = {None: sink}

        for name, table in context.tables.items():
            # a source column takes precedence over a projection of the same name, but a
            # projection can still be referenced through the table when no such column exists
            sort_table = Table(sink.columns, sink.rows)
            for i in table.column_range or source_columns:
                sort_table.reader.columns[sink.columns[i]] = i
            sort_tables[name] = sort_table

        sort_ctx = self.context(sort_tables)
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


class Python(Dialect):
    class Tokenizer(tokens.Tokenizer):
        STRING_ESCAPES = ["\\"]

    Generator = PythonGenerator
