from __future__ import annotations

import math
import typing as t

from sqlglot import alias, exp
from sqlglot.errors import ExecuteError
from sqlglot.helper import name_sequence
from sqlglot.optimizer.eliminate_joins import join_condition
from sqlglot.optimizer.scope import find_all_in_scope, find_in_scope
from collections.abc import Iterator, Sequence, Iterable


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

        if isinstance(expression, exp.Select) and expression.find(exp.Window):
            windowed = _plan_windows(expression, ctes)

            if windowed is not None:
                return windowed

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

        for e in expression.expressions:
            if find_in_scope(e, exp.AggFunc):
                projections.append(exp.column(e.alias_or_name, step.name, quoted=True))
                extract_agg_operands(e)
            else:
                projections.append(e)

        where: exp.Where | None = expression.args.get("where")

        if where is not None:
            step.condition = where.this

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

            for projection in projections:
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

        order: exp.Order | None = expression.args.get("order")

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


class Frame:
    """
    A window frame. Each endpoint type is one of UNBOUNDED_PRECEDING, PRECEDING, CURRENT,
    FOLLOWING or UNBOUNDED_FOLLOWING, and `exclude` is None, CURRENT ROW, GROUP or TIES.
    """

    def __init__(
        self,
        kind: str,
        start: str,
        start_offset: float,
        end: str,
        end_offset: float,
        exclude: str | None,
    ) -> None:
        self.kind = kind
        self.start = start
        self.start_offset = start_offset
        self.end = end
        self.end_offset = end_offset
        self.exclude = exclude

    @property
    def has_range_offset(self) -> bool:
        return self.kind == "RANGE" and (
            self.start in ("PRECEDING", "FOLLOWING") or self.end in ("PRECEDING", "FOLLOWING")
        )

    def __repr__(self) -> str:
        parts = [self.kind, self.start, str(self.start_offset), self.end, str(self.end_offset)]
        return " ".join(parts + ([f"EXCLUDE {self.exclude}"] if self.exclude else []))


DEFAULT_FRAME = Frame("RANGE", "UNBOUNDED_PRECEDING", 0, "CURRENT", 0, None)


class WindowFunction:
    """
    A window function over columns of the input of a Window step.

    `kind` is AGG for aggregates (looked up by `func` in the execution environment), or one of
    ROWNUMBER, RANK, DENSERANK, PERCENTRANK, CUMEDIST, NTILE, LAG, LEAD, FIRSTVALUE, LASTVALUE
    and NTHVALUE. `order` holds (column, desc, nulls_first) triples.
    """

    def __init__(
        self,
        name: str,
        kind: str,
        func: str = "",
        arg: str | None = None,
        filter: str | None = None,
        default: str | None = None,
        offset: int = 0,
        partition: tuple[str, ...] = (),
        order: tuple[tuple[str, bool, bool], ...] = (),
        frame: Frame | None = None,
    ) -> None:
        self.name = name
        self.kind = kind
        self.func = func
        self.arg = arg
        self.filter = filter
        self.default = default
        self.offset = offset
        self.partition = partition
        self.order = order
        self.frame = frame or DEFAULT_FRAME

    def __repr__(self) -> str:
        func = self.func or self.kind
        return (
            f"{self.name} = {func}({self.arg or ''}) FILTER {self.filter} "
            f"PARTITION {list(self.partition)} ORDER {list(self.order)} {self.frame!r}"
        )


class Window(Step):
    def __init__(self) -> None:
        super().__init__()
        self.functions: list[WindowFunction] = []

    def _to_s(self, indent: str) -> list[str]:
        return [f"{indent}Window:", *(f"{indent}  - {function!r}" for function in self.functions)]


WINDOW_STEP_NAME = "_window"
RANKING_FUNCTIONS = (exp.RowNumber, exp.Rank, exp.DenseRank, exp.PercentRank, exp.CumeDist)
FRAME_KINDS = ("ROWS", "RANGE", "GROUPS")


def _scope_windows(expression: exp.Expr) -> Iterator[exp.Window]:
    """Windows of the query that owns `expression`, not those of its subqueries."""
    stack = [expression]

    while stack:
        node = stack.pop()

        if isinstance(node, exp.Window):
            yield node
        else:
            stack.extend(c for c in node.iter_expressions() if not isinstance(c, exp.Query))


def _window_number(expression: exp.Expr | None, what: str) -> int | float:
    node = expression

    while isinstance(node, exp.Paren):
        node = node.this

    inner = node.this if isinstance(node, exp.Neg) else node
    while isinstance(inner, exp.Paren):
        inner = inner.this

    if not isinstance(inner, exp.Literal) or not inner.is_number:
        raise ExecuteError(f"{what} must be a numeric literal")

    value = node.to_py()  # type: ignore
    return int(value) if value == int(value) else float(value)


def _window_integer(expression: exp.Expr | None, minimum: int, what: str) -> int:
    value = _window_number(expression, what)

    if value != int(value) or value < minimum:
        raise ExecuteError(f"{what} must be an integer of at least {minimum}")

    return int(value)


class _WindowRewriter:
    """
    Splits a SELECT with window functions into three stages:

    1. an inner SELECT computing, as `_iN` columns, every expression the windows and the rest
       of the query read (this performs WHERE, GROUP BY and HAVING),
    2. a Window step appending one `_wN` column per window function to each row,
    3. an outer SELECT over the Window step applying the final projections, QUALIFY, DISTINCT,
       ORDER BY, LIMIT and OFFSET to expressions rewritten in terms of those columns.
    """

    def __init__(self, select: exp.Select) -> None:
        self.inputs: dict[exp.Expr, str] = {}
        self.functions: dict[tuple, WindowFunction] = {}
        self.named: dict[str, exp.Window] = {}
        self.alias_refs: dict[str, exp.Expr] = {}

        for window in select.args.get("windows") or []:
            if isinstance(window.this, exp.Expr):
                self.named[window.this.name] = window

    def column(self, name: str) -> exp.Column:
        return exp.column(name, WINDOW_STEP_NAME, quoted=True)

    def input(self, expression: exp.Expr) -> str:
        key = expression.copy()

        for column in list(key.find_all(exp.Column)):
            if self._is_alias_ref(column):
                if column is key:
                    key = self.alias_refs[column.name].copy()
                else:
                    column.replace(self.alias_refs[column.name].copy())

        name = self.inputs.get(key)

        if name is None:
            name = self.inputs[key] = f"_i{len(self.inputs)}"

        return name

    def _is_alias_ref(self, node: exp.Expr) -> bool:
        return isinstance(node, exp.Column) and not node.table and node.name in self.alias_refs

    def _blocked(self, node: exp.Expr) -> bool:
        stack = [node]

        while stack:
            current = stack.pop()

            if isinstance(current, exp.Window) or self._is_alias_ref(current):
                return True

            stack.extend(c for c in current.iter_expressions() if not isinstance(c, exp.Query))

        return False

    def rewrite(self, node: exp.Expr) -> exp.Expr:
        if isinstance(node, exp.Window):
            return self.column(self.window(node))

        if not self._blocked(node):
            if node.find(exp.Column) or find_in_scope(node, exp.AggFunc):
                return self.column(self.input(node))
            return node

        for child in list(node.iter_expressions()):
            replacement = self.rewrite(child)

            if replacement is not child:
                child.replace(replacement)

        return node

    def resolve(
        self, window: exp.Window, seen: tuple[str, ...] = ()
    ) -> tuple[list[exp.Expr], exp.Expr | None, exp.Expr | None]:
        partition = window.args.get("partition_by") or []
        order = window.args.get("order")
        spec = window.args.get("spec")
        base = window.args.get("alias")

        if base is not None:
            name = base.name
            definition = self.named.get(name) or next(
                (w for n, w in self.named.items() if n.lower() == name.lower()), None
            )

            if definition is None:
                raise ExecuteError(f"Window '{name}' is not defined")
            if name in seen:
                raise ExecuteError(f"Window '{name}' is defined in terms of itself")

            base_partition, base_order, base_spec = self.resolve(definition, (*seen, name))
            partition = partition or base_partition
            order = order or base_order
            spec = spec or base_spec

        return partition, order, spec

    def frame(self, spec: exp.Expr | None, order_count: int) -> Frame | None:
        if spec is None:
            return None

        kind = str(spec.args.get("kind") or "").upper()

        if kind not in FRAME_KINDS:
            raise ExecuteError(f"Unsupported window frame {spec.sql()}")

        def bound(value: t.Any, side: t.Any, is_start: bool) -> tuple[str, float]:
            side = str(side or "").upper()
            text = (
                value
                if isinstance(value, str)
                else value.name
                if isinstance(value, exp.Var)
                else None
            )

            if text is not None:
                text = text.upper()

                if text == "CURRENT ROW":
                    return "CURRENT", 0
                if text == "UNBOUNDED" and side == ("PRECEDING" if is_start else "FOLLOWING"):
                    return f"UNBOUNDED_{side}", 0
                raise ExecuteError(f"Invalid window frame boundary {text} {side}".strip())

            if side not in ("PRECEDING", "FOLLOWING"):
                raise ExecuteError("Window frame offsets must be PRECEDING or FOLLOWING")

            if kind == "RANGE":
                offset = _window_number(value, "RANGE offset")
                if offset < 0:
                    raise ExecuteError("RANGE offset must not be negative")
                if order_count != 1:
                    raise ExecuteError("RANGE with an offset requires exactly one ORDER BY term")
            else:
                offset = _window_integer(value, 0, f"{kind} offset")

            return side, offset

        end_value = spec.args.get("end")
        start, start_offset = bound(spec.args.get("start"), spec.args.get("start_side"), True)

        if end_value is None:
            end, end_offset = "CURRENT", 0
        else:
            end, end_offset = bound(end_value, spec.args.get("end_side"), False)

        exclude = spec.args.get("exclude")
        exclude_name = exclude.name.upper() if exclude is not None else None

        return Frame(
            kind,
            start,
            start_offset,
            end,
            end_offset,
            None if exclude_name in (None, "NO OTHERS") else exclude_name,
        )

    def window(self, window: exp.Window) -> str:
        if isinstance(window.find_ancestor(exp.AggFunc, exp.Select), exp.AggFunc):
            raise ExecuteError("Window functions cannot be nested in aggregate functions")

        func = window.this
        filter_ = None

        if isinstance(func, exp.Filter):
            filter_ = self.input(func.expression.this)
            func = func.this

        if isinstance(func, exp.IgnoreNulls):
            raise ExecuteError("IGNORE NULLS is not supported in window functions")
        if isinstance(func, exp.RespectNulls):
            func = func.this

        partition, order, spec = self.resolve(window)
        order_terms = [
            term if isinstance(term, exp.Ordered) else exp.Ordered(this=term)
            for term in (order.expressions if order is not None else [])
        ]

        for part in (*partition, *order_terms, *(spec.args.values() if spec else ())):
            if isinstance(part, exp.Expr) and any(True for _ in _scope_windows(part)):
                raise ExecuteError("Window functions cannot be nested")

        function = WindowFunction(
            name="",
            kind="",
            filter=filter_,
            partition=tuple(self.input(e) for e in partition),
            order=tuple(
                (
                    self.input(term.this),
                    bool(term.args.get("desc")),
                    (
                        bool(term.args["nulls_first"])
                        if term.args.get("nulls_first") is not None
                        else not term.args.get("desc")
                    ),
                )
                for term in order_terms
            ),
            frame=self.frame(spec, len(order_terms)),
        )

        if any(True for _ in _scope_windows(func)):
            raise ExecuteError("Window functions cannot be nested")

        if isinstance(func, RANKING_FUNCTIONS):
            function.kind = func.key.upper()
        elif isinstance(func, exp.Ntile):
            function.kind = "NTILE"
            function.offset = _window_integer(func.this, 1, "NTILE bucket count")
        elif isinstance(func, (exp.Lag, exp.Lead)):
            function.kind = func.key.upper()
            function.arg = self.input(func.this)
            offset = func.args.get("offset")
            function.offset = 1 if offset is None else _window_integer(offset, 0, "Offset")
            default = func.args.get("default")
            function.default = self.input(default) if default is not None else None
        elif isinstance(func, (exp.FirstValue, exp.LastValue)):
            function.kind = func.key.upper()
            function.arg = self.input(func.this)
        elif isinstance(func, exp.NthValue):
            function.kind = "NTHVALUE"
            function.arg = self.input(func.this)
            function.offset = _window_integer(func.args.get("offset"), 1, "NTH_VALUE offset")
        elif isinstance(func, exp.AggFunc) or (
            isinstance(func, exp.Anonymous) and len(func.expressions) == 1
        ):
            function.kind = "AGG"
            function.func = func.key.upper() if isinstance(func, exp.AggFunc) else func.name.upper()
            operand = func.this if isinstance(func, exp.AggFunc) else func.expressions[0]

            if isinstance(operand, exp.Distinct):
                raise ExecuteError("DISTINCT is not supported in window aggregates")

            function.arg = self.input(
                exp.Literal.number(1)
                if operand is None or isinstance(operand, exp.Star)
                else operand
            )
        else:
            raise ExecuteError(f"Unsupported window function {func.sql()}")

        if filter_ is not None and function.kind != "AGG":
            raise ExecuteError("FILTER is only supported for aggregate window functions")

        key = (
            function.kind,
            function.func,
            function.arg,
            function.filter,
            function.default,
            function.offset,
            function.partition,
            function.order,
            repr(function.frame),
        )
        existing = self.functions.get(key)

        if existing is None:
            function.name = f"_w{len(self.functions)}"
            existing = self.functions[key] = function

        return existing.name


def _plan_windows(expression: exp.Select, ctes: dict[str, Step]) -> Step | None:
    for key in ("where", "group", "having"):
        clause = expression.args.get(key)

        if clause is not None and any(True for _ in _scope_windows(clause)):
            raise ExecuteError(f"Window functions are not allowed in {key.upper()}")

    clauses = [
        *expression.expressions,
        *filter(None, map(expression.args.get, ("qualify", "order"))),
    ]

    if not any(True for clause in clauses for _ in _scope_windows(clause)):
        return None

    rewriter = _WindowRewriter(expression)
    aliases = {e.alias: e.this for e in expression.expressions if isinstance(e, exp.Alias)}

    def substitute(node: exp.Expr, only_windowed: bool) -> exp.Expr:
        for column in list(node.find_all(exp.Column)):
            replacement = aliases.get(column.name) if not column.table else None

            if replacement is not None and (
                not only_windowed or any(True for _ in _scope_windows(replacement))
            ):
                if column is node:
                    node = replacement.copy()
                else:
                    column.replace(replacement.copy())

        return node

    projections = []

    for i, e in enumerate(expression.expressions):
        name = e.alias_or_name or f"_col_{i}"
        value = e.this if isinstance(e, exp.Alias) else e
        projections.append(exp.alias_(rewriter.rewrite(value.copy()), name, quoted=True))

    outer = exp.Select(
        expressions=projections,
        from_=exp.From(this=exp.to_table(WINDOW_STEP_NAME)),
    )

    qualify = expression.args.get("qualify")

    if qualify is not None:
        outer.set("where", exp.Where(this=rewriter.rewrite(substitute(qualify.this.copy(), False))))

    order = expression.args.get("order")

    if order is not None:
        rewriter.alias_refs = aliases
        keys = []

        for ordered in order.expressions:
            ordered = ordered.copy()
            ordered.set("this", rewriter.rewrite(substitute(ordered.this, True)))
            keys.append(ordered)

        outer.set("order", exp.Order(expressions=keys))

    for key in ("distinct", "limit", "offset"):
        if expression.args.get(key) is not None:
            outer.set(key, expression.args[key].copy())

    inner = exp.Select(expressions=[])

    for key in ("from_", "joins", "where", "group", "having"):
        value = expression.args.get(key)

        if value:
            inner.set(key, [v.copy() for v in value] if isinstance(value, list) else value.copy())

    if not rewriter.inputs:
        rewriter.input(exp.Literal.number(1))

    inner.set(
        "expressions",
        [alias(e, name, quoted=True) for e, name in rewriter.inputs.items()],
    )

    window = Window()
    window.name = WINDOW_STEP_NAME
    window.functions = list(rewriter.functions.values())
    window.add_dependency(Step.from_expression(inner, ctes))

    return Step.from_expression(outer, {**ctes, WINDOW_STEP_NAME: window})
