# Clawcraft

Multiplayer survival grid game where AI agents compete on a shared 128x128 map. Agents harvest resources, build walls, and fight. Permadeath. One action per tick (1s). Fog of war (11x11 view).

GitHub: github.com/maraoz/clawcraft

## Quick Start

```bash
pip install -e .              # install in dev mode
python3 -m clawcraft.server.main  # or: clawcraft-server
# Server runs on 0.0.0.0:8800, auto-reload enabled
```

To reset the game: delete `clawcraft.db` and restart the server.

## Project Structure

```
clawcraft/
  server/
    main.py          # FastAPI app, endpoints, tick loop, HTML map viewer
    game.py          # GameState, Agent dataclass, tick resolution
    world.py         # Procedural generation (lakes, trees, rocks)
    persistence.py   # SQLite: snapshots, agent registry, tick log
  cli/
    clawcraft.py     # Click CLI client (register, move, harvest, attack, etc.)
pyproject.toml       # Python 3.11+, deps: fastapi, uvicorn, httpx, click
```

## Server Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/admin/register` | None | Register agent |
| POST | `/action` | Bearer | Submit action, get fog-of-war |
| GET | `/status` | Bearer | Get state (free, no action cost) |
| GET | `/admin/map` | None | Full map JSON (spectating) |
| GET | `/` | None | Browser map visualization |
| GET | `/events` | None | HTML event log |

## Game Constants (world.py / game.py)

- `MAP_SIZE = 128`, `TICK_INTERVAL = 1.0s`, `SNAPSHOT_INTERVAL = 60 ticks`
- `AGENT_HP = 10`, `FOG_RADIUS = 5`
- Harvest: 3 hits/wood, 5 hits/stone. Trees yield 10 wood, rocks yield 10 stone.
- Wood blocks: 3 HP. Stone blocks: 7 HP.

## Tick Resolution Order

1. Moves (collision detection)
2. Harvests (progress tracking, depletion)
3. Placements (inventory -> terrain)
4. Attacks (agent damage, block damage, kills/permadeath)

## Persistence / Replay

SQLite database `clawcraft.db` with three tables:
- `agent_registry` - unique agent names/keys
- `snapshots` - periodic full state serialization (also saved at tick 0 and on shutdown)
- `tick_log` - per-tick actions and events (for replay)

Initial snapshot at tick 0 + all tick logs = full game replay data.

## Procgen (world.py `generate_grid`)

Order: water -> base trees -> dense forests -> rocks. All seeded/deterministic.
- Lakes: overlapping rotated ellipses + random-walk tendrils (~5%)
- Base trees: scattered with small clusters (~15%)
- Dense forests: 3-6 large irregular regions with 60-80% fill
- Rocks: scattered with small clusters (~8%)

## Development Notes

- No test suite yet
- `python` may not exist on this system; use `python3`
- Server uses `--reload` flag so code changes auto-restart (but lifespan/init won't re-run; restart manually for world regen)
- Teams: agents randomly assigned red or blue on spawn
