"""Procedural world generation and grid data structures."""

import random
from enum import Enum


class CellType(str, Enum):
    EMPTY = "empty"
    TREE = "tree"
    ROCK = "rock"
    WATER = "water"
    WOOD_BLOCK = "wood_block"
    STONE_BLOCK = "stone_block"


IMPASSABLE = {CellType.WATER, CellType.ROCK, CellType.TREE, CellType.WOOD_BLOCK, CellType.STONE_BLOCK}

MAP_SIZE = 64

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


def generate_grid(seed: int | None = None) -> list[list[Cell]]:
    """Generate a MAP_SIZE x MAP_SIZE grid with procgen terrain."""
    rng = random.Random(seed)
    grid = [[Cell(CellType.EMPTY) for _ in range(MAP_SIZE)] for _ in range(MAP_SIZE)]

    # Place water clusters (~5%)
    num_ponds = rng.randint(15, 25)
    for _ in range(num_ponds):
        cx, cy = rng.randint(5, MAP_SIZE - 6), rng.randint(5, MAP_SIZE - 6)
        radius = rng.randint(2, 5)
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if dx * dx + dy * dy <= radius * radius + rng.randint(-2, 2):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < MAP_SIZE and 0 <= ny < MAP_SIZE:
                        grid[ny][nx] = Cell(CellType.WATER)

    # Place trees (~15%)
    target_trees = int(MAP_SIZE * MAP_SIZE * 0.15)
    placed = 0
    while placed < target_trees:
        x, y = rng.randint(0, MAP_SIZE - 1), rng.randint(0, MAP_SIZE - 1)
        if grid[y][x].type == CellType.EMPTY:
            grid[y][x] = Cell(CellType.TREE)
            placed += 1
            # Cluster: place a few nearby
            for _ in range(rng.randint(0, 3)):
                nx = x + rng.randint(-2, 2)
                ny = y + rng.randint(-2, 2)
                if 0 <= nx < MAP_SIZE and 0 <= ny < MAP_SIZE and grid[ny][nx].type == CellType.EMPTY:
                    grid[ny][nx] = Cell(CellType.TREE)
                    placed += 1

    # Place rocks (~8%)
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

    return grid
