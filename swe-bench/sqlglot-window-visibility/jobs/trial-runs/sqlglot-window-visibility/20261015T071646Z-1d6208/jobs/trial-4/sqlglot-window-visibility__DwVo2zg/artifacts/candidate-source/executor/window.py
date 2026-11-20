from __future__ import annotations

import decimal
import typing as t

from sqlglot.errors import ExecuteError

UNBOUNDED_PRECEDING = "UNBOUNDED PRECEDING"
PRECEDING = "PRECEDING"
CURRENT_ROW = "CURRENT ROW"
FOLLOWING = "FOLLOWING"
UNBOUNDED_FOLLOWING = "UNBOUNDED FOLLOWING"

ROWS = "ROWS"
RANGE = "RANGE"
GROUPS = "GROUPS"

EXCLUDE_CURRENT_ROW = "CURRENT ROW"
EXCLUDE_GROUP = "GROUP"
EXCLUDE_TIES = "TIES"

Bound = tuple[str, t.Any]


class Frame:
    """The rows of a partition that a window function reads, relative to the current row."""

    def __init__(
        self,
        kind: str = RANGE,
        start: Bound = (UNBOUNDED_PRECEDING, None),
        end: Bound = (CURRENT_ROW, None),
        exclude: str | None = None,
    ) -> None:
        self.kind = kind
        self.start = start
        self.end = end
        self.exclude = exclude


def check_offset(value: t.Any, what: str, minimum: int = 0) -> int:
    """Validate a ROWS / GROUPS / navigation offset, which must be a whole number."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)

    if isinstance(value, bool) or not isinstance(value, int):
        raise ExecuteError(f"{what} must be a whole number, got {value!r}")

    if value < minimum:
        raise ExecuteError(f"{what} must be {'positive' if minimum else 'non-negative'}: {value}")

    return value


def check_range_offset(value: t.Any) -> t.Any:
    if isinstance(value, bool) or not isinstance(value, (int, float, decimal.Decimal)):
        raise ExecuteError(f"RANGE offset must be a number, got {value!r}")

    if value < 0 or value != value:
        raise ExecuteError(f"RANGE offset must be non-negative: {value}")

    return value


def _is_number(value: t.Any) -> bool:
    return isinstance(value, (int, float, decimal.Decimal)) and not isinstance(value, bool)


def _first_true(predicate: t.Callable[[int], bool], size: int) -> int:
    """The first index for which a monotonic predicate holds, or `size` if there isn't any."""
    low, high = 0, size

    while low < high:
        middle = (low + high) // 2
        if predicate(middle):
            high = middle
        else:
            low = middle + 1

    return low


class WindowFunction:
    """
    Evaluates a window function for every row of a relation.

    The relation is described by per row columns that have already been evaluated:

    - `partition_keys`: tuple of partitioning values or None when there is no PARTITION BY
    - `order_values`: tuple of raw ordering values or None when there is no ORDER BY
    - `sort_keys`: tuple of sortable ordering values (see `ordered` in env)
    - `values`: the operand of the function
    - `params`: the offset, bucket count or n of the function
    - `defaults`: the default of LAG / LEAD
    - `include`: result of the FILTER predicate

    Results are returned in the order of the input rows, which is never rearranged.
    """

    def __init__(
        self,
        kind: str,
        frame: Frame,
        order_directions: list[tuple[bool, bool]],
        aggregate: t.Callable[[list], t.Any] | None = None,
    ) -> None:
        self.kind = kind
        self.frame = frame
        self.order_directions = order_directions
        self.aggregate = aggregate

    def run(
        self,
        size: int,
        partition_keys: list | None,
        order_values: list | None,
        sort_keys: list | None,
        values: list | None = None,
        params: list | None = None,
        defaults: list | None = None,
        include: list | None = None,
    ) -> list:
        results: list = [None] * size
        partitions: dict[t.Any, list[int]] = {}

        if partition_keys is None:
            partitions[()] = list(range(size))
        else:
            for index, key in enumerate(partition_keys):
                partitions.setdefault(key, []).append(index)

        for rows in partitions.values():
            if sort_keys is not None:
                rows.sort(key=sort_keys.__getitem__)

            self._partition(rows, results, order_values, values, params, defaults, include)

        return results

    def _partition(self, rows, results, order_values, values, params, defaults, include) -> None:
        size = len(rows)
        group_ids = [0] * size
        group_starts: list[int] = []
        group_ends: list[int] = []
        previous: t.Any = None

        for position, row in enumerate(rows):
            key = order_values[row] if order_values is not None else ()

            if position == 0 or key != previous:
                if group_starts:
                    group_ends.append(position)
                group_starts.append(position)

            group_ids[position] = len(group_starts) - 1
            previous = key

        group_ends.append(size)

        kind = self.kind

        if kind == "ROW_NUMBER":
            for position, row in enumerate(rows):
                results[row] = position + 1
        elif kind == "RANK":
            for position, row in enumerate(rows):
                results[row] = group_starts[group_ids[position]] + 1
        elif kind == "DENSE_RANK":
            for position, row in enumerate(rows):
                results[row] = group_ids[position] + 1
        elif kind == "PERCENT_RANK":
            for position, row in enumerate(rows):
                start = group_starts[group_ids[position]]
                results[row] = start / (size - 1) if size > 1 else 0.0
        elif kind == "CUME_DIST":
            for position, row in enumerate(rows):
                results[row] = group_ends[group_ids[position]] / size
        elif kind == "NTILE":
            for position, row in enumerate(rows):
                buckets = params[row]
                if buckets is not None:
                    results[row] = self._ntile(position, size, check_offset(buckets, "NTILE", 1))
        elif kind in ("LAG", "LEAD"):
            sign = -1 if kind == "LAG" else 1

            for position, row in enumerate(rows):
                offset = params[row]

                if offset is None:
                    continue

                target = position + sign * check_offset(offset, f"{kind} offset")

                if 0 <= target < size:
                    results[row] = values[rows[target]]
                elif defaults is not None:
                    results[row] = defaults[row]
        else:
            partition = _Partition(rows, group_ids, group_starts, group_ends)

            if self.frame.kind == RANGE and any(
                mode in (PRECEDING, FOLLOWING) for mode, _ in (self.frame.start, self.frame.end)
            ):
                partition.range_keys = [order_values[row][0] for row in rows]

            self._framed(partition, results, values, params, include)

    @staticmethod
    def _ntile(position: int, size: int, buckets: int) -> int:
        base, extra = divmod(size, buckets)
        boundary = extra * (base + 1)

        if position < boundary:
            return position // (base + 1) + 1

        return extra + (position - boundary) // base + 1

    def _framed(self, partition, results, values, params, include) -> None:
        frame = self.frame
        rows, size = partition.rows, partition.size
        partition_values = [values[row] for row in rows] if values is not None else []
        partition_include = [include[row] for row in rows] if include is not None else None
        cache: dict[tuple, t.Any] = {}

        for position, row in enumerate(rows):
            start = max(self._bound(frame.start, True, position, partition), 0)
            end = min(self._bound(frame.end, False, position, partition), size)
            positions: t.Any = range(start, end) if start < end else ()

            if frame.exclude and positions:
                positions = self._exclude(positions, frame.exclude, position, partition)

            if self.kind == "AGG":
                key = (start, end) if not frame.exclude else None

                if key is not None and key in cache:
                    results[row] = cache[key]
                    continue

                if isinstance(positions, range):
                    frame_values = partition_values[positions.start : positions.stop]

                    if partition_include is not None:
                        flags = partition_include[positions.start : positions.stop]
                        frame_values = [v for v, flag in zip(frame_values, flags) if flag]
                else:
                    frame_values = [
                        partition_values[p]
                        for p in positions
                        if partition_include is None or partition_include[p]
                    ]

                results[row] = self.aggregate(frame_values)  # type: ignore

                if key is not None:
                    cache[key] = results[row]
            elif self.kind == "FIRST_VALUE":
                results[row] = partition_values[positions[0]] if positions else None
            elif self.kind == "LAST_VALUE":
                results[row] = partition_values[positions[-1]] if positions else None
            elif self.kind == "NTH_VALUE":
                nth = params[row]

                if nth is not None:
                    nth = check_offset(nth, "NTH_VALUE", 1)

                    if nth <= len(positions):
                        results[row] = partition_values[positions[nth - 1]]
            else:
                raise ExecuteError(f"Unsupported window function {self.kind}")

    @staticmethod
    def _exclude(positions, exclude, position, partition):
        group = partition.group_ids[position]

        if exclude == EXCLUDE_CURRENT_ROW:
            excluded_start, excluded_end = position, position + 1
        else:
            excluded_start = partition.group_starts[group]
            excluded_end = partition.group_ends[group]

        first, last = positions[0], positions[-1] + 1
        remaining = list(range(first, min(last, excluded_start)))

        if exclude == EXCLUDE_TIES and first <= position < last:
            remaining.append(position)

        remaining.extend(range(max(first, excluded_end), last))
        return remaining

    def _bound(self, bound: Bound, is_start: bool, position: int, partition: _Partition) -> int:
        """
        The first row (start bound) or the row after the last row (end bound) of the frame. The
        result isn't clipped to the partition.
        """
        mode, offset = bound
        kind = self.frame.kind
        size = partition.size
        group = partition.group_ids[position]

        if mode == UNBOUNDED_PRECEDING:
            return 0
        if mode == UNBOUNDED_FOLLOWING:
            return size

        if kind == ROWS:
            if mode == CURRENT_ROW:
                target = position
            else:
                target = position - offset if mode == PRECEDING else position + offset

            return target if is_start else target + 1

        if kind == GROUPS:
            if mode == CURRENT_ROW:
                target = group
            else:
                target = group - offset if mode == PRECEDING else group + offset

            if target < 0:
                return 0

            edges = partition.group_starts if is_start else partition.group_ends
            return edges[target] if target < len(edges) else size

        peers = partition.group_starts[group] if is_start else partition.group_ends[group]

        if mode == CURRENT_ROW:
            return peers

        keys = partition.range_keys
        current = keys[position]  # type: ignore

        if not _is_number(current):
            return peers

        descending, nulls_first = self.order_directions[0]
        delta = -offset if mode == PRECEDING else offset

        if descending:
            delta = -delta

        if isinstance(current, decimal.Decimal) and not isinstance(delta, decimal.Decimal):
            delta = decimal.Decimal(delta)

        limit = current + delta

        if is_start:

            def not_before(index: int) -> bool:
                key = keys[index]  # type: ignore
                if key is None:
                    return not nulls_first
                return key <= limit if descending else key >= limit

            return _first_true(not_before, size)

        def after(index: int) -> bool:
            key = keys[index]  # type: ignore
            if key is None:
                return not nulls_first
            return key < limit if descending else key > limit

        return _first_true(after, size)


class _Partition:
    def __init__(self, rows, group_ids, group_starts, group_ends) -> None:
        self.rows = rows
        self.size = len(rows)
        self.group_ids = group_ids
        self.group_starts = group_starts
        self.group_ends = group_ends
        self.range_keys: list | None = None
