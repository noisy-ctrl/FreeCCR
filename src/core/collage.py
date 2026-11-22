"""The collage maker's layouts (spec/collage-maker.md): pure geometry, no Qt.

Every layout takes the frames' shapes (width / height, as they will be
shown: after crop and turns, and with a film border its outer shape), the
canvas and the settings, plus a seed, and returns cells in output pixels.
The same seed always gives the same arrangement (only `random.Random(seed)`
is used), so Shuffle's history can walk back and forth.
"""
import math
import random
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

FORMATS = (
    ("post", "Post 4:5", 1080, 1350),
    ("square", "Square", 1080, 1080),
    ("landscape", "Landscape 1.91:1", 1080, 566),
    ("story", "Story / Reel 9:16", 1080, 1920),
    ("custom", "Custom", None, None),
)
LAYOUTS = (("mosaic", "Mosaic"), ("carousel", "Carousel"), ("triptych", "Triptych"),
           ("strip", "Film strip"), ("tear", "Tear"))
CAROUSEL_STYLES = (("varied", "Varied"), ("aligned", "Aligned height"),
                   ("full", "Full height"), ("posts", "Each post its own"))
MIN_SIDE, MAX_SIDE = 200, 8000
SEAM_SHARE = 0.30          # varied / aligned: a frame crosses a post's edge with this much a side
SLIVER_SHARE = 0.12        # full height: never less than this of a frame on a post ...
SLIVER_PX = 60             # ... nor fewer pixels
FULL_HEIGHT = 0.95         # full height: the frames' height, share of the post's
MAX_PARTITION = 12         # justified rows: every split up to this many frames
NEAR_BEST = 0.35           # the seed picks among splits this close to the best


@dataclass
class Cell:
    index: int                                # which member
    x: float
    y: float
    w: float
    h: float
    trim: Tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # of the frame, shares

    def rect(self):
        return (self.x, self.y, self.w, self.h)

    def right(self):
        return self.x + self.w

    def bottom(self):
        return self.y + self.h


@dataclass
class Arrangement:
    cells: List[Cell]
    width: int                                # the whole canvas (all posts side by side)
    height: int
    posts: int = 1
    band: Optional[Tuple[float, float]] = None   # tear: the band's top and bottom
    worst_change: float = 0.0                 # the largest crop change made (share)
    strips: Optional[List[List[int]]] = None  # film strip: each strip's cells, in order
    strip_dir: str = ""                       # film strip: "down" or "across"


@dataclass
class Settings:
    width: int = 1080
    height: int = 1350
    layout: str = "mosaic"
    style: str = "varied"                     # carousel style
    posts: int = 3
    height_share: float = FULL_HEIGHT         # full height
    gap: int = 14
    respect: bool = True
    variance: float = 0.06                    # crop change allowed with respect off
    frames: int = 3                           # triptych
    direction: str = "across"                 # triptych: across / down
    margin: Optional[int] = None              # canvas edge; None = the gap
    piece_aspect: float = 35.0 / 38.0         # film strip: a frame's piece (w / h) running down

    @property
    def edge(self):
        return self.gap if self.margin is None else self.margin


# --------------------------------------------------------------------------- #
def cover_trim(frame_aspect: float, cell_aspect: float) -> Tuple[float, float, float, float]:
    """The centred part of a frame that fills a cell of another shape."""
    if cell_aspect > frame_aspect:            # cell wider: trim top and bottom
        h = frame_aspect / cell_aspect
        return (0.0, (1 - h) / 2, 1.0, h)
    w = cell_aspect / frame_aspect
    return ((1 - w) / 2, 0.0, w, 1.0)


def _partitions(n: int):
    for mask in range(1 << (n - 1)):
        rows, cur = [], [0]
        for i in range(1, n):
            if mask >> (i - 1) & 1:
                rows.append(cur)
                cur = [i]
            else:
                cur.append(i)
        rows.append(cur)
        yield rows


def _greedy_rows(aspects, W, H, gap):
    """Rows for many frames: as many rows as fill the height, filled in order."""
    n = len(aspects)
    total = sum(aspects)
    best = None
    for r in range(1, n + 1):
        target = total / r
        rows, cur, acc = [], [], 0.0
        for i, a in enumerate(aspects):
            cur.append(i)
            acc += a
            if acc >= target and len(rows) < r - 1:
                rows.append(cur)
                cur, acc = [], 0.0
        if cur:
            rows.append(cur)
        hs = [(W - gap * (len(x) - 1)) / sum(aspects[i] for i in x) for x in rows]
        fit = abs(math.log((sum(hs) + gap * (len(rows) - 1)) / H))
        if best is None or fit < best[0]:
            best = (fit, rows)
    return best[1]


def justified(aspects: Sequence[float], x0, y0, W, H, gap, rng: random.Random,
              respect=True, cap=0.06, top=5) -> Tuple[List[Cell], float]:
    """Rows filling the width, the split chosen so they fill the height with
    rows of similar height (no lone giant); the seed picks among the
    near-best splits. Respect: every frame keeps its shape (the slack is
    centred). Otherwise rows stretch to fill, a crop changing by at most
    `cap`. Returns (cells, the largest crop change)."""
    n = len(aspects)
    if n == 0:
        return [], 0.0
    if n <= MAX_PARTITION:
        scored = []
        for rows in _partitions(n):
            hs = [(W - gap * (len(r) - 1)) / sum(aspects[i] for i in r) for r in rows]
            total = sum(hs) + gap * (len(rows) - 1)
            lh = [math.log(h) for h in hs]
            mean = sum(lh) / len(lh)
            spread = math.sqrt(sum((v - mean) ** 2 for v in lh) / len(lh))
            lone = sum(1 for r in rows if len(r) == 1) if n >= 5 else 0
            fit = abs(math.log(total / H))
            if not respect:
                fit = max(0.0, fit - math.log(1 + cap))
            scored.append((3.0 * fit + 2.0 * spread + 0.25 * lone, rows))
        scored.sort(key=lambda t: t[0])
        near = [t for t in scored[:top] if t[0] <= scored[0][0] + NEAR_BEST]
        rows = near[rng.randrange(len(near))][1]
    else:
        rows = _greedy_rows(aspects, W, H, gap)
    hs = [(W - gap * (len(r) - 1)) / sum(aspects[i] for i in r) for r in rows]
    fill = (H - gap * (len(rows) - 1)) / sum(hs)
    cells: List[Cell] = []
    if respect:
        k = min(1.0, fill)
        used = sum(hs) * k + gap * (len(rows) - 1)
        y = y0 + (H - used) / 2
        for r, h in zip(rows, hs):
            hh = h * k
            row_w = sum(aspects[i] * hh for i in r) + gap * (len(r) - 1)
            x = x0 + (W - row_w) / 2
            for i in r:
                w = aspects[i] * hh
                cells.append(Cell(i, x, y, w, hh))
                x += w + gap
            y += hh + gap
        return cells, 0.0
    f = min(max(fill, 1 / (1 + cap)), 1 + cap)
    used = sum(hs) * f + gap * (len(rows) - 1)
    k = (H - gap * (len(rows) - 1)) / (sum(hs) * f) if used > H else 1.0
    y = y0 + max(0.0, (H - used) / 2)
    worst = 0.0
    for r, h in zip(rows, hs):
        hh = h * f * k
        free = (W - gap * (len(r) - 1)) * k
        x = x0 + (W - free - gap * (len(r) - 1)) / 2
        s = sum(aspects[i] for i in r)
        for i in r:
            w = free * aspects[i] / s
            cell_a = w / hh
            worst = max(worst, abs(cell_a / aspects[i] - 1))
            cells.append(Cell(i, x, y, w, hh, cover_trim(aspects[i], cell_a)))
            x += w + gap
        y += hh + gap
    return cells, worst


# --------------------------------------------------------------------------- #
def _shift_from(cols, k, dx):
    for col in cols[k:]:
        for c in col:
            c.x += dx


def _seam_rule(cols, posts, W, gap, min_share=SEAM_SHARE):
    """A frame crossing a post's edge by less than `min_share` on a side moves
    on: mostly right of it, it starts past the edge; mostly left, it moves to
    cross at its middle."""
    for s in range(1, posts):
        seam = s * W
        for k, col in enumerate(cols):
            hit = next((c for c in col if c.x < seam < c.right()), None)
            if hit is None:
                continue
            left = (seam - hit.x) / hit.w
            if left < min_share:
                _shift_from(cols, k, seam + gap * 1.5 - hit.x)
            elif left > 1 - min_share:
                _shift_from(cols, k, (seam - hit.x) - hit.w * 0.5)
            break


def _fit_run(cols, posts, W, H, edge, gap, seams, rounds=8):
    """Scale a run of columns (about the middle height) or open its gaps so
    it ends at the last post's margin, applying `seams` after each step."""
    for _ in range(rounds):
        end = max(c.right() for col in cols for c in col)
        k = (posts * W - 2 * edge) / max(end - edge, 1e-6)
        if abs(k - 1) < 0.01:
            break
        if k < 1:
            mid = H / 2.0
            for col in cols:
                for c in col:
                    c.x = edge + (c.x - edge) * k
                    c.y = mid + (c.y - mid) * k
                    c.w *= k
                    c.h *= k
        else:
            extra = posts * W - edge - end
            for i, col in enumerate(cols):
                for c in col:
                    c.x += extra * i / max(1, len(cols) - 1)
        seams()


def carousel_varied(aspects, posts, W, H, gap, edge, rng) -> List[Cell]:
    """An airy run across the posts: columns of one or two frames, sizes and
    heights varied by the seed, white space kept (the user's reference)."""
    n = len(aspects)
    cols_idx, i = [], 0
    while i < n:
        if i + 1 < n and rng.random() < 0.7:
            cols_idx.append([i, i + 1])
            i += 2
        else:
            cols_idx.append([i])
            i += 1
    usable = H - 2 * edge
    cols, x = [], float(edge)
    for c in cols_idx:
        if len(c) == 2:
            a1, a2 = aspects[c[0]], aspects[c[1]]
            ht = usable * rng.uniform(0.8, 1.0)
            w = (ht - gap) / (1 / a1 + 1 / a2)
            h1, h2 = w / a1, w / a2
            t = edge + (usable - (h1 + h2 + gap)) * rng.random()
            col = [Cell(c[0], x, t, w, h1), Cell(c[1], x, t + h1 + gap, w, h2)]
        else:
            j = c[0]
            share = rng.uniform(0.62, 0.95) if aspects[j] < 1 else rng.uniform(0.45, 0.68)
            h = usable * share
            w = aspects[j] * h
            t = edge + (usable - h) * rng.choice([0.0, 1.0, rng.random()])
            col = [Cell(j, x, t, w, h)]
        cols.append(col)
        x = max(cc.right() for cc in col) + gap * rng.uniform(1.0, 2.6)
    _fit_run(cols, posts, W, H, edge, gap, lambda: _seam_rule(cols, posts, W, gap))
    return [c for col in cols for c in col]


def carousel_aligned(aspects, posts, W, H, gap, edge, rng) -> List[Cell]:
    """One band of equal-height frames running through the posts."""
    n = len(aspects)
    room = posts * W - 2 * edge - gap * (n - 1)
    h = min(room / max(sum(aspects), 1e-6), 0.9 * (H - 2 * edge))
    y = (H - h) / 2
    cols, x = [], float(edge)
    for i, a in enumerate(aspects):
        cols.append([Cell(i, x, y, a * h, h)])
        x += a * h + gap
    _fit_run(cols, posts, W, H, edge, gap, lambda: _seam_rule(cols, posts, W, gap), rounds=4)
    return [c for col in cols for c in col]


def full_height_posts(aspects, W, H, gap, share=FULL_HEIGHT, edge=None) -> int:
    """How many posts a Full height row needs."""
    edge = gap if edge is None else edge
    h = share * H
    length = 2 * edge + sum(a * h for a in aspects) + gap * max(0, len(aspects) - 1)
    return max(1, int(math.ceil(length / W - 1e-9)))


def _no_slivers(cells, W, gap, rounds=200):
    """Full height: a frame never shows less than SLIVER_SHARE of itself (or
    SLIVER_PX) on a post. The smallest move does it: a sliver on the left of
    an edge (the frame starts just before it) moves the frame to start just
    past the edge; a sliver on the right (it ends just past the edge) moves
    it on until that part is big enough. Everything after it moves with it.
    Returns whether anything moved."""
    moved = False
    for _ in range(rounds):
        hit = False
        for k, c in enumerate(cells):
            limit = max(SLIVER_SHARE * c.w, SLIVER_PX)
            p = int(c.x // W) + 1
            while p * W < c.right():
                seam = p * W
                left, right = seam - c.x, c.right() - seam
                dx = 0.0
                if 0 < left < limit:
                    dx = left + gap / 2.0
                elif 0 < right < limit:
                    dx = limit - right + 1.0
                if dx:
                    for cc in cells[k:]:
                        cc.x += dx
                    moved = hit = True
                    break
                p += 1
            if hit:
                break
        if not hit:
            break
    return moved


def carousel_full(aspects, W, H, gap, share=FULL_HEIGHT, edge=None) -> Tuple[List[Cell], int]:
    """Full height (the user, 2026-11-20): every frame `share` of the post's
    height, centred, in one row through the posts, split between swipes but
    never as a sliver; as many posts as the row needs, the last post's spare
    room spread into the gaps. Returns (cells, posts)."""
    edge = gap if edge is None else edge
    h = share * H
    y = (H - h) / 2
    cells, x = [], float(edge)
    for i, a in enumerate(aspects):
        cells.append(Cell(i, x, y, a * h, h))
        x += a * h + gap
    if not cells:
        return cells, 1
    _no_slivers(cells, W, gap)
    end = cells[-1].right()
    posts = max(1, int(math.ceil((end + edge) / W - 1e-9)))
    spare = posts * W - edge - end
    if spare > 1 and len(cells) > 1:
        for part in (1.0, 0.5, 0.25):          # spread the spare into the gaps
            trial = [Cell(c.index, c.x + spare * part * i / (len(cells) - 1), c.y, c.w, c.h)
                     for i, c in enumerate(cells)]
            probe = [Cell(c.index, c.x, c.y, c.w, c.h) for c in trial]
            if not _no_slivers(probe, W, gap):
                cells = trial
                break
    return cells, posts


def _carousel_faults(cells, posts, W, H, tol=2.0):
    """How badly a varied / aligned run breaks its rules: frames outside the
    canvas (weighted heavily), then frames crossing an edge too little."""
    bad = 0
    for c in cells:
        if c.x < -tol or c.y < -tol or c.right() > posts * W + tol or c.bottom() > H + tol:
            bad += 10
        for p in range(1, posts):
            seam = p * W
            if c.x < seam < c.right():
                share = (seam - c.x) / c.w
                if share < SEAM_SHARE - 0.02 or share > 1 - SEAM_SHARE + 0.02:
                    bad += 1
    return bad


def carousel_posts(aspects, posts, W, H, gap, edge, rng, respect, cap):
    """Each post its own mosaic: the frames dealt out in order."""
    n = len(aspects)
    cells, worst = [], 0.0
    per = [n // posts + (1 if p < n % posts else 0) for p in range(posts)]
    start = 0
    for p, k in enumerate(per):
        idx = list(range(start, start + k))
        start += k
        if not idx:
            continue
        cs, w = justified([aspects[i] for i in idx], p * W + edge, edge, W - 2 * edge,
                          H - 2 * edge, gap, rng, respect, cap)
        worst = max(worst, w)
        for c in cs:
            c.index = idx[c.index]
            cells.append(c)
    return cells, worst


def triptych(aspects, W, H, gap, direction="across", scale=0.86) -> List[Cell]:
    """The frames in a row (or a column), one height (or width), centred,
    filling `scale` of the canvas."""
    n = len(aspects)
    if n == 0:
        return []
    if direction == "down":
        w = min(W * scale, (H * scale - gap * (n - 1)) / sum(1 / a for a in aspects))
        total = sum(w / a for a in aspects) + gap * (n - 1)
        y = (H - total) / 2
        out = []
        for i, a in enumerate(aspects):
            out.append(Cell(i, (W - w) / 2, y, w, w / a))
            y += w / a + gap
        return out
    h = min(H * scale, (W * scale - gap * (n - 1)) / sum(aspects))
    total = sum(a * h for a in aspects) + gap * (n - 1)
    x = (W - total) / 2
    out = []
    for i, a in enumerate(aspects):
        out.append(Cell(i, x, (H - h) / 2, a * h, h))
        x += a * h + gap
    return out


STRIP_SCALE = 0.86         # film strip: the strips fill this much of the canvas


def film_strips(n, piece_aspect, W, H, gap, scale=STRIP_SCALE):
    """Film strip (spec/collage-maker.md, revision 2026-11-22): the frames as
    pieces of ONE film, laid end to end in strips that run along the
    canvas's long side (down a portrait or square canvas, across a landscape
    one), as many strips side by side as gives the biggest frames (fewer when
    within 2 %), `gap` between strips, centred, every strip starting level.
    `piece_aspect` is a piece's w / h with the film running down. Returns
    (cells, strips = each strip's cell numbers in order, direction)."""
    if n == 0:
        return [], [], "down"
    down = H >= W
    pa = piece_aspect if down else 1.0 / piece_aspect
    best = None
    for m in range(1, n + 1):                  # pieces a strip
        k = -(-n // m)                         # strips
        if down:
            ph = min(H * scale / m, (W * scale - gap * (k - 1)) / k / pa)
            pw = ph * pa
        else:
            pw = min(W * scale / m, (H * scale - gap * (k - 1)) / k * pa)
            ph = pw / pa
        if pw <= 0 or ph <= 0:
            continue
        if best is None or pw * ph > best[0] * 1.02:
            best = (pw * ph, m, k, pw, ph)
    if best is None:
        return [], [], "down" if down else "across"
    _area, m, k, pw, ph = best
    cells, strips = [], []
    if down:
        x0, y0 = (W - (k * pw + (k - 1) * gap)) / 2, (H - m * ph) / 2
    else:
        x0, y0 = (W - m * pw) / 2, (H - (k * ph + (k - 1) * gap)) / 2
    for j in range(k):
        run = []
        for q in range(m):
            i = j * m + q
            if i >= n:
                break
            if down:
                c = Cell(i, x0 + j * (pw + gap), y0 + q * ph, pw, ph)
            else:
                c = Cell(i, x0 + q * pw, y0 + j * (ph + gap), pw, ph)
            run.append(len(cells))
            cells.append(c)
        strips.append(run)
    return cells, strips, "down" if down else "across"


def tear_band(H, rng) -> Tuple[float, float]:
    """Where a torn band sits: a third to a half of the height, placed by the seed."""
    t = H * rng.uniform(0.28, 0.45)
    b = t + H * rng.uniform(0.22, 0.32)
    return t, min(b, H * 0.85)


# --------------------------------------------------------------------------- #
def arrange(aspects: Sequence[float], st: Settings, seed: int) -> Arrangement:
    """The layout for `st` and `seed` (deterministic)."""
    rng = random.Random(seed)
    W, H, gap, edge = int(st.width), int(st.height), int(st.gap), int(st.edge)
    aspects = [max(0.05, float(a)) for a in aspects]
    order = list(range(len(aspects)))
    if st.layout in ("mosaic", "carousel"):
        rng.shuffle(order)                     # Shuffle moves frames about, too
    shuffled = [aspects[i] for i in order]

    def unshuffle(cells):
        for c in cells:
            c.index = order[c.index]
        return cells

    if st.layout == "mosaic":
        cells, worst = justified(shuffled, edge, edge, W - 2 * edge, H - 2 * edge, gap, rng,
                                 st.respect, st.variance)
        return Arrangement(unshuffle(cells), W, H, 1, worst_change=worst)
    if st.layout == "carousel":
        if st.style == "full":
            cells, posts = carousel_full(shuffled, W, H, gap, st.height_share, edge)
            return Arrangement(unshuffle(cells), posts * W, H, posts)
        posts = max(1, int(st.posts))
        if st.style in ("aligned", "varied"):
            # fitting the run to the posts and the seam rule can pull against
            # each other: deal again (same seed, so the same result every
            # time) until both hold, else take the try that broke least
            make = carousel_aligned if st.style == "aligned" else carousel_varied
            best = None
            for attempt in range(16):
                if attempt:
                    rng.shuffle(order)
                cells = make([aspects[i] for i in order], posts, W, H, gap, edge, rng)
                bad = _carousel_faults(cells, posts, W, H)
                if best is None or bad < best[0]:
                    best = (bad, [Cell(order[c.index], c.x, c.y, c.w, c.h, c.trim)
                                  for c in cells])
                if bad == 0:
                    break
            return Arrangement(best[1], posts * W, H, posts)
        cells, worst = carousel_posts(shuffled, posts, W, H, gap, edge, rng,
                                      st.respect, st.variance)
        return Arrangement(unshuffle(cells), posts * W, H, posts, worst_change=worst)
    if st.layout == "triptych":
        k = max(1, min(int(st.frames), len(aspects)))
        pick = sorted(rng.sample(range(len(aspects)), k)) if len(aspects) > k else list(range(k))
        cells = triptych([aspects[i] for i in pick], W, H, gap, st.direction)
        for c in cells:
            c.index = pick[c.index]
        return Arrangement(cells, W, H, 1)
    if st.layout == "tear":
        if not aspects:
            return Arrangement([], W, H, 1)
        a, b = (rng.sample(range(len(aspects)), 2) if len(aspects) > 1 else (0, 0))
        top, bottom = tear_band(H, rng)
        back = Cell(a, 0, 0, W, H, cover_trim(aspects[a], W / H))
        band = Cell(b, 0, top, W, bottom - top, cover_trim(aspects[b], W / (bottom - top)))
        return Arrangement([back, band], W, H, 1, band=(top, bottom))
    # film strip: the frames end to end on one film, in the members' order
    cells, strips, direction = film_strips(len(aspects), st.piece_aspect, W, H, gap)
    return Arrangement(cells, W, H, 1, strips=strips, strip_dir=direction)


class SeedHistory:
    """Shuffle's history: ◀ / ▶ walk it, Shuffle adds a seed after the one
    on show (dropping any ahead, as an editor's undo does)."""

    def __init__(self, first: int = 1, rng: Optional[random.Random] = None):
        self.seeds = [int(first)]
        self.at = 0
        self._rng = rng or random.Random()

    @property
    def seed(self) -> int:
        return self.seeds[self.at]

    def shuffle(self) -> int:
        del self.seeds[self.at + 1:]
        s = self._rng.randrange(1, 2 ** 31)
        self.seeds.append(s)
        self.at += 1
        return s

    def back(self) -> bool:
        if self.at > 0:
            self.at -= 1
            return True
        return False

    def forward(self) -> bool:
        if self.at < len(self.seeds) - 1:
            self.at += 1
            return True
        return False

    def label(self) -> str:
        return f"arrangement {self.at + 1} of {len(self.seeds)}"
