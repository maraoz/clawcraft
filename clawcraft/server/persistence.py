"""SQLite persistence for game state snapshots and agent registry."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .game import Agent, GameState
from .world import Cell, CellType, MAP_SIZE

DB_PATH = Path("clawcraft.db")


def _get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: Path = DB_PATH):
    conn = _get_conn(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_registry (
            name TEXT UNIQUE NOT NULL,
            api_key TEXT UNIQUE NOT NULL,
            agent_id TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tick INTEGER NOT NULL,
            data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tick_log (
            tick INTEGER PRIMARY KEY,
            actions TEXT NOT NULL,
            events TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.close()


def register_agent_name(name: str, api_key: str, agent_id: str, db_path: Path = DB_PATH) -> bool:
    """Register agent name. Returns False if name already taken."""
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO agent_registry (name, api_key, agent_id) VALUES (?, ?, ?)",
            (name, api_key, agent_id),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def is_name_taken(name: str, db_path: Path = DB_PATH) -> bool:
    conn = _get_conn(db_path)
    row = conn.execute("SELECT 1 FROM agent_registry WHERE name = ?", (name,)).fetchone()
    conn.close()
    return row is not None


def save_snapshot(state: GameState, db_path: Path = DB_PATH):
    """Serialize full game state to SQLite."""
    # Serialize grid
    grid_data = []
    for y in range(MAP_SIZE):
        row = []
        for x in range(MAP_SIZE):
            cell = state.grid[y][x]
            c = {"t": cell.type.value}
            if cell.hp:
                c["hp"] = cell.hp
            if cell.resource_remaining:
                c["r"] = cell.resource_remaining
            row.append(c)
        grid_data.append(row)

    agents_data = {}
    for aid, agent in state.agents.items():
        agents_data[aid] = {
            "name": agent.name,
            "api_key": agent.api_key,
            "x": agent.x,
            "y": agent.y,
            "hp": agent.hp,
            "wood": agent.wood,
            "stone": agent.stone,
            "color": agent.color,
            "kills": agent.kills,
        }

    snapshot = {
        "tick": state.tick,
        "seed": state.seed,
        "grid": grid_data,
        "agents": agents_data,
        "api_keys": state.api_keys,
    }

    conn = _get_conn(db_path)
    conn.execute("INSERT INTO snapshots (tick, data) VALUES (?, ?)", (state.tick, json.dumps(snapshot)))
    conn.commit()
    conn.close()


def load_latest_snapshot(db_path: Path = DB_PATH) -> GameState | None:
    """Load most recent snapshot. Returns None if no snapshots exist."""
    conn = _get_conn(db_path)
    row = conn.execute("SELECT data FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()

    if not row:
        return None

    snapshot = json.loads(row[0])
    state = GameState()
    state.tick = snapshot["tick"]
    state.seed = snapshot.get("seed")

    # Restore grid
    state.grid = []
    for row_data in snapshot["grid"]:
        row = []
        for c in row_data:
            cell = Cell(CellType(c["t"]))
            cell.hp = c.get("hp", 0)
            cell.resource_remaining = c.get("r", 0)
            row.append(cell)
        state.grid.append(row)

    # Restore agents
    for aid, adata in snapshot["agents"].items():
        agent = Agent(
            id=aid,
            name=adata["name"],
            api_key=adata["api_key"],
            x=adata["x"],
            y=adata["y"],
            hp=adata["hp"],
            wood=adata["wood"],
            stone=adata["stone"],
            color=adata.get("color", "red"),
            kills=adata.get("kills", 0),
        )
        state.agents[aid] = agent

    state.api_keys = snapshot["api_keys"]
    return state


def log_tick(tick: int, actions: list, events: list, db_path: Path = DB_PATH):
    """Log a tick's actions and resulting events."""
    conn = _get_conn(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO tick_log (tick, actions, events) VALUES (?, ?, ?)",
        (tick, json.dumps(actions), json.dumps(events)),
    )
    conn.commit()
    conn.close()
