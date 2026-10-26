"""Place a query graph on a plane: groups, layers, order and coordinates.

Pure geometry, with no Qt: the UI measures each node, passes the sizes in and
draws what comes back. Nodes sit in frames (one per schema, one for the
tables without a schema, the temp tables, the CTEs and derived tables, the
results), and the frames sit in layers from left to right:

1. the flows between frames point right;
2. so do the joins, read outwards from each statement's first source, as long
   as they do not close a cycle;
3. the cycles left are broken where a depth-first walk finds them;
4. a frame's layer is the longest path that reaches it;
5. a frame with only outgoing edges moves right, next to its first successor.

Inside a layer, frames and the nodes in each frame are ordered by the mean
position of their neighbours in the other layers, in two sweeps each way. A
frame stacks at most ``STACK`` nodes per column. Nodes in the same column of a
layer share its widest width, so every column is one vertical band of the
plane, and an edge that crosses bands passes each one through a free gap:
it never cuts a node. An edge between two nodes of the same band is a
bracket in a 10 px lane at its side, right for joins and left for flows.
Flows into a statement's result leave once per frame, from its title bar.

Coordinates start at (0, 0); the output depends only on the input.
"""

from __future__ import annotations

import bisect
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from .query_graph import GraphNode, QueryGraph

GroupKind = Literal["schema", "tables", "temp", "derived", "result"]
Side = Literal["left", "right"]
Point = tuple[float, float]
# y of a column's row from the top of its node; None asks for the header
Anchor = Callable[[GraphNode, str | None], float]

LAYER_GAP = 80.0
GROUP_GAP = 26.0
NODE_GAP = 18.0
FRAME_PAD = 14.0
FRAME_TITLE = 28.0
LANE = 10.0
STACK = 6  # nodes per column inside a frame
CLEARANCE = 8.0  # how close a route may pass to a node
DEFAULT_SIZE = (168.0, 56.0)


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def moved(self, dx: float, dy: float) -> Rect:
        return Rect(self.x + dx, self.y + dy, self.w, self.h)


@dataclass
class GroupBox:
    key: str  # "schema:vendas", "tables", "temp", "derived", "result"
    kind: GroupKind
    name: str  # the schema as written; "" for the other kinds
    layer: int
    nodes: list[str]  # top to bottom, column by column
    rect: Rect


@dataclass
class Bundle:
    """Flows from the nodes of one frame into one result, drawn as one."""

    group: str
    target: str
    edges: list[int]  # indexes in ``graph.edges``
    points: list[Point]


@dataclass
class Layout:
    nodes: dict[str, Rect]
    groups: list[GroupBox]
    routes: dict[int, list[Point]]  # by index in ``graph.edges``, bundled ones out
    labels: dict[int, Point]  # where each routed edge's label goes
    bundles: list[Bundle]
    size: tuple[float, float]


def node_group(node: GraphNode) -> tuple[str, GroupKind, str]:
    """The frame a node belongs to: (key, kind, name)."""
    if node.kind == "result":
        return "result", "result", ""
    if node.kind in ("cte", "derived"):
        return "derived", "derived", ""
    if node.kind == "temp":
        return "temp", "temp", ""
    if node.schema:
        return f"schema:{node.schema.casefold()}", "schema", node.schema
    return "tables", "tables", ""


def layout(
    graph: QueryGraph,
    sizes: Mapping[str, tuple[float, float]],
    anchor: Anchor | None = None,
) -> Layout:
    """Lay *graph* out with each node at its size in *sizes* (width, height).

    *anchor* gives where an edge meets a node: the y of a column's row, or of
    the header for ``None``, from the node's top. By default, its middle.
    """
    if not graph.nodes:
        return Layout({}, [], {}, {}, [], (0.0, 0.0))
    return _Placer(graph, sizes, anchor).run()


@dataclass
class _Group:
    key: str
    kind: GroupKind
    name: str
    rank: int  # order of first appearance
    nodes: list[str] = field(default_factory=list)
    layer: int = 0


@dataclass
class _Band:
    """One column of one layer: every node in it has the same x and width."""

    left: float
    right: float
    nodes: list[tuple[float, float, str]]  # (top, bottom, id), top to bottom
    gaps: list[tuple[float, float]] = field(default_factory=list)
    ids: set[str] = field(default_factory=set)

    def free(self, skip: set[str]) -> list[tuple[float, float]]:
        """The heights a route may cross this band at, around every node but
        those in *skip*; empty when no node is left."""
        if skip.isdisjoint(self.ids):
            return self.gaps
        return _free([(top, bottom) for top, bottom, n in self.nodes if n not in skip])


@dataclass
class _Bracket:
    edge: int
    band: tuple[int, int]
    side: Side
    top: float
    bottom: float
    lane: int = 0


class _Placer:
    def __init__(
        self,
        graph: QueryGraph,
        sizes: Mapping[str, tuple[float, float]],
        anchor: Anchor | None,
    ) -> None:
        self.graph = graph
        self.node = {n.id: n for n in graph.nodes}
        self.index = {n.id: i for i, n in enumerate(graph.nodes)}
        self.size = {n.id: sizes.get(n.id, DEFAULT_SIZE) for n in graph.nodes}
        self.anchor = anchor
        self.edges = [
            (i, e)
            for i, e in enumerate(graph.edges)
            if e.source in self.node and e.target in self.node
        ]
        self.groups: dict[str, _Group] = {}
        self.group: dict[str, str] = {}
        for n in graph.nodes:
            key, kind, name = node_group(n)
            if key not in self.groups:
                self.groups[key] = _Group(key, kind, name, len(self.groups))
            self.groups[key].nodes.append(n.id)
            self.group[n.id] = key
        for g in self.groups.values():
            g.nodes.sort(key=lambda n: (not self.node[n].is_base, self.index[n]))
        self.top: dict[str, float] = {}  # node and group tops
        self.height: dict[str, float] = {}  # group heights

    def run(self) -> Layout:
        layers = self.layers()
        self.order(layers)
        return self.place(layers)

    # -- layers ---------------------------------------------------------------

    def layers(self) -> list[list[str]]:
        succ: dict[str, set[str]] = {k: set() for k in self.groups}
        for _, e in self.edges:
            a, b = self.group[e.source], self.group[e.target]
            if e.kind == "flow" and a != b:
                succ[a].add(b)
        for info in self.graph.statements:
            joins = [
                (self.group[e.source], self.group[e.target])
                for _, e in self.edges
                if e.kind == "join"
                and e.statement == info.index
                and self.group[e.source] != self.group[e.target]
            ]
            if not joins:
                continue
            start = self.group.get(info.base or "", joins[0][0])
            seen = _bfs(start, joins)
            for a, b in joins:
                if seen[a] > seen[b]:
                    a, b = b, a
                if b not in succ[a] and not _reaches(succ, b, a):
                    succ[a].add(b)
        self.drop_back_edges(succ)
        pred: dict[str, set[str]] = {k: set() for k in self.groups}
        for a, targets in succ.items():
            for b in targets:
                pred[b].add(a)
        layer: dict[str, int] = {}
        for k in self.topological(succ, pred):
            layer[k] = max((layer[p] + 1 for p in pred[k]), default=0)
        for k in self.groups:
            if succ[k] and not pred[k]:
                layer[k] = min(layer[s] for s in succ[k]) - 1
        used = sorted(set(layer.values()))
        layers: list[list[str]] = [[] for _ in used]
        for k in self.groups:  # by rank
            self.groups[k].layer = used.index(layer[k])
            layers[self.groups[k].layer].append(k)
        return layers

    def drop_back_edges(self, succ: dict[str, set[str]]) -> None:
        rank = {k: g.rank for k, g in self.groups.items()}
        state: dict[str, int] = {}  # 1 on the path, 2 done

        def visit(k: str) -> None:
            state[k] = 1
            for s in sorted(succ[k], key=rank.__getitem__):
                if state.get(s) == 1:
                    succ[k].discard(s)
                elif s not in state:
                    visit(s)
            state[k] = 2

        for k in self.groups:
            if k not in state:
                visit(k)

    def topological(
        self, succ: dict[str, set[str]], pred: dict[str, set[str]]
    ) -> list[str]:
        rank = {k: g.rank for k, g in self.groups.items()}
        missing = {k: len(p) for k, p in pred.items()}
        ready = sorted((k for k, n in missing.items() if not n), key=rank.__getitem__)
        out: list[str] = []
        while ready:
            k = ready.pop(0)
            out.append(k)
            for s in sorted(succ[k], key=rank.__getitem__):
                missing[s] -= 1
                if not missing[s]:
                    ready.append(s)
                    ready.sort(key=rank.__getitem__)
        return out

    # -- order ----------------------------------------------------------------

    def order(self, layers: list[list[str]]) -> None:
        layer_of = {n: self.groups[self.group[n]].layer for n in self.node}
        near: dict[str, list[str]] = defaultdict(list)
        for _, e in self.edges:
            if layer_of[e.source] != layer_of[e.target]:
                near[e.source].append(e.target)
                near[e.target].append(e.source)
        for i in range(len(layers)):
            self.stack(layers[i])
        if len(layers) < 2:
            return
        forward = range(1, len(layers))
        backward = range(len(layers) - 2, -1, -1)
        for _ in range(2):
            for step, sweep in ((1, forward), (-1, backward)):
                for i in sweep:
                    self.reorder(layers[i], i, step, near, layer_of)
                    self.stack(layers[i])

    def reorder(
        self,
        keys: list[str],
        i: int,
        step: int,
        near: dict[str, list[str]],
        layer_of: dict[str, int],
    ) -> None:
        """Sort a layer by where its neighbours on the *step* side are."""

        def seen_from(n: str) -> list[float]:
            return [self.middle(m) for m in near[n] if (layer_of[m] - i) * step < 0]

        where: dict[str, float] = {}
        for k in keys:
            group = self.groups[k]
            ys: list[float] = []
            spot: dict[str, float] = {}
            for n in group.nodes:
                mine = seen_from(n)
                ys.extend(mine)
                spot[n] = sum(mine) / len(mine) if mine else self.middle(n)
            position = {n: j for j, n in enumerate(group.nodes)}
            group.nodes.sort(
                key=lambda n: (spot[n], not self.node[n].is_base, position[n])
            )
            where[k] = sum(ys) / len(ys) if ys else self.top[k] + self.height[k] / 2
        position = {k: j for j, k in enumerate(keys)}
        keys.sort(key=lambda k: (where[k], position[k]))

    def middle(self, n: str) -> float:
        return self.top[n] + self.size[n][1] / 2

    def stack(self, keys: list[str]) -> None:
        """Set the tops of a layer's frames and nodes, centred on y = 0."""
        heights = []
        for k in keys:
            columns = _columns(self.groups[k].nodes)
            content = max(
                sum(self.size[n][1] for n in c) + NODE_GAP * (len(c) - 1)
                for c in columns
            )
            heights.append(FRAME_TITLE + 2 * FRAME_PAD + content)
        top = -(sum(heights) + GROUP_GAP * (len(keys) - 1)) / 2
        for k, height in zip(keys, heights):
            self.top[k] = top
            self.height[k] = height
            for c in _columns(self.groups[k].nodes):
                y = top + FRAME_TITLE + FRAME_PAD
                for n in c:
                    self.top[n] = y
                    y += self.size[n][1] + NODE_GAP
            top += height + GROUP_GAP

    # -- coordinates ----------------------------------------------------------

    def place(self, layers: list[list[str]]) -> Layout:
        band: dict[str, tuple[int, int]] = {}
        for i, keys in enumerate(layers):
            for k in keys:
                for j, c in enumerate(_columns(self.groups[k].nodes)):
                    for n in c:
                        band[n] = (i, j)
        width: dict[tuple[int, int], float] = defaultdict(float)
        for n, b in band.items():
            width[b] = max(width[b], self.size[n][0])

        bundled: dict[tuple[str, str], list[int]] = {}
        brackets: list[_Bracket] = []
        routed: list[int] = []
        for i, e in self.edges:
            if (
                e.kind == "flow"
                and self.node[e.target].kind == "result"
                and self.groups[self.group[e.source]].kind != "result"
            ):
                bundled.setdefault((self.group[e.source], e.target), []).append(i)
            elif band[e.source] == band[e.target]:
                ys = self.anchor_y(e.source, e.source_column)
                yt = self.anchor_y(e.target, e.target_column)
                side: Side = "right" if e.kind == "join" else "left"
                brackets.append(
                    _Bracket(i, band[e.source], side, min(ys, yt), max(ys, yt))
                )
            else:
                routed.append(i)
        lanes = _lanes(brackets)

        # x of every band, frames left-aligned in their layer
        left: dict[tuple[int, int], float] = {}
        right: dict[tuple[int, int], float] = {}
        frame_right: dict[str, float] = {}
        layer_x: list[float] = []
        x = 0.0
        for i, keys in enumerate(layers):
            layer_x.append(x)
            count = max(len(_columns(self.groups[k].nodes)) for k in keys)
            edge = x + FRAME_PAD
            for j in range(count):
                edge += lanes[((i, j), "left")] * LANE
                left[(i, j)] = edge
                edge += width[(i, j)]
                right[(i, j)] = edge
                edge += lanes[((i, j), "right")] * LANE + NODE_GAP
            for k in keys:
                last = (i, len(_columns(self.groups[k].nodes)) - 1)
                frame_right[k] = right[last] + lanes[(last, "right")] * LANE
                frame_right[k] += FRAME_PAD
            x = max(frame_right[k] for k in keys) + LAYER_GAP

        rects = {
            n: Rect(left[b], self.top[n], width[b], self.size[n][1])
            for n, b in band.items()
        }
        boxes = [
            GroupBox(
                k,
                self.groups[k].kind,
                self.groups[k].name,
                i,
                list(self.groups[k].nodes),
                Rect(
                    layer_x[i], self.top[k], frame_right[k] - layer_x[i], self.height[k]
                ),
            )
            for i, keys in enumerate(layers)
            for k in keys
        ]
        bands_at = {b: _Band(left[b], right[b], []) for b in left}
        for n, at in band.items():
            bands_at[at].nodes.append((rects[n].y, rects[n].bottom, n))
        for strip in bands_at.values():
            strip.nodes.sort()
            strip.gaps = _free([(top, bottom) for top, bottom, _ in strip.nodes])
            strip.ids = {n for _, _, n in strip.nodes}
        bands = sorted(bands_at.values(), key=lambda strip: strip.left)

        routes: dict[int, list[Point]] = {}
        labels: dict[int, Point] = {}
        for i in routed:
            e = self.graph.edges[i]
            ends = [
                (e.source, e.source_column),
                (e.target, e.target_column),
            ]
            ends.sort(key=lambda end: rects[end[0]].x)
            (a, a_col), (z, z_col) = ends
            start = (rects[a].right, rects[a].y + self.offset(a, a_col))
            end = (rects[z].x, rects[z].y + self.offset(z, z_col))
            points = _route(start, end, bands, {a, z})
            if a != e.source:
                points.reverse()
            routes[i] = points
            labels[i] = _halfway(points)

        used: dict[tuple[tuple[int, int], Side], list[float]] = defaultdict(list)
        for bracket in sorted(brackets, key=lambda b: (b.lane, b.edge)):
            e = self.graph.edges[bracket.edge]
            b = bracket.band
            lane_x = (
                right[b] + LANE * (bracket.lane + 1)
                if bracket.side == "right"
                else left[b] - LANE * (bracket.lane + 1)
            )
            points = []
            for n, column in ((e.source, e.source_column), (e.target, e.target_column)):
                rect = rects[n]
                y = rect.y + self.offset(n, column)
                side_x = rect.right if bracket.side == "right" else rect.x
                points.append((side_x, y))
            (sx, sy), (tx, ty) = points
            routes[bracket.edge] = [(sx, sy), (lane_x, sy), (lane_x, ty), (tx, ty)]
            y = _label_gap(bracket, bands_at[b].nodes, used[(b, bracket.side)])
            used[(b, bracket.side)].append(y)
            labels[bracket.edge] = (lane_x, y)

        frames = {box.key: box.rect for box in boxes}
        bundles = []
        for (key, target), members in bundled.items():
            frame = frames[key]
            rect = rects[target]
            start = (frame.right, frame.y + FRAME_TITLE / 2)
            end = (rect.x, rect.y + self.offset(target, None))
            bundles.append(
                Bundle(key, target, members, _route(start, end, bands, {target}))
            )

        return _normalized(rects, boxes, routes, labels, bundles)

    def offset(self, n: str, column: str | None) -> float:
        height = self.size[n][1]
        if self.anchor is None:
            return height / 2
        return min(max(self.anchor(self.node[n], column), 0.0), height)

    def anchor_y(self, n: str, column: str | None) -> float:
        return self.top[n] + self.offset(n, column)


def _columns(nodes: list[str]) -> list[list[str]]:
    return [nodes[i : i + STACK] for i in range(0, len(nodes), STACK)] or [[]]


def _bfs(start: str, joins: list[tuple[str, str]]) -> dict[str, int]:
    """The order in which a walk from *start* reaches each group of *joins*."""
    near: dict[str, list[str]] = defaultdict(list)
    for a, b in joins:
        near[a].append(b)
        near[b].append(a)
    seen: dict[str, int] = {}
    for root in [start, *(g for pair in joins for g in pair)]:
        if root in seen:
            continue
        seen[root] = len(seen)
        queue = deque([root])
        while queue:
            for g in near[queue.popleft()]:
                if g not in seen:
                    seen[g] = len(seen)
                    queue.append(g)
    return seen


def _reaches(succ: dict[str, set[str]], start: str, goal: str) -> bool:
    todo, seen = [start], {start}
    while todo:
        k = todo.pop()
        if k == goal:
            return True
        for s in succ[k]:
            if s not in seen:
                seen.add(s)
                todo.append(s)
    return False


def _lanes(brackets: list[_Bracket]) -> dict[tuple[tuple[int, int], Side], int]:
    """Give each bracket the lane nearest its band that it does not share with
    an overlapping one, the shortest first; return the lanes used per side."""
    count: dict[tuple[tuple[int, int], Side], int] = defaultdict(int)
    taken: dict[tuple[tuple[int, int], Side], list[list[_Bracket]]] = defaultdict(list)
    for bracket in sorted(brackets, key=lambda b: (b.bottom - b.top, b.top, b.edge)):
        lanes = taken[(bracket.band, bracket.side)]
        for k, lane in enumerate(lanes):
            if all(o.bottom < bracket.top or bracket.bottom < o.top for o in lane):
                bracket.lane = k
                lane.append(bracket)
                break
        else:
            bracket.lane = len(lanes)
            lanes.append([bracket])
        count[(bracket.band, bracket.side)] = len(lanes)
    return count


def _route(start: Point, end: Point, bands: list[_Band], skip: set[str]) -> list[Point]:
    """A line from *start* to *end* (left to right) that crosses each band in
    between through the free gap nearest to the straight line."""
    x0, x1, y1 = start[0], end[0], end[1]
    points = [start]
    px, py = start
    for band in bands:
        if band.left >= x1:
            break
        if band.right <= x0:
            continue
        gaps = band.free(skip)
        if not gaps:
            continue
        lo_x, hi_x = max(band.left, x0), min(band.right, x1)
        mid = (lo_x + hi_x) / 2
        ideal = py + (y1 - py) * (mid - px) / (x1 - px) if x1 > px else py
        # a band that starts before the line (or ends after it) must be
        # crossed at the height the line enters (or leaves) it
        needs = [y for cut, y in ((band.left < x0, py), (band.right > x1, y1)) if cut]
        if needs:
            fits = [g for g in gaps if all(g[0] <= y <= g[1] for y in needs)]
            gaps = fits or [g for g in gaps if g[0] <= needs[0] <= g[1]] or gaps
        y = _nearest(gaps, ideal)
        points += [(lo_x, y), (hi_x, y)]
        px, py = hi_x, y
    points.append(end)
    out = [points[0]]
    for p in points[1:]:
        if p != out[-1]:
            out.append(p)
    return out


def _free(blocks: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The gaps around *blocks* (sorted) on a line, kept ``CLEARANCE`` away
    from them; none when there is no block."""
    if not blocks:
        return []
    out = []
    low = float("-inf")
    for top, bottom in blocks:
        if top - CLEARANCE > low:
            out.append((low, top - CLEARANCE))
        low = max(low, bottom + CLEARANCE)
    out.append((low, float("inf")))
    return out


def _nearest(gaps: list[tuple[float, float]], y: float) -> float:
    """The height nearest *y* inside one of *gaps* (sorted, apart)."""
    k = bisect.bisect_right(gaps, (y, float("inf"))) - 1
    if k >= 0 and y <= gaps[k][1]:
        return y
    above = gaps[k][1] if k >= 0 else float("-inf")
    below = gaps[k + 1][0] if k + 1 < len(gaps) else float("inf")
    return above if y - above <= below - y else below


def _halfway(points: list[Point]) -> Point:
    lengths = [
        (
            abs(bx - ax) + abs(by - ay)
            if ax == bx or ay == by
            else ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        )
        for (ax, ay), (bx, by) in zip(points, points[1:])
    ]
    rest = sum(lengths) / 2
    for (ax, ay), (bx, by), length in zip(points, points[1:], lengths):
        if rest <= length and length:
            t = rest / length
            return (ax + (bx - ax) * t, ay + (by - ay) * t)
        rest -= length
    return points[-1]


def _label_gap(
    bracket: _Bracket, nodes: list[tuple[float, float, str]], used: list[float]
) -> float:
    """Where a bracket's label goes: the gap between two nodes of its band
    nearest the bracket's middle, one no other label there took if it can."""
    middle = (bracket.top + bracket.bottom) / 2
    gaps = [
        (bottom + top) / 2
        for (_, bottom, _), (top, _, _) in zip(nodes, nodes[1:])
        if bracket.top <= (bottom + top) / 2 <= bracket.bottom
    ]
    if not gaps:
        return middle
    free = [y for y in gaps if y not in used] or gaps
    return min(free, key=lambda y: abs(y - middle))


def _normalized(
    rects: dict[str, Rect],
    boxes: list[GroupBox],
    routes: dict[int, list[Point]],
    labels: dict[int, Point],
    bundles: list[Bundle],
) -> Layout:
    """Move everything so the top-left corner is (0, 0)."""
    points = [p for r in routes.values() for p in r]
    points += [p for b in bundles for p in b.points]
    xs = [x for x, _ in points] + [x for b in boxes for x in (b.rect.x, b.rect.right)]
    ys = [y for _, y in points] + [y for b in boxes for y in (b.rect.y, b.rect.bottom)]
    dx, dy = -min(xs), -min(ys)

    def move(p: Point) -> Point:
        return (p[0] + dx, p[1] + dy)

    for box in boxes:
        box.rect = box.rect.moved(dx, dy)
    for bundle in bundles:
        bundle.points = [move(p) for p in bundle.points]
    return Layout(
        {n: r.moved(dx, dy) for n, r in rects.items()},
        boxes,
        {i: [move(p) for p in r] for i, r in routes.items()},
        {i: move(p) for i, p in labels.items()},
        bundles,
        (max(xs) + dx, max(ys) + dy),
    )
