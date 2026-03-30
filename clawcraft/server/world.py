"""Procedural world generation and grid data structures."""

import math
import random
from collections import deque
from enum import Enum


class CellType(str, Enum):
    EMPTY = "empty"
    TREE = "tree"
    ROCK = "rock"
    WATER = "water"
    WOOD_BLOCK = "wood_block"
    STONE_BLOCK = "stone_block"


IMPASSABLE = {CellType.WATER, CellType.ROCK, CellType.TREE, CellType.WOOD_BLOCK, CellType.STONE_BLOCK}

MAP_SIZE = 128

# Block/resource HP
WOOD_BLOCK_HP = 3
STONE_BLOCK_HP = 7
TREE_TOTAL_WOOD = 10
ROCK_TOTAL_STONE = 10
HARVESTS_PER_WOOD = 3
HARVESTS_PER_STONE = 5


class Cell:
    __slots__ = ("type", "hp", "resource_remaining", "harvest_progress")

    def __init__(self, cell_type: CellType):
        self.type = cell_type
        self.hp = 0
        self.resource_remaining = 0
        self.harvest_progress: dict[str, int] = {}  # agent_id -> consecutive harvests

        if cell_type == CellType.TREE:
            self.resource_remaining = TREE_TOTAL_WOOD
        elif cell_type == CellType.ROCK:
            self.resource_remaining = ROCK_TOTAL_STONE
        elif cell_type == CellType.WOOD_BLOCK:
            self.hp = WOOD_BLOCK_HP
        elif cell_type == CellType.STONE_BLOCK:
            self.hp = STONE_BLOCK_HP

    def to_dict(self) -> dict:
        d: dict = {"type": self.type.value}
        if self.type in (CellType.WOOD_BLOCK, CellType.STONE_BLOCK):
            d["hp"] = self.hp
        if self.type in (CellType.TREE, CellType.ROCK):
            d["resource_remaining"] = self.resource_remaining
        return d


# ---------------------------------------------------------------------------
# Value noise for organic terrain generation
# ---------------------------------------------------------------------------

class ValueNoise2D:
    """Seedable 2D value noise with smoothstep interpolation."""

    def __init__(self, seed: int, grid_size: int = 16):
        self._seed = seed
        self.grid_size = grid_size
        self._cache: dict[tuple[int, int], float] = {}

    def _grid_value(self, gx: int, gy: int) -> float:
        key = (gx, gy)
        if key not in self._cache:
            self._cache[key] = random.Random(hash((self._seed, gx, gy))).random()
        return self._cache[key]

    @staticmethod
    def _smoothstep(t: float) -> float:
        return t * t * (3.0 - 2.0 * t)

    def sample(self, x: float, y: float) -> float:
        gx = x / self.grid_size
        gy = y / self.grid_size
        x0 = int(math.floor(gx))
        y0 = int(math.floor(gy))
        sx = self._smoothstep(gx - x0)
        sy = self._smoothstep(gy - y0)
        top = self._grid_value(x0, y0) + sx * (self._grid_value(x0 + 1, y0) - self._grid_value(x0, y0))
        bot = self._grid_value(x0, y0 + 1) + sx * (self._grid_value(x0 + 1, y0 + 1) - self._grid_value(x0, y0 + 1))
        return top + sy * (bot - top)


def _fractal_noise(noise: ValueNoise2D, x: float, y: float,
                   octaves: int = 4, persistence: float = 0.5, lacunarity: float = 2.0) -> float:
    value = 0.0
    amplitude = 1.0
    frequency = 1.0
    max_val = 0.0
    for _ in range(octaves):
        value += noise.sample(x * frequency, y * frequency) * amplitude
        max_val += amplitude
        amplitude *= persistence
        frequency *= lacunarity
    return value / max_val


def _noise_field(seed: int, grid_size: int, octaves: int = 3,
                 persistence: float = 0.5) -> list[list[float]]:
    """Generate a MAP_SIZE x MAP_SIZE noise field, values in 0..1."""
    noise = ValueNoise2D(seed=seed, grid_size=grid_size)
    field = []
    for y in range(MAP_SIZE):
        row = []
        for x in range(MAP_SIZE):
            row.append(_fractal_noise(noise, x, y, octaves=octaves, persistence=persistence))
        field.append(row)
    return field


def _percentile_threshold(field: list[list[float]], coverage: float) -> float:
    """Find the threshold value such that `coverage` fraction of cells are below it."""
    flat = sorted(v for row in field for v in row)
    idx = max(0, min(int(coverage * len(flat)), len(flat) - 1))
    return flat[idx]


def _remove_small_regions(water: list[list[bool]], min_size: int = 8) -> list[list[bool]]:
    """Remove connected water regions smaller than min_size via flood fill."""
    visited = [[False] * MAP_SIZE for _ in range(MAP_SIZE)]
    result = [row[:] for row in water]
    for y in range(MAP_SIZE):
        for x in range(MAP_SIZE):
            if visited[y][x] or not water[y][x]:
                continue
            region: list[tuple[int, int]] = []
            q: deque[tuple[int, int]] = deque([(x, y)])
            visited[y][x] = True
            while q:
                cx, cy = q.popleft()
                region.append((cx, cy))
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < MAP_SIZE and 0 <= ny < MAP_SIZE and not visited[ny][nx] and water[ny][nx]:
                        visited[ny][nx] = True
                        q.append((nx, ny))
            if len(region) < min_size:
                for rx, ry in region:
                    result[ry][rx] = False
    return result


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------

def generate_grid(seed: int | None = None) -> list[list[Cell]]:
    """Generate a MAP_SIZE x MAP_SIZE grid with procgen terrain."""
    rng = random.Random(seed)
    grid = [[Cell(CellType.EMPTY) for _ in range(MAP_SIZE)] for _ in range(MAP_SIZE)]

    # --- Water (~5%) via noise thresholding ---
    # Large grid_size produces a few big connected lakes, not scattered dots.
    # Low octaves keep shorelines smooth and organic.
    water_field = _noise_field(seed=rng.randint(0, 2**31), grid_size=32,
                               octaves=3, persistence=0.4)
    water_thresh = _percentile_threshold(water_field, 0.10)
    water_mask = [[water_field[y][x] <= water_thresh for x in range(MAP_SIZE)] for y in range(MAP_SIZE)]
    # water_mask = _remove_small_regions(water_mask, min_size=8)
    for y in range(MAP_SIZE):
        for x in range(MAP_SIZE):
            if water_mask[y][x]:
                grid[y][x] = Cell(CellType.WATER)

    # --- Base trees (~15%) with small random clusters ---
    target_trees = int(MAP_SIZE * MAP_SIZE * 0.15)
    placed = 0
    while placed < target_trees:
        x, y = rng.randint(0, MAP_SIZE - 1), rng.randint(0, MAP_SIZE - 1)
        if grid[y][x].type == CellType.EMPTY:
            grid[y][x] = Cell(CellType.TREE)
            placed += 1
            for _ in range(rng.randint(0, 3)):
                nx = x + rng.randint(-2, 2)
                ny = y + rng.randint(-2, 2)
                if 0 <= nx < MAP_SIZE and 0 <= ny < MAP_SIZE and grid[ny][nx].type == CellType.EMPTY:
                    grid[ny][nx] = Cell(CellType.TREE)
                    placed += 1

    # --- Dense forest layer on top ---
    # Separate noise field defines forest density zones (~15% of map).
    # Inside dense zones, empty cells get trees at high probability.
    forest_field = _noise_field(seed=rng.randint(0, 2**31), grid_size=24,
                                octaves=3, persistence=0.5)
    dense_thresh = _percentile_threshold(forest_field, 1.0 - 0.15)  # top 15%
    for y in range(MAP_SIZE):
        for x in range(MAP_SIZE):
            if grid[y][x].type != CellType.EMPTY:
                continue
            nv = forest_field[y][x]
            if nv >= dense_thresh:
                # Smooth density falloff at edges
                blend = min(1.0, (nv - dense_thresh) / 0.08)
                chance = 0.3 + 0.4 * blend  # 30-70% fill
                if rng.random() < chance:
                    grid[y][x] = Cell(CellType.TREE)

    # --- Rocks (~8%) with small clusters ---
    target_rocks = int(MAP_SIZE * MAP_SIZE * 0.08)
    placed = 0
    while placed < target_rocks:
        x, y = rng.randint(0, MAP_SIZE - 1), rng.randint(0, MAP_SIZE - 1)
        if grid[y][x].type == CellType.EMPTY:
            grid[y][x] = Cell(CellType.ROCK)
            placed += 1
            for _ in range(rng.randint(0, 2)):
                nx = x + rng.randint(-1, 1)
                ny = y + rng.randint(-1, 1)
                if 0 <= nx < MAP_SIZE and 0 <= ny < MAP_SIZE and grid[ny][nx].type == CellType.EMPTY:
                    grid[ny][nx] = Cell(CellType.ROCK)
                    placed += 1

    # --- Fortresses (red top-left, blue bottom-right) ---
    fortresses = _generate_fortresses(grid, rng)

    return grid, fortresses


def _rect_overlaps_water(grid: list[list[Cell]], x1: int, y1: int, x2: int, y2: int) -> bool:
    """Check if any cell in the rectangle is water."""
    for y in range(max(0, y1), min(MAP_SIZE, y2 + 1)):
        for x in range(max(0, x1), min(MAP_SIZE, x2 + 1)):
            if grid[y][x].type == CellType.WATER:
                return True
    return False


def _generate_fortresses(
    grid: list[list[Cell]], rng: random.Random
) -> dict[str, tuple[int, int, int, int]]:
    """Build a red (north) and blue (south) fortress via room accretion."""
    margin = 4
    rects: dict[str, tuple[int, int, int, int]] = {}

    for color, y_lo, y_hi in [("red", margin, MAP_SIZE // 3),
                               ("blue", MAP_SIZE * 2 // 3, MAP_SIZE - margin)]:
        # Bounding rectangle for the fortress
        bound_w = rng.randint(22, 28)
        bound_h = rng.randint(18, 24)

        # Try positions until we find one not overlapping water
        for _ in range(50):
            bx1 = rng.randint(margin, MAP_SIZE - bound_w - margin)
            by1 = rng.randint(y_lo, max(y_lo, y_hi - bound_h))
            bx2, by2 = bx1 + bound_w - 1, by1 + bound_h - 1
            if by2 >= MAP_SIZE - margin:
                continue
            if not _rect_overlaps_water(grid, bx1 - 2, by1 - 2, bx2 + 2, by2 + 2):
                break

        rects[color] = (bx1, by1, bx2, by2)
        _build_fortress_accretion(grid, rng, bx1, by1, bx2, by2)

    return rects


# ---------------------------------------------------------------------------
# Room-accretion fortress builder
# ---------------------------------------------------------------------------

_Room = tuple[int, int, int, int]  # (x1, y1, x2, y2) inclusive, walls included


def _rooms_overlap(a: _Room, b: _Room, margin: int = 1) -> bool:
    """Check if two rooms overlap (with margin buffer on all sides of b)."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return not (ax2 + margin < bx1 or bx2 + margin < ax1 or
                ay2 + margin < by1 or by2 + margin < ay1)


def _shared_wall_segment(
    source: _Room, new: _Room, side: str
) -> list[tuple[int, int]]:
    """Return the cells along the shared wall where a door can go.

    Excludes corners (first/last cell) to avoid diagonal-only access.
    """
    sx1, sy1, sx2, sy2 = source
    nx1, ny1, nx2, ny2 = new
    cells = []

    if side == "east":
        # Shared column is sx2 (= nx1). Overlap in y.
        y_lo = max(sy1, ny1) + 1
        y_hi = min(sy2, ny2) - 1
        x = sx2
        for y in range(y_lo, y_hi + 1):
            cells.append((x, y))
    elif side == "west":
        y_lo = max(sy1, ny1) + 1
        y_hi = min(sy2, ny2) - 1
        x = sx1
        for y in range(y_lo, y_hi + 1):
            cells.append((x, y))
    elif side == "south":
        x_lo = max(sx1, nx1) + 1
        x_hi = min(sx2, nx2) - 1
        y = sy2
        for x in range(x_lo, x_hi + 1):
            cells.append((x, y))
    elif side == "north":
        x_lo = max(sx1, nx1) + 1
        x_hi = min(sx2, nx2) - 1
        y = sy1
        for x in range(x_lo, x_hi + 1):
            cells.append((x, y))

    return cells


def _build_fortress_accretion(
    grid: list[list[Cell]],
    rng: random.Random,
    bx1: int, by1: int, bx2: int, by2: int,
):
    """Build a fortress by accreting rooms within the bounding rect."""
    # Room size ranges (including walls). 1 tile = 1m, so rooms are 4-9m.
    min_room_w, max_room_w = 4, 9
    min_room_h, max_room_h = 4, 8
    min_door_overlap = 3  # minimum shared wall length to place a door

    # --- Clear the bounding area + buffer ---
    for y in range(max(0, by1 - 2), min(MAP_SIZE, by2 + 3)):
        for x in range(max(0, bx1 - 2), min(MAP_SIZE, bx2 + 3)):
            grid[y][x] = Cell(CellType.EMPTY)

    rooms: list[_Room] = []
    doors: list[tuple[int, int]] = []

    # --- First room: centered-ish ---
    rw = rng.randint(min_room_w + 1, max_room_w)
    rh = rng.randint(min_room_h + 1, max_room_h)
    cx = (bx1 + bx2) // 2
    cy = (by1 + by2) // 2
    rx1 = cx - rw // 2 + rng.randint(-2, 2)
    ry1 = cy - rh // 2 + rng.randint(-2, 2)
    # Clamp to bounds
    rx1 = max(bx1, min(rx1, bx2 - rw + 1))
    ry1 = max(by1, min(ry1, by2 - rh + 1))
    first_room = (rx1, ry1, rx1 + rw - 1, ry1 + rh - 1)
    rooms.append(first_room)

    # --- Accrete rooms ---
    max_failures = 150
    max_rooms = 12
    failures = 0

    while failures < max_failures and len(rooms) < max_rooms:
        # Pick a random existing room to expand from
        source = rng.choice(rooms)
        sx1, sy1, sx2, sy2 = source

        # Pick a random side
        side = rng.choice(["north", "south", "east", "west"])

        # Generate candidate room dimensions
        nw = rng.randint(min_room_w, max_room_w)
        nh = rng.randint(min_room_h, max_room_h)

        # Position the new room flush against the chosen side
        if side == "east":
            nx1 = sx2  # share the wall column
            # Random y offset so they overlap vertically
            overlap_range = min(sy2 - sy1 + 1, nh) - min_door_overlap
            if overlap_range < 0:
                failures += 1
                continue
            y_offset = rng.randint(-overlap_range, overlap_range)
            ny1 = sy1 + y_offset
        elif side == "west":
            nx1 = sx1 - nw + 1  # new room's right wall = source's left wall
            overlap_range = min(sy2 - sy1 + 1, nh) - min_door_overlap
            if overlap_range < 0:
                failures += 1
                continue
            y_offset = rng.randint(-overlap_range, overlap_range)
            ny1 = sy1 + y_offset
        elif side == "south":
            ny1 = sy2  # share the wall row
            overlap_range = min(sx2 - sx1 + 1, nw) - min_door_overlap
            if overlap_range < 0:
                failures += 1
                continue
            x_offset = rng.randint(-overlap_range, overlap_range)
            nx1 = sx1 + x_offset
        elif side == "north":
            ny1 = sy1 - nh + 1
            overlap_range = min(sx2 - sx1 + 1, nw) - min_door_overlap
            if overlap_range < 0:
                failures += 1
                continue
            x_offset = rng.randint(-overlap_range, overlap_range)
            nx1 = sx1 + x_offset

        nx2 = nx1 + nw - 1
        ny2 = ny1 + nh - 1
        candidate = (nx1, ny1, nx2, ny2)

        # Validate: must fit within bounding rect
        if nx1 < bx1 or ny1 < by1 or nx2 > bx2 or ny2 > by2:
            failures += 1
            continue

        # Validate: must not overlap any existing room (except sharing one wall with source)
        overlaps = False
        for existing in rooms:
            if existing == source:
                continue
            if _rooms_overlap(candidate, existing, margin=0):
                overlaps = True
                break
        if overlaps:
            failures += 1
            continue

        # Validate: enough shared wall for a door
        wall_segment = _shared_wall_segment(source, candidate, side)
        if len(wall_segment) < 1:
            failures += 1
            continue

        # Place the room
        rooms.append(candidate)
        failures = 0  # reset on success

        # Place door in the shared wall
        door_pos = rng.choice(wall_segment)
        doors.append(door_pos)

    # --- Render rooms to grid ---
    # First pass: draw walls for all rooms (stone outer walls)
    for (rx1, ry1, rx2, ry2) in rooms:
        for x in range(rx1, rx2 + 1):
            for y in (ry1, ry2):
                if 0 <= x < MAP_SIZE and 0 <= y < MAP_SIZE:
                    grid[y][x] = Cell(CellType.STONE_BLOCK)
        for y in range(ry1, ry2 + 1):
            for x in (rx1, rx2):
                if 0 <= x < MAP_SIZE and 0 <= y < MAP_SIZE:
                    grid[y][x] = Cell(CellType.STONE_BLOCK)

    # Second pass: carve interiors
    for (rx1, ry1, rx2, ry2) in rooms:
        for y in range(ry1 + 1, ry2):
            for x in range(rx1 + 1, rx2):
                if 0 <= x < MAP_SIZE and 0 <= y < MAP_SIZE:
                    grid[y][x] = Cell(CellType.EMPTY)

    # Third pass: carve doors (clear the wall cell + one on each side for passage)
    for dx, dy in doors:
        if 0 <= dx < MAP_SIZE and 0 <= dy < MAP_SIZE:
            grid[dy][dx] = Cell(CellType.EMPTY)

    # --- Exterior rectangular wall around the bounding rect ---
    # Expand 2 tiles out from the bounding rect for the outer wall
    wx1, wy1 = bx1 - 2, by1 - 2
    wx2, wy2 = bx2 + 2, by2 + 2

    # Clear the buffer zone between inner rooms and outer wall
    for y in range(max(0, wy1), min(MAP_SIZE, wy2 + 1)):
        for x in range(max(0, wx1), min(MAP_SIZE, wx2 + 1)):
            if 0 <= x < MAP_SIZE and 0 <= y < MAP_SIZE:
                if grid[y][x].type not in (CellType.STONE_BLOCK, CellType.EMPTY):
                    grid[y][x] = Cell(CellType.EMPTY)

    # Draw outer wall (wood)
    for x in range(max(0, wx1), min(MAP_SIZE, wx2 + 1)):
        if 0 <= wy1 < MAP_SIZE:
            grid[wy1][x] = Cell(CellType.WOOD_BLOCK)
        if 0 <= wy2 < MAP_SIZE:
            grid[wy2][x] = Cell(CellType.WOOD_BLOCK)
    for y in range(max(0, wy1), min(MAP_SIZE, wy2 + 1)):
        if 0 <= wx1 < MAP_SIZE:
            grid[y][wx1] = Cell(CellType.WOOD_BLOCK)
        if 0 <= wx2 < MAP_SIZE:
            grid[y][wx2] = Cell(CellType.WOOD_BLOCK)

    # Corner towers (3x3 wood blocks at each corner)
    for cx, cy in [(wx1 - 1, wy1 - 1), (wx1 - 1, wy2 - 1),
                    (wx2 - 1, wy1 - 1), (wx2 - 1, wy2 - 1)]:
        for dx in range(3):
            for dy in range(3):
                tx, ty = cx + dx, cy + dy
                if 0 <= tx < MAP_SIZE and 0 <= ty < MAP_SIZE:
                    grid[ty][tx] = Cell(CellType.WOOD_BLOCK)

    # Mid-wall turrets (small 2x2 bumps at ~1/3 and ~2/3 along each wall)
    wall_w = wx2 - wx1
    wall_h = wy2 - wy1
    for frac in (0.33, 0.66):
        # Top and bottom walls
        tx = wx1 + int(wall_w * frac)
        for dx in range(2):
            for dy in range(2):
                if 0 <= tx + dx < MAP_SIZE:
                    if 0 <= wy1 - 1 + dy < MAP_SIZE:
                        grid[wy1 - 1 + dy][tx + dx] = Cell(CellType.WOOD_BLOCK)
                    if 0 <= wy2 + dy < MAP_SIZE:
                        grid[wy2 + dy][tx + dx] = Cell(CellType.WOOD_BLOCK)
        # Left and right walls
        ty = wy1 + int(wall_h * frac)
        for dx in range(2):
            for dy in range(2):
                if 0 <= ty + dy < MAP_SIZE:
                    if 0 <= wx1 - 1 + dx < MAP_SIZE:
                        grid[ty + dy][wx1 - 1 + dx] = Cell(CellType.WOOD_BLOCK)
                    if 0 <= wx2 + dx < MAP_SIZE:
                        grid[ty + dy][wx2 + dx] = Cell(CellType.WOOD_BLOCK)

    # Gates in the outer wall (3-5 entries, 2-wide, on different sides)
    num_gates = rng.randint(3, 5)
    sides = ["north", "south", "east", "west"] * 2
    rng.shuffle(sides)
    gates_placed = 0
    used_positions: set[str] = set()
    for side in sides:
        if gates_placed >= num_gates:
            break
        key = side
        if side == "north":
            gx = rng.randint(wx1 + 3, wx2 - 4)
            pos_key = f"n{gx // 5}"
            if pos_key in used_positions:
                continue
            used_positions.add(pos_key)
            for dx in range(2):
                x = gx + dx
                if 0 <= x < MAP_SIZE and 0 <= wy1 < MAP_SIZE:
                    grid[wy1][x] = Cell(CellType.EMPTY)
        elif side == "south":
            gx = rng.randint(wx1 + 3, wx2 - 4)
            pos_key = f"s{gx // 5}"
            if pos_key in used_positions:
                continue
            used_positions.add(pos_key)
            for dx in range(2):
                x = gx + dx
                if 0 <= x < MAP_SIZE and 0 <= wy2 < MAP_SIZE:
                    grid[wy2][x] = Cell(CellType.EMPTY)
        elif side == "west":
            gy = rng.randint(wy1 + 3, wy2 - 4)
            pos_key = f"w{gy // 5}"
            if pos_key in used_positions:
                continue
            used_positions.add(pos_key)
            for dy in range(2):
                y = gy + dy
                if 0 <= wx1 < MAP_SIZE and 0 <= y < MAP_SIZE:
                    grid[y][wx1] = Cell(CellType.EMPTY)
        elif side == "east":
            gy = rng.randint(wy1 + 3, wy2 - 4)
            pos_key = f"e{gy // 5}"
            if pos_key in used_positions:
                continue
            used_positions.add(pos_key)
            for dy in range(2):
                y = gy + dy
                if 0 <= wx2 < MAP_SIZE and 0 <= y < MAP_SIZE:
                    grid[y][wx2] = Cell(CellType.EMPTY)
        gates_placed += 1
