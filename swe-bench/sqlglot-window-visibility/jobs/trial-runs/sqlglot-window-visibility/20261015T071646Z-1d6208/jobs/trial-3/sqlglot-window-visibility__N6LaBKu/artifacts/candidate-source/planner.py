from __future__ import annotations

import math
import typing as t

from sqlglot import alias, exp
from sqlglot.errors import ErrorLevel, ExecuteError
from sqlglot.helper import name_sequence
from sqlglot.optimizer.eliminate_joins import join_condition
from sqlglot.optimizer.scope import find_all_in_scope, find_in_scope
from collections.abc import Iterator, Sequence, Iterable


def _window_table_name(expression: exp.Expr) -> str:
    taken = {identifier.name for identifier in expression.find_all(exp.Identifier)}
    name = "_w"

    while name in taken:
        name += "_"

    return name


def _window_clauses(
    window: exp.Window, named: dict[str, exp.Window], seen: tuple[str, ...] = ()
) -> tuple[list[exp.Expr], exp.Expr | None, exp.Expr | None]:
    partition = [e.copy() for e in window.args.get("partition_by") or []]
    order = window.args.get("order")
    spec = window.args.get("spec")
    order = order.copy() if order is not None else None
    spec = spec.copy() if spec is not None else None
    base = window.args.get("alias")

    if base is not None:
        name = base.name
        definition = named.get(name)

        if definition is None:
            definition = next((w for n, w in named.items() if n.lower() == name.lower()), None)

        if definition is None:
            raise ExecuteError(f"Window '{name}' is not defined")

        if name.lower() in seen:
            raise ExecuteError(f"Window '{name}' is defined in terms of itself")

        base_partition, base_order, base_spec = _window_clauses(
            definition, named, (*seen, name.lower())
        )

        if partition and base_partition:
            raise ExecuteError(f"Cannot override the PARTITION BY of window '{name}'")
        if order is not None and base_order is not None:
            raise ExecuteError(f"Cannot override the ORDER BY of window '{name}'")
        if spec is not None and base_spec is not None:
            raise ExecuteError(f"Cannot override the frame of window '{name}'")

        partition = partition or base_partition
        order = order if order is not None else base_order
        spec = spec if spec is not None else base_spec

    return partition, order, spec


def _resolve_window(window: exp.Window, named: dict[str, exp.Window]) -> exp.Window:
    """Folds a named or inherited window reference into one self-contained window."""
    partition, order, spec = _window_clauses(window, named)

    return exp.Window(
        this=window.this.copy(),
        partition_by=partition or None,
        order=order,
        spec=spec,
    )


def _window_parts(window: exp.Window) -> list[exp.Expr]:
    """The sub-expressions of a resolved window which are evaluated for each row."""
    function = window.this
    parts: list[exp.Expr] = []

    while isinstance(function, (exp.Filter, exp.IgnoreNulls, exp.RespectNulls)):
        if isinstance(function, exp.Filter):
            parts.append(function.expression)
        function = function.this

    for value in function.args.values():
        parts.extend(value if isinstance(value, list) else [value])

    parts.extend(window.args.get("partition_by") or [])

    order = window.args.get("order")
    if order is not None:
        parts.extend(order.expressions)

    return [part for part in parts if isinstance(part, exp.Expr)]


class Plan:
    def __init__(self, expression: exp.Expr) -> None:
        self.expression: exp.Expr = expression.copy()
        with_: exp.With | None = self.expression.args.get("with_")
        self.ctes: exp.With | None = with_.copy() if with_ is not None else None
        self.root: Step = Step.from_expression(self.expression)
        self._dag: dict[Step, set[Step]] = {}

    @property
    def dag(self) -> dict[Step, set[Step]]:
        if not self._dag:
            dag: dict[Step, set[Step]] = {}
            nodes = {self.root}

            while nodes:
                node = nodes.pop()
                dag[node] = set()

                for dep in node.dependencies:
                    dag[node].add(dep)
                    nodes.add(dep)

            self._dag = dag

        return self._dag

    @property
    def leaves(self) -> Iterator[Step]:
        return (node for node, deps in self.dag.items() if not deps)

    def __repr__(self) -> str:
        return f"Plan\n----\n{repr(self.root)}"


class Step:
    @classmethod
    def from_expression(cls, expression: exp.Expr, ctes: dict[str, Step] | None = None) -> Step:
        """
        Builds a DAG of Steps from a SQL expression so that it's easier to execute in an engine.
        Note: the expression's tables and subqueries must be aliased for this method to work. For
        example, given the following expression:

        SELECT
          x.a,
          SUM(x.b)
        FROM x AS x
        JOIN y AS y
          ON x.a = y.a
        GROUP BY x.a

        the following DAG is produced (the expression IDs might differ per execution):

        - Aggregate: x (4347984624)
            Context:
              Aggregations:
                - SUM(x.b)
              Group:
                - x.a
            Projections:
              - x.a
              - "x".""
            Dependencies:
            - Join: x (4347985296)
              Context:
                y:
                On: x.a = y.a
              Projections:
              Dependencies:
              - Scan: x (4347983136)
                Context:
                  Source: x AS x
                Projections:
              - Scan: y (4343416624)
                Context:
                  Source: y AS y
                Projections:

        Args:
            expression: the expression to build the DAG from.
            ctes: a dictionary that maps CTEs to their corresponding Step DAG by name.

        Returns:
            A Step DAG corresponding to `expression`.
        """
        ctes = ctes or {}
        expression = expression.unnest()
        with_: exp.With | None = expression.args.get("with_")

        # CTEs break the mold of scope and introduce themselves to all in the context.
        if with_ is not None:
            ctes = ctes.copy()
            for cte in with_.expressions:
                step = Step.from_expression(cte.this, ctes)
                step.name = cte.alias
                ctes[step.name] = step  # type: ignore

        from_ = expression.args.get("from_")

        if isinstance(expression, exp.Select) and from_:
            step = Scan.from_expression(from_.this, ctes)
        elif isinstance(expression, exp.SetOperation):
            step = SetOperation.from_expression(expression, ctes)
        else:
            step = Scan()

        joins: list[exp.Join] | None = expression.args.get("joins")

        if joins is not None:
            join = Join.from_joins(joins, ctes)
            join.name = step.name
            join.source_name = step.name
            join.add_dependency(step)
            step = join

        # final selects in this chain of steps representing a select
        projections: list[exp.Expr] = []
        # intermediate computations of agg funcs eg x + 1 in SUM(x + 1)
        operands: dict[exp.Expr, str] = {}
        aggregations: dict[exp.Expr, None] = {}
        next_operand_name = name_sequence("_a_")

        def extract_agg_operands(expression: exp.Expr) -> bool:
            agg_funcs = tuple(find_all_in_scope(expression, exp.AggFunc))
            if agg_funcs:
                aggregations[expression] = None

            for agg in agg_funcs:
                for operand in agg.unnest_operands():
                    targets = (
                        operand.expressions if isinstance(operand, exp.Distinct) else [operand]
                    )

                    for target in targets:
                        if isinstance(target, exp.Column):
                            continue
                        if target not in operands:
                            operands[target] = next_operand_name()

                        target.replace(exp.column(operands[target], quoted=True))

            return bool(agg_funcs)

        def set_ops_and_aggs(step) -> None:
            step.operands = tuple(alias(operand, alias_) for operand, alias_ in operands.items())
            step.aggregations = list(aggregations)

        qualify: exp.Qualify | None = expression.args.get("qualify")
        order: exp.Order | None = expression.args.get("order")
        windowed = [find_in_scope(e, exp.Window) is not None for e in expression.expressions]
        has_windows = (
            any(windowed)
            or qualify is not None
            or (order is not None and find_in_scope(order, exp.Window) is not None)
        )

        # ORDER BY and QUALIFY may refer to projections by alias, even from within a window
        projection_aliases = (
            {
                e.alias: e.unalias().copy()
                for e, is_windowed in zip(expression.expressions, windowed)
                if e.alias and not is_windowed
            }
            if has_windows
            else {}
        )

        for e, is_windowed in zip(expression.expressions, windowed):
            if is_windowed:
                projections.append(e)
            elif find_in_scope(e, exp.AggFunc):
                projections.append(exp.column(e.alias_or_name, step.name, quoted=True))
                extract_agg_operands(e)
            else:
                projections.append(e)

        where: exp.Where | None = expression.args.get("where")

        if where is not None:
            step.condition = where.this

        windows: dict[str, tuple[str, exp.Window]] = {}
        window_table = ""
        window_hosts: list[exp.Expr] = []

        if has_windows:
            window_table = _window_table_name(expression)
            named_windows = {
                w.name: w for w in expression.args.get("windows") or [] if isinstance(w, exp.Window)
            }
            next_agg_name = name_sequence("_agg_")
            extracted_aggs: dict[str, str] = {}

            def extract_window_aggs(host: exp.Expr) -> None:
                # Aggregates evaluated outside of a window run with the grouping, ahead of it
                for agg in list(find_all_in_scope(host, exp.AggFunc)):
                    if agg.parent is None:
                        continue

                    key = repr(agg)
                    name = extracted_aggs.get(key)

                    if name is None:
                        name = extracted_aggs[key] = next_agg_name()
                        extract_agg_operands(exp.alias_(agg.copy(), name, quoted=True))

                    agg.replace(exp.column(name, step.name, quoted=True))

            def extract_windows(host: exp.Expr, expand_aliases: bool = False) -> exp.Expr:
                for window in list(find_all_in_scope(host, exp.Window)):
                    resolved = _resolve_window(window, named_windows)

                    if expand_aliases:
                        for column in list(resolved.find_all(exp.Column)):
                            if not column.table and column.name in projection_aliases:
                                column.replace(projection_aliases[column.name].copy())

                    key = repr(resolved)

                    if key not in windows:
                        windows[key] = (f"_w_{len(windows)}", resolved)

                    ref = exp.column(windows[key][0], window_table, quoted=True)

                    if window is host:
                        host = ref
                    else:
                        window.replace(ref)

                extract_window_aggs(host)
                return host

            projections = [
                extract_windows(e) if is_windowed else e
                for e, is_windowed in zip(projections, windowed)
            ]

            if qualify is not None:
                extract_windows(qualify, expand_aliases=True)
                window_hosts.append(qualify)

            if order is not None:
                extract_windows(order, expand_aliases=True)
                window_hosts.append(order)

            for _, resolved in windows.values():
                for part in _window_parts(resolved):
                    extract_window_aggs(part)

                window_hosts.append(resolved)

        group: exp.Group | None = expression.args.get("group")

        if group is not None or aggregations:
            aggregate = Aggregate()
            aggregate.source = step.name
            aggregate.name = step.name

            having: exp.Having | None = expression.args.get("having")

            if having is not None:
                if extract_agg_operands(exp.alias_(having.this, "_h", quoted=True)):
                    aggregate.condition = exp.column("_h", step.name, quoted=True)
                else:
                    aggregate.condition = having.this

            set_ops_and_aggs(aggregate)

            # give aggregates names and replace projections with references to them
            aggregate.group = {
                f"_g{i}": e for i, e in enumerate(group.expressions if group else [])
            }

            intermediate: dict[str | exp.Expr, str] = {}
            for k, v in aggregate.group.items():
                intermediate[v] = k
                if isinstance(v, exp.Column):
                    intermediate[v.name] = k

            for projection in [*projections, *window_hosts]:
                for node in projection.walk():
                    name = intermediate.get(node)
                    if name:
                        node.replace(exp.column(name, step.name))

            if aggregate.condition:
                for node in aggregate.condition.walk():
                    name = intermediate.get(node) or intermediate.get(node.name)
                    if name:
                        node.replace(exp.column(name, step.name))

            aggregate.add_dependency(step)
            step = aggregate
        else:
            aggregate = None

        if window_table:
            window = Window()
            window.name = step.name
            window.table = window_table
            window.windows = {name: resolved for name, resolved in windows.values()}
            window.condition = qualify.this if qualify is not None else None
            window.add_dependency(step)
            step = window

        # Plan DISTINCT before ORDER BY, since Aggregate sorts by its own group key
        if isinstance(expression, exp.Select) and expression.args.get("distinct"):
            distinct = Aggregate()
            distinct.source = step.name
            distinct.name = step.name
            distinct.group = {
                e.alias_or_name: e.unalias() for e in projections or expression.expressions
            }
            projections = [exp.column(name, step.name, quoted=True) for name in distinct.group]
            distinct.add_dependency(step)
            step = distinct
        else:
            distinct = None

        if order is not None:
            if aggregate is not None:
                for i, ordered in enumerate(order.expressions):
                    if extract_agg_operands(exp.alias_(ordered.this, f"_o_{i}", quoted=True)):
                        ordered.this.replace(exp.column(f"_o_{i}", aggregate.name, quoted=True))

                set_ops_and_aggs(aggregate)

            if distinct is not None:
                for i, ordered in enumerate(order.expressions):
                    key = ordered.this
                    group_name = next((n for n, e in distinct.group.items() if e == key), None)
                    if group_name:
                        key.replace(exp.column(group_name, step.name, quoted=True))
                        continue

                    # a bare column is a reference to an output name
                    if isinstance(key, exp.Column) and not key.table and key.name in distinct.group:
                        continue

                    key = key.copy()
                    for node in [
                        n
                        for n in key.walk()
                        if isinstance(n, exp.Column) and not n.table and n.name in distinct.group
                    ]:
                        node.replace(distinct.group[node.name].copy())

                    # the key has no single value once DISTINCT collapses rows; follow
                    # duckdb/sqlite and take an arbitrary one
                    if not isinstance(key, exp.Column):
                        distinct.operands += (alias(key, f"_a_{i}"),)
                        key = exp.column(f"_a_{i}", quoted=True)

                    distinct.aggregations.append(
                        exp.alias_(exp.First(this=key), f"_o_{i}", quoted=True)
                    )
                    ordered.this.replace(exp.column(f"_o_{i}", step.name, quoted=True))

            sort = Sort()
            sort.name = step.name
            sort.key = order.expressions
            sort.add_dependency(step)
            step = sort

        step.projections = projections

        limit: exp.Limit | None = expression.args.get("limit")

        if limit is not None and not limit.is_limit_all:
            step.limit = int(limit.text("expression"))

        offset: exp.Offset | None = expression.args.get("offset")

        if offset is not None:
            step.offset = int(offset.text("expression"))

        return step

    def __init__(self) -> None:
        self.name: str | None = None
        self.dependencies: set[Step] = set()
        self.dependents: set[Step] = set()
        self.projections: Sequence[exp.Expr] = []
        self.limit: float = math.inf
        self.offset: int = 0
        self.condition: exp.Expr | None = None

    def add_dependency(self, dependency: Step) -> None:
        self.dependencies.add(dependency)
        dependency.dependents.add(self)

    def __repr__(self) -> str:
        return self.to_s()

    def to_s(self, level: int = 0) -> str:
        indent = "  " * level
        nested = f"{indent}    "

        context = self._to_s(f"{nested}  ")

        if context:
            context = [f"{nested}Context:"] + context

        lines = [
            f"{indent}- {self.id}",
            *context,
            f"{nested}Projections:",
        ]

        for expression in self.projections:
            lines.append(f"{nested}  - {expression.sql()}")

        if self.condition:
            lines.append(f"{nested}Condition: {self.condition.sql()}")

        if self.limit is not math.inf:
            lines.append(f"{nested}Limit: {self.limit}")

        if self.offset:
            lines.append(f"{nested}Offset: {self.offset}")

        if self.dependencies:
            lines.append(f"{nested}Dependencies:")
            for dependency in self.dependencies:
                lines.append("  " + dependency.to_s(level + 1))

        return "\n".join(lines)

    @property
    def type_name(self) -> str:
        return self.__class__.__name__

    @property
    def id(self) -> str:
        name = self.name
        name = f" {name}" if name else ""
        return f"{self.type_name}:{name} ({id(self)})"

    def _to_s(self, _indent: str) -> list[str]:
        return []


class Scan(Step):
    @classmethod
    def from_expression(cls, expression: exp.Expr, ctes: dict[str, Step] | None = None) -> Step:
        table: exp.Expr = expression
        alias_ = expression.alias_or_name

        if isinstance(expression, exp.Subquery):
            table = expression.this
            step = Step.from_expression(table, ctes)
            step.name = alias_
            return step

        step = Scan()
        step.name = alias_
        step.source = expression
        if ctes and table.name in ctes:
            step.add_dependency(ctes[table.name])

        return step

    def __init__(self) -> None:
        super().__init__()
        self.source: exp.Expr | None = None

    def _to_s(self, indent: str) -> list[str]:
        return [f"{indent}Source: {self.source.sql() if self.source else '-static-'}"]  # type: ignore


class Join(Step):
    @classmethod
    def from_joins(cls, joins: Iterable[exp.Join], ctes: dict[str, Step] | None = None) -> Join:
        step = Join()

        for join in joins:
            source_key, join_key, condition = join_condition(join)
            step.joins[join.alias_or_name] = {
                "side": join.side,  # type: ignore
                "join_key": join_key,
                "source_key": source_key,
                "condition": condition,
            }

            step.add_dependency(Scan.from_expression(join.this, ctes))

        return step

    def __init__(self) -> None:
        super().__init__()
        self.source_name: str | None = None
        self.joins: dict[str, dict[str, list[str] | exp.Expr | list[exp.Expr]]] = {}

    def _to_s(self, indent: str) -> list[str]:
        lines = [f"{indent}Source: {self.source_name or self.name}"]
        for name, join in self.joins.items():
            lines.append(f"{indent}{name}: {join['side'] or 'INNER'}")
            join_key = ", ".join(str(key) for key in t.cast(list[str], join.get("join_key") or []))
            if join_key:
                lines.append(f"{indent}Key: {join_key}")
            if join.get("condition"):
                lines.append(f"{indent}On: {join['condition'].sql()}")  # type: ignore
        return lines


class Aggregate(Step):
    def __init__(self) -> None:
        super().__init__()
        self.aggregations: list[exp.Expr] = []
        self.operands: tuple[exp.Expr, ...] = ()
        self.group: dict[str, exp.Expr] = {}
        self.source: str | None = None

    def _to_s(self, indent: str) -> list[str]:
        lines = [f"{indent}Aggregations:"]

        for expression in self.aggregations:
            lines.append(f"{indent}  - {expression.sql()}")

        if self.group:
            lines.append(f"{indent}Group:")
            for expression in self.group.values():
                lines.append(f"{indent}  - {expression.sql()}")
        if self.condition:
            lines.append(f"{indent}Having:")
            lines.append(f"{indent}  - {self.condition.sql()}")
        if self.operands:
            lines.append(f"{indent}Operands:")
            for expression in self.operands:
                lines.append(f"{indent}  - {expression.sql()}")

        return lines


class Window(Step):
    def __init__(self) -> None:
        super().__init__()
        self.windows: dict[str, exp.Window] = {}
        self.table: str = "_w"

    def _to_s(self, indent: str) -> list[str]:
        lines = [f"{indent}Windows:"]

        for name, window in self.windows.items():
            lines.append(f"{indent}  - {name}: {window.sql(unsupported_level=ErrorLevel.IGNORE)}")

        return lines


class Sort(Step):
    def __init__(self) -> None:
        super().__init__()
        self.key: list[exp.Expr] | None = None

    def _to_s(self, indent: str) -> list[str]:
        lines = [f"{indent}Key:"]

        for expression in self.key:  # type: ignore
            lines.append(f"{indent}  - {expression.sql()}")

        return lines


class SetOperation(Step):
    def __init__(self, op: type[exp.Expr], left: str, right: str, distinct: bool = False) -> None:
        super().__init__()
        self.op: type[exp.Expr] = op
        self.left: str = left
        self.right: str = right
        self.distinct: bool = distinct

    @classmethod
    def from_expression(
        cls, expression: exp.Expr, ctes: dict[str, Step] | None = None
    ) -> SetOperation:
        assert isinstance(expression, exp.SetOperation)

        left = Step.from_expression(expression.left, ctes)
        # SELECT 1 UNION SELECT 2  <-- these subqueries don't have names
        left.name = left.name or "left"
        right = Step.from_expression(expression.right, ctes)
        right.name = right.name or "right"
        step = cls(
            op=expression.__class__,
            left=left.name,
            right=right.name,
            distinct=bool(expression.args.get("distinct")),
        )

        step.add_dependency(left)
        step.add_dependency(right)

        return step

    def _to_s(self, indent: str) -> list[str]:
        lines: list[str] = []
        if self.distinct:
            lines.append(f"{indent}Distinct: {self.distinct}")
        return lines

    @property
    def type_name(self) -> str:
        return self.op.__name__
