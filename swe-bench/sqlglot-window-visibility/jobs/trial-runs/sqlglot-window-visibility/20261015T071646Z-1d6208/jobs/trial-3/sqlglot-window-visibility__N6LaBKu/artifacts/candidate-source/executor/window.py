"""Frame arithmetic for window functions, independent of how values are evaluated."""

from __future__ import annotations

import typing as t
from decimal import Decimal

from sqlglot.errors import ExecuteError

UNBOUNDED = "UNBOUNDED"
CURRENT = "CURRENT"
OFFSET = "OFFSET"

ROWS = "ROWS"
RANGE = "RANGE"
GROUPS = "GROUPS"


def is_number(value: t.Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


class Bound:
    __slots__ = ("kind", "following", "offset")

    def __init__(self, kind: str, following: bool = False, offset: t.Any = None) -> None:
        self.kind = kind
        self.following = following
        self.offset = offset

    @property
    def rank(self) -> int:
        if self.kind == UNBOUNDED:
            return 4 if self.following else 0
        if self.kind == CURRENT:
            return 2
        return 3 if self.following else 1


class Frame:
    __slots__ = ("kind", "start", "end", "exclude")

    def __init__(
        self,
        kind: str = RANGE,
        start: Bound | None = None,
        end: Bound | None = None,
        exclude: str | None = None,
    ) -> None:
        self.kind = kind
        self.start = start or Bound(UNBOUNDED)
        self.end = end or Bound(CURRENT)
        self.exclude = exclude

        if self.start.kind == UNBOUNDED and self.start.following:
            raise ExecuteError("Window frame start cannot be UNBOUNDED FOLLOWING")
        if self.end.kind == UNBOUNDED and not self.end.following:
            raise ExecuteError("Window frame end cannot be UNBOUNDED PRECEDING")
        if self.start.rank > self.end.rank:
            raise ExecuteError("Window frame start cannot be after the frame end")

    @property
    def has_offset(self) -> bool:
        return OFFSET in (self.start.kind, self.end.kind)


class Partition:
    """
    The rows of one window partition, in window order, along with their peer groups.

    `rows` holds indices into the relation the window reads. Positions are offsets into `rows`.
    """

    __slots__ = ("rows", "size", "group", "starts", "ends")

    def __init__(self, rows: list[int], order_keys: list[t.Any] | None) -> None:
        self.rows = rows
        self.size = len(rows)
        self.group = [0] * self.size
        self.starts: list[int] = []
        previous: t.Any = None

        for position, row in enumerate(rows):
            key = order_keys[row] if order_keys is not None else ()

            if position == 0 or key != previous:
                self.starts.append(position)
                previous = key

            self.group[position] = len(self.starts) - 1

        self.ends = self.starts[1:] + [self.size]


def frame_range(
    frame: Frame,
    partition: Partition,
    position: int,
    keys: list[t.Any] | None = None,
    desc: bool = False,
    nulls_first: bool = False,
) -> tuple[int, int]:
    """
    The half-open span of positions that the frame covers, before any exclusion.

    `keys` holds the single ordering value of each row of the relation, and is only needed for
    RANGE frames with an offset.
    """
    low = _bound(frame, frame.start, True, partition, position, keys, desc, nulls_first)
    high = _bound(frame, frame.end, False, partition, position, keys, desc, nulls_first)
    low = max(low, 0)
    high = min(high, partition.size)

    return (low, high) if low < high else (0, 0)


def _bound(
    frame: Frame,
    bound: Bound,
    is_start: bool,
    partition: Partition,
    position: int,
    keys: list[t.Any] | None,
    desc: bool,
    nulls_first: bool,
) -> int:
    group = partition.group[position]

    if bound.kind == UNBOUNDED:
        return 0 if is_start else partition.size

    if bound.kind == CURRENT:
        if frame.kind == ROWS:
            return position if is_start else position + 1
        return partition.starts[group] if is_start else partition.ends[group]

    step = bound.offset if bound.following else -bound.offset

    if frame.kind == ROWS:
        return position + step if is_start else position + step + 1

    if frame.kind == GROUPS:
        target = group + step

        if target < 0:
            return 0
        if target >= len(partition.starts):
            return partition.size
        return partition.starts[target] if is_start else partition.ends[target]

    assert keys is not None
    current = keys[partition.rows[position]]

    # a current key that is not a number has no distance to other keys: only its peers match
    if not is_number(current):
        return partition.starts[group] if is_start else partition.ends[group]

    if isinstance(current, Decimal) and isinstance(step, float):
        step = Decimal(str(step))
    elif isinstance(current, float) and isinstance(step, Decimal):
        step = float(step)

    limit = current + (-step if desc else step)
    rows = partition.rows

    def before(row: int) -> bool:
        key = keys[row]

        if key is None:
            return nulls_first
        return key > limit if desc else key < limit

    def within(row: int) -> bool:
        key = keys[row]

        if key is None:
            return nulls_first
        return key >= limit if desc else key <= limit

    # both predicates are true for a prefix of the partition and false afterwards
    predicate = before if is_start else within
    low, high = 0, partition.size

    while low < high:
        middle = (low + high) // 2

        if predicate(rows[middle]):
            low = middle + 1
        else:
            high = middle

    return low


def frame_ranges(
    frame: Frame, partition: Partition, position: int, span: tuple[int, int]
) -> list[tuple[int, int]]:
    """The half-open runs of positions in `span` which remain after the frame's exclusion."""
    low, high = span
    exclude = frame.exclude

    if low >= high:
        return []

    if exclude is None or exclude == "NO OTHERS":
        return [(low, high)]

    if exclude == "CURRENT ROW":
        holes = [(position, position + 1)]
    else:
        group = partition.group[position]
        holes = [(partition.starts[group], partition.ends[group])]

        if exclude == "TIES":
            holes = [(holes[0][0], position), (position + 1, holes[0][1])]

    runs = []

    for hole_start, hole_end in holes:
        if low < hole_start:
            runs.append((low, min(hole_start, high)))
        low = max(low, hole_end)

    if low < high:
        runs.append((low, high))

    return [run for run in runs if run[0] < run[1]]


def ntile_bucket(buckets: int, size: int, position: int) -> int:
    base, extra = divmod(size, buckets)
    boundary = extra * (base + 1)

    if position < boundary:
        return position // (base + 1) + 1

    return extra + (position - boundary) // base + 1
