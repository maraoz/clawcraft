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
    """Build a red (north) and blue (south) fortress. Returns {color: (x1, y1, x2, y2)} spawn rects."""
    margin = 4
    rects: dict[str, tuple[int, int, int, int]] = {}

    for color, y_lo, y_hi in [("red", margin, MAP_SIZE // 3),
                               ("blue", MAP_SIZE * 2 // 3, MAP_SIZE - margin)]:
        fw = rng.randint(18, 24)
        fh = rng.randint(16, 20)

        # Try positions until we find one not overlapping water
        for _ in range(50):
            x1 = rng.randint(margin, MAP_SIZE - fw - margin)
            y1 = rng.randint(y_lo, max(y_lo, y_hi - fh))
            x2, y2 = x1 + fw - 1, y1 + fh - 1
            if y2 >= MAP_SIZE - margin:
                continue
            # Check the rect plus a 2-cell buffer for water
            if not _rect_overlaps_water(grid, x1 - 2, y1 - 2, x2 + 2, y2 + 2):
                break

        rects[color] = (x1, y1, x2, y2)
        _build_fortress(grid, rng, x1, y1, x2, y2)

    return rects


def _build_fortress(
    grid: list[list[Cell]],
    rng: random.Random,
    x1: int, y1: int, x2: int, y2: int,
):
    """Build a fortress with irregular walls, multiple gates, and random interior rooms."""
    fw = x2 - x1 + 1
    fh = y2 - y1 + 1

    # --- Step 1: Compute irregular wall shape ---
    # For each edge, generate a per-cell wobble (0, +1, or -1 inward/outward)
    # so the outline isn't a perfect rectangle.
    wall_cells: set[tuple[int, int]] = set()
    interior_cells: set[tuple[int, int]] = set()

    # Generate wobble offsets for each edge
    top_wobble = [0] + [rng.choice([-1, 0, 0, 0, 1]) for _ in range(fw - 2)] + [0]
    bot_wobble = [0] + [rng.choice([-1, 0, 0, 0, 1]) for _ in range(fw - 2)] + [0]
    left_wobble = [0] + [rng.choice([-1, 0, 0, 0, 1]) for _ in range(fh - 2)] + [0]
    right_wobble = [0] + [rng.choice([-1, 0, 0, 0, 1]) for _ in range(fh - 2)] + [0]

    # Smooth wobbles so we don't get single-cell spikes
    def _smooth(w: list[int]) -> list[int]:
        s = w[:]
        for i in range(1, len(s) - 1):
            if s[i] != 0 and s[i - 1] == 0 and s[i + 1] == 0:
                s[i] = 0  # remove isolated bumps
        return s

    top_wobble = _smooth(top_wobble)
    bot_wobble = _smooth(bot_wobble)
    left_wobble = _smooth(left_wobble)
    right_wobble = _smooth(right_wobble)

    # Top and bottom walls
    for i in range(fw):
        x = x1 + i
        yt = y1 + top_wobble[i]
        yb = y2 + bot_wobble[i]
        if 0 <= x < MAP_SIZE:
            if 0 <= yt < MAP_SIZE:
                wall_cells.add((x, yt))
            if 0 <= yb < MAP_SIZE:
                wall_cells.add((x, yb))

    # Left and right walls
    for j in range(fh):
        y = y1 + j
        xl = x1 + left_wobble[j]
        xr = x2 + right_wobble[j]
        if 0 <= y < MAP_SIZE:
            if 0 <= xl < MAP_SIZE:
                wall_cells.add((xl, y))
            if 0 <= xr < MAP_SIZE:
                wall_cells.add((xr, y))

    # Corner towers (3x3 stone at each corner for a chunky look)
    for cx, cy in [(x1, y1), (x1, y2 - 2), (x2 - 2, y1), (x2 - 2, y2 - 2)]:
        for dx in range(3):
            for dy in range(3):
                tx, ty = cx + dx, cy + dy
                if 0 <= tx < MAP_SIZE and 0 <= ty < MAP_SIZE:
                    wall_cells.add((tx, ty))

    # Interior = everything inside the base rect that isn't a wall
    for y in range(y1, y2 + 1):
        for x in range(x1, x2 + 1):
            if (x, y) not in wall_cells:
                interior_cells.add((x, y))

    # --- Step 2: Clear everything in and around the fortress ---
    for y in range(max(0, y1 - 2), min(MAP_SIZE, y2 + 3)):
        for x in range(max(0, x1 - 2), min(MAP_SIZE, x2 + 3)):
            grid[y][x] = Cell(CellType.EMPTY)

    # --- Step 3: Place walls ---
    for wx, wy in wall_cells:
        if 0 <= wx < MAP_SIZE and 0 <= wy < MAP_SIZE:
            grid[wy][wx] = Cell(CellType.STONE_BLOCK)

    # --- Step 4: Multiple gates (2-3 per fortress, on different sides) ---
    sides = ["top", "bottom", "left", "right"]
    rng.shuffle(sides)
    num_gates = rng.randint(2, 3)
    for side in sides[:num_gates]:
        if side == "top":
            gx = rng.randint(x1 + 3, x2 - 3)
            for dx in range(2):
                # Clear wall and one cell outside for passage
                for dy in range(-1, 2):
                    ty = y1 + top_wobble[min(gx + dx - x1, fw - 1)] + dy
                    if 0 <= gx + dx < MAP_SIZE and 0 <= ty < MAP_SIZE:
                        grid[ty][gx + dx] = Cell(CellType.EMPTY)
        elif side == "bottom":
            gx = rng.randint(x1 + 3, x2 - 3)
            for dx in range(2):
                for dy in range(-1, 2):
                    ty = y2 + bot_wobble[min(gx + dx - x1, fw - 1)] + dy
                    if 0 <= gx + dx < MAP_SIZE and 0 <= ty < MAP_SIZE:
                        grid[ty][gx + dx] = Cell(CellType.EMPTY)
        elif side == "left":
            gy = rng.randint(y1 + 3, y2 - 3)
            for dy in range(2):
                for dx in range(-1, 2):
                    tx = x1 + left_wobble[min(gy + dy - y1, fh - 1)] + dx
                    if 0 <= tx < MAP_SIZE and 0 <= gy + dy < MAP_SIZE:
                        grid[gy + dy][tx] = Cell(CellType.EMPTY)
        elif side == "right":
            gy = rng.randint(y1 + 3, y2 - 3)
            for dy in range(2):
                for dx in range(-1, 2):
                    tx = x2 + right_wobble[min(gy + dy - y1, fh - 1)] + dx
                    if 0 <= tx < MAP_SIZE and 0 <= gy + dy < MAP_SIZE:
                        grid[gy + dy][tx] = Cell(CellType.EMPTY)

    # --- Step 5: Interior rooms via recursive partitioning ---
    inner_x1, inner_y1 = x1 + 3, y1 + 3
    inner_x2, inner_y2 = x2 - 3, y2 - 3
    _partition_rooms(grid, rng, inner_x1, inner_y1, inner_x2, inner_y2, depth=0)

    # --- Step 6: A few stone pillars for character ---
    for _ in range(rng.randint(2, 5)):
        px = rng.randint(inner_x1, inner_x2)
        py = rng.randint(inner_y1, inner_y2)
        if grid[py][px].type == CellType.EMPTY:
            grid[py][px] = Cell(CellType.STONE_BLOCK)


def _partition_rooms(
    grid: list[list[Cell]],
    rng: random.Random,
    x1: int, y1: int, x2: int, y2: int,
    depth: int,
):
    """Recursively partition a rectangle into rooms with wood-block walls and doorways."""
    w = x2 - x1 + 1
    h = y2 - y1 + 1
    min_room = 4

    # Stop if too small or randomly stop at deeper levels
    if w < min_room * 2 + 1 and h < min_room * 2 + 1:
        return
    if depth >= 3:
        return
    if depth >= 1 and rng.random() < 0.3:
        return

    # Choose split direction based on aspect ratio + randomness
    can_split_h = h >= min_room * 2 + 1
    can_split_v = w >= min_room * 2 + 1

    if not can_split_h and not can_split_v:
        return

    if can_split_h and can_split_v:
        split_h = rng.random() < (0.6 if h > w else 0.4)
    else:
        split_h = can_split_h

    if split_h:
        # Horizontal wall
        split_y = rng.randint(y1 + min_room, y2 - min_room)
        for x in range(x1, x2 + 1):
            if 0 <= x < MAP_SIZE and 0 <= split_y < MAP_SIZE:
                grid[split_y][x] = Cell(CellType.WOOD_BLOCK)
        # Doorway (2-wide)
        door_x = rng.randint(x1 + 1, x2 - 2)
        grid[split_y][door_x] = Cell(CellType.EMPTY)
        grid[split_y][door_x + 1] = Cell(CellType.EMPTY)
        # Recurse
        _partition_rooms(grid, rng, x1, y1, x2, split_y - 1, depth + 1)
        _partition_rooms(grid, rng, x1, split_y + 1, x2, y2, depth + 1)
    else:
        # Vertical wall
        split_x = rng.randint(x1 + min_room, x2 - min_room)
        for y in range(y1, y2 + 1):
            if 0 <= y < MAP_SIZE and 0 <= split_x < MAP_SIZE:
                grid[y][split_x] = Cell(CellType.WOOD_BLOCK)
        # Doorway (2-wide)
        door_y = rng.randint(y1 + 1, y2 - 2)
        grid[door_y][split_x] = Cell(CellType.EMPTY)
        grid[door_y + 1][split_x] = Cell(CellType.EMPTY)
        # Recurse
        _partition_rooms(grid, rng, x1, y1, split_x - 1, y2, depth + 1)
        _partition_rooms(grid, rng, split_x + 1, y1, x2, y2, depth + 1)
