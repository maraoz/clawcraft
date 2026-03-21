"""Game state and tick resolution."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from .world import (
    MAP_SIZE,
    HARVESTS_PER_STONE,
    HARVESTS_PER_WOOD,
    IMPASSABLE,
    STONE_BLOCK_HP,
    WOOD_BLOCK_HP,
    Cell,
    CellType,
    generate_grid,
)

DIRECTIONS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

AGENT_HP = 10
FOG_RADIUS = 5


@dataclass
class Agent:
    id: str
    name: str
    api_key: str
    x: int
    y: int
    hp: int = AGENT_HP
    wood: int = 0
    stone: int = 0
    color: str = "red"
    harvest_target: tuple[int, int] | None = None
    harvest_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "hp": self.hp,
            "wood": self.wood,
            "stone": self.stone,
            "color": self.color,
        }


@dataclass
class GameState:
    grid: list[list[Cell]] = field(default_factory=list)
    agents: dict[str, Agent] = field(default_factory=dict)  # id -> Agent
    api_keys: dict[str, str] = field(default_factory=dict)  # api_key -> agent_id
    tick: int = 0
    pending_actions: dict[str, dict] = field(default_factory=dict)  # agent_id -> action
    seed: int | None = None

    def initialize(self, seed: int | None = None):
        self.seed = seed
        self.grid = generate_grid(seed)
        self.tick = 0

    def register_agent(self, name: str) -> Agent:
        agent_id = secrets.token_hex(8)
        api_key = secrets.token_hex(16)

        # Find random empty tile near center (within 16-tile radius)
        import random
        center = MAP_SIZE // 2
        spawn_radius = 10
        while True:
            x = random.randint(center - spawn_radius, center + spawn_radius)
            y = random.randint(center - spawn_radius, center + spawn_radius)
            if 0 <= x < MAP_SIZE and 0 <= y < MAP_SIZE and self.grid[y][x].type == CellType.EMPTY and not self._agent_at(x, y):
                break

        color = random.choice(["red", "blue"])
        agent = Agent(id=agent_id, name=name, api_key=api_key, x=x, y=y, color=color)
        self.agents[agent_id] = agent
        self.api_keys[api_key] = agent_id
        return agent

    def _agent_at(self, x: int, y: int) -> Agent | None:
        for a in self.agents.values():
            if a.x == x and a.y == y and a.hp > 0:
                return a
        return None

    def queue_action(self, agent_id: str, action: dict):
        self.pending_actions[agent_id] = action

    def resolve_tick(self):
        """Process all queued actions simultaneously, then advance tick."""
        actions = dict(self.pending_actions)
        self.pending_actions.clear()

        # Separate actions by type
        moves: dict[str, tuple[int, int]] = {}
        harvests: dict[str, tuple[int, int]] = {}
        places: dict[str, tuple[int, int, str]] = {}
        attacks: dict[str, tuple[int, int]] = {}

        for agent_id, act in actions.items():
            agent = self.agents.get(agent_id)
            if not agent or agent.hp <= 0:
                continue

            action_type = act.get("action")
            direction = act.get("direction")

            if action_type == "look":
                continue

            if direction not in DIRECTIONS:
                continue

            dx, dy = DIRECTIONS[direction]
            tx, ty = agent.x + dx, agent.y + dy

            if action_type == "move":
                moves[agent_id] = (tx, ty)
            elif action_type == "harvest":
                harvests[agent_id] = (tx, ty)
            elif action_type == "place":
                material = act.get("material")
                if material in ("wood", "stone"):
                    places[agent_id] = (tx, ty, material)
            elif action_type == "attack":
                attacks[agent_id] = (tx, ty)

        # --- Resolve moves ---
        # Check for conflicts: two agents trying to move to same tile
        target_counts: dict[tuple[int, int], list[str]] = {}
        for aid, target in moves.items():
            target_counts.setdefault(target, []).append(aid)

        moved_agents: set[str] = set()
        for aid, (tx, ty) in moves.items():
            agent = self.agents[aid]
            # Out of bounds
            if not (0 <= tx < MAP_SIZE and 0 <= ty < MAP_SIZE):
                continue
            # Impassable terrain
            if self.grid[ty][tx].type in IMPASSABLE:
                continue
            # Conflict: multiple agents targeting same tile
            if len(target_counts[(tx, ty)]) > 1:
                continue
            # Occupied by non-moving agent
            occupant = self._agent_at(tx, ty)
            if occupant and occupant.id not in moves:
                continue
            # Occupied by agent also moving but to a conflicted tile (they stay)
            if occupant and occupant.id in moves and occupant.id not in moved_agents:
                # Check if occupant will actually move away
                occ_target = moves[occupant.id]
                otx, oty = occ_target
                if not (0 <= otx < MAP_SIZE and 0 <= oty < MAP_SIZE):
                    continue
                if self.grid[oty][otx].type in IMPASSABLE:
                    continue
                if len(target_counts.get(occ_target, [])) > 1:
                    continue

            agent.x, agent.y = tx, ty
            moved_agents.add(aid)
            # Reset harvest progress on move
            agent.harvest_target = None
            agent.harvest_count = 0

        # --- Resolve harvests ---
        for aid, (tx, ty) in harvests.items():
            agent = self.agents[aid]
            if not (0 <= tx < MAP_SIZE and 0 <= ty < MAP_SIZE):
                continue

            cell = self.grid[ty][tx]
            if cell.type == CellType.TREE and cell.resource_remaining > 0:
                target_pos = (tx, ty)
                if agent.harvest_target != target_pos:
                    agent.harvest_target = target_pos
                    agent.harvest_count = 0
                agent.harvest_count += 1
                if agent.harvest_count >= HARVESTS_PER_WOOD:
                    agent.wood += 1
                    agent.harvest_count = 0
                    cell.resource_remaining -= 1
                    if cell.resource_remaining <= 0:
                        self.grid[ty][tx] = Cell(CellType.EMPTY)

            elif cell.type == CellType.ROCK and cell.resource_remaining > 0:
                target_pos = (tx, ty)
                if agent.harvest_target != target_pos:
                    agent.harvest_target = target_pos
                    agent.harvest_count = 0
                agent.harvest_count += 1
                if agent.harvest_count >= HARVESTS_PER_STONE:
                    agent.stone += 1
                    agent.harvest_count = 0
                    cell.resource_remaining -= 1
                    if cell.resource_remaining <= 0:
                        self.grid[ty][tx] = Cell(CellType.EMPTY)

        # --- Resolve places ---
        for aid, (tx, ty, material) in places.items():
            agent = self.agents[aid]
            if not (0 <= tx < MAP_SIZE and 0 <= ty < MAP_SIZE):
                continue
            if self.grid[ty][tx].type != CellType.EMPTY:
                continue
            if self._agent_at(tx, ty):
                continue
            if material == "wood" and agent.wood > 0:
                agent.wood -= 1
                self.grid[ty][tx] = Cell(CellType.WOOD_BLOCK)
            elif material == "stone" and agent.stone > 0:
                agent.stone -= 1
                self.grid[ty][tx] = Cell(CellType.STONE_BLOCK)

        # --- Resolve attacks ---
        # Collect positions of agents AFTER moves resolved (for miss detection)
        agent_positions: dict[tuple[int, int], str] = {}
        for a in self.agents.values():
            if a.hp > 0:
                agent_positions[(a.x, a.y)] = a.id

        dead_agents: list[str] = []

        for aid, (tx, ty) in attacks.items():
            if not (0 <= tx < MAP_SIZE and 0 <= ty < MAP_SIZE):
                continue

            # Attack agent at target
            target_aid = agent_positions.get((tx, ty))
            if target_aid and target_aid != aid:
                target_agent = self.agents[target_aid]
                target_agent.hp -= 1
                if target_agent.hp <= 0:
                    dead_agents.append(target_aid)
                continue

            # Attack block
            cell = self.grid[ty][tx]
            if cell.type in (CellType.WOOD_BLOCK, CellType.STONE_BLOCK):
                cell.hp -= 1
                if cell.hp <= 0:
                    self.grid[ty][tx] = Cell(CellType.EMPTY)

        # Remove dead agents (permadeath)
        for aid in dead_agents:
            agent = self.agents.pop(aid, None)
            if agent:
                self.api_keys.pop(agent.api_key, None)

        self.tick += 1

    def get_fog_of_war(self, agent: Agent) -> dict:
        """Return 11x11 view centered on agent."""
        view = []
        visible_agents = []

        for dy in range(-FOG_RADIUS, FOG_RADIUS + 1):
            row = []
            for dx in range(-FOG_RADIUS, FOG_RADIUS + 1):
                wx, wy = agent.x + dx, agent.y + dy
                if 0 <= wx < MAP_SIZE and 0 <= wy < MAP_SIZE:
                    row.append(self.grid[wy][wx].to_dict())
                    # Check for agents on this tile
                    occ = self._agent_at(wx, wy)
                    if occ and occ.id != agent.id:
                        visible_agents.append({
                            "name": occ.name,
                            "x": dx,
                            "y": dy,
                            "hp": occ.hp,
                        })
                else:
                    row.append({"type": "void"})
            view.append(row)

        return {
            "grid": view,
            "agents": visible_agents,
            "self": {
                "hp": agent.hp,
                "wood": agent.wood,
                "stone": agent.stone,
                "x": agent.x,
                "y": agent.y,
            },
            "tick": self.tick,
        }

    def get_full_map(self) -> dict:
        """Return full map state for spectating."""
        grid = []
        for y in range(MAP_SIZE):
            row = []
            for x in range(MAP_SIZE):
                row.append(self.grid[y][x].to_dict())
            grid.append(row)

        agents = [a.to_dict() for a in self.agents.values() if a.hp > 0]
        return {"grid": grid, "agents": agents, "tick": self.tick}
