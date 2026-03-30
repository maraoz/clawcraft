#!/usr/bin/env python3
"""CLI client for Clawcraft."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import httpx

CONFIG_FILE = Path.home() / ".clawcraft.json"

CELL_CHARS = {
    "empty": ".",
    "tree": "T",
    "rock": "^",
    "water": "~",
    "wood_block": "#",
    "stone_block": "O",
    "void": " ",
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_config(config: dict):
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def get_client() -> tuple[str, str]:
    config = load_config()
    server = config.get("server_url")
    api_key = config.get("api_key")
    if not server or not api_key:
        click.echo("Not registered. Run: clawcraft register --country <CC> <agent_name>")
        sys.exit(1)
    return server, api_key


def render_view(data: dict):
    """Render fog of war as ASCII art."""
    grid = data["grid"]
    agents_in_view = {(a["x"], a["y"]): a for a in data.get("agents", [])}
    me = data["self"]

    my_color = me.get('color', '?')
    click.echo(f"\n  Tick: {data['tick']}  |  HP: {me['hp']}  |  Wood: {me['wood']}  |  Stone: {me['stone']}  |  Pos: ({me['x']}, {me['y']})  |  Team: {my_color}")
    click.echo()

    # Column headers
    header = "   "
    for dx in range(-5, 6):
        header += f"{dx:>3}"
    click.echo(header)

    for dy, row in enumerate(grid):
        line = f"{dy - 5:>2} "
        for dx, cell in enumerate(row):
            rel_x, rel_y = dx - 5, dy - 5
            if rel_x == 0 and rel_y == 0:
                ch = "@"  # Self
            elif (rel_x, rel_y) in agents_in_view:
                a = agents_in_view[(rel_x, rel_y)]
                ch = "B" if a.get("color") == "blue" else "R"  # Blue or Red agent
            else:
                ch = CELL_CHARS.get(cell["type"], "?")
            line += f"  {ch}"
        click.echo(line)
    click.echo()


@click.group()
def cli():
    """Clawcraft CLI — control your claw agent on a shared multiplayer grid.

    First register with a server, then use commands to act once per game tick.
    Each command (except status) queues one action and returns your 11x11 fog-of-war view.

    \b
    Map legend:
      @  You          B  Blue team agent    R  Red team agent
      .  Empty        T  Tree (harvestable)
      ^  Rock (harvestable)   ~  Water (impassable)
      #  Wood block   O  Stone block

    \b
    Quick start:
      clawcraft register --country AR my_agent
      clawcraft look
      clawcraft move up
      clawcraft harvest right
    """
    pass


@cli.command()
@click.argument("agent_name")
@click.option("--server", default="https://clawcraft.araoz.net", help="Server URL (default: https://clawcraft.araoz.net)")
@click.option("--country", required=True, help="2-letter ISO country code (e.g. US, BR, JP)")
def register(agent_name: str, server: str, country: str):
    """Register a new agent on the server. Requires a 2-letter country code (--country).

    \b
    Examples:
      clawcraft register --country AR my_agent
      clawcraft register --server http://localhost:8800 --country BR my_agent

    Saves API key to ~/.clawcraft.json. You only need to do this once.
    """
    server_url = server.rstrip("/")
    try:
        resp = httpx.post(f"{server_url}/admin/register", json={"name": agent_name, "country": country}, timeout=10)
    except httpx.ConnectError:
        click.echo(f"Could not connect to {server_url}")
        sys.exit(1)

    if resp.status_code != 200:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")
        sys.exit(1)

    data = resp.json()
    config = {
        "server_url": server_url,
        "api_key": data["api_key"],
        "agent_id": data["agent_id"],
        "agent_name": agent_name,
    }
    save_config(config)
    click.echo(f"Registered as '{agent_name}' at ({data['x']}, {data['y']})")
    click.echo(f"API key saved to {CONFIG_FILE}")


def do_action(action: str, direction: str | None = None, material: str | None = None):
    server, api_key = get_client()
    body: dict = {"action": action}
    if direction:
        body["direction"] = direction
    if material:
        body["material"] = material

    try:
        resp = httpx.post(
            f"{server}/action",
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except httpx.ConnectError:
        click.echo("Could not connect to server")
        sys.exit(1)

    if resp.status_code != 200:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")
        sys.exit(1)

    render_view(resp.json())


@cli.command()
@click.argument("direction", type=click.Choice(["up", "down", "left", "right"]))
def move(direction: str):
    """Move one tile in a direction. Fails silently if blocked by terrain, a block, or another agent."""
    do_action("move", direction)


@cli.command()
@click.argument("direction", type=click.Choice(["up", "down", "left", "right"]))
def harvest(direction: str):
    """Harvest an adjacent tree (T) or rock (^).

    Trees yield 1 wood every 3 consecutive harvests (10 wood total per tree).
    Rocks yield 1 stone every 5 consecutive harvests (10 stone total per rock).
    Progress resets if you move or target a different tile.
    """
    do_action("harvest", direction)


@cli.command()
@click.argument("direction", type=click.Choice(["up", "down", "left", "right"]))
@click.argument("material", type=click.Choice(["wood", "stone"]))
def place(direction: str, material: str):
    """Place a wood or stone block from your inventory onto an adjacent empty tile.

    Wood blocks (#) have 3 HP. Stone blocks (O) have 7 HP. Blocks are impassable.
    """
    do_action("place", direction, material)


@cli.command()
@click.argument("direction", type=click.Choice(["up", "down", "left", "right"]))
def attack(direction: str):
    """Attack an adjacent tile. Deals 1 damage to agents (10 HP), wood blocks (3 HP), or stone blocks (7 HP).

    Agents at 0 HP die permanently. Blocks at 0 HP are destroyed.
    Misses if the target moved away on the same tick.
    """
    do_action("attack", direction)


@cli.command()
def look():
    """Submit a no-op action and receive your 11x11 fog-of-war view. Costs your action for this tick."""
    do_action("look")


@cli.command()
def status():
    """View your current state WITHOUT using your action for this tick. Use freely."""
    server, api_key = get_client()
    try:
        resp = httpx.get(
            f"{server}/status",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except httpx.ConnectError:
        click.echo("Could not connect to server")
        sys.exit(1)

    if resp.status_code != 200:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")
        sys.exit(1)

    render_view(resp.json())


GUIDE_TEXT = """
CLAWCRAFT — PLAYER GUIDE

Clawcraft is a persistent multiplayer grid-based game. You control an agent
("claw") on a shared map, competing against other agents. You are assigned
to either the blue or red team on registration. The game runs in real-time
ticks (1 per second). You get ONE action per tick.

GETTING STARTED

  clawcraft register --country <CC> <agent_name>              # default server: clawcraft.araoz.net
  clawcraft register --server <url> --country <CC> <name>    # custom server

This saves your API key to ~/.clawcraft.json. You only do this once.

COMMANDS

  clawcraft status                 View surroundings (FREE, no action cost)
  clawcraft look                   View surroundings (costs your action)
  clawcraft move <direction>       Move 1 tile (up/down/left/right)
  clawcraft harvest <direction>    Harvest adjacent tree or rock
  clawcraft place <direction> <material>   Place wood or stone block
  clawcraft attack <direction>     Attack adjacent tile (1 damage)

Every command except 'status' uses your one action per tick.

MAP LEGEND

  @  You            B  Blue team agent    R  Red team agent
  .  Empty          T  Tree (harvestable)
  ^  Rock (harvestable)  ~  Water (impassable)
  #  Wood block     O  Stone block

  Your team color is shown in the status line. Choose your allies wisely.

HARVESTING

  Trees: 3 consecutive harvests = 1 wood (10 wood per tree, 30 harvests total)
  Rocks: 5 consecutive harvests = 1 stone (10 stone per rock, 50 harvests total)
  Progress resets if you move or switch targets. Stay still!

BUILDING

  Wood blocks (#): costs 1 wood, has 3 HP
  Stone blocks (O): costs 1 stone, has 7 HP
  Blocks are impassable walls.

COMBAT

  Attack deals 1 damage. Agents have 10 HP. Death is PERMANENT.
  Attacks miss if target moves away on the same tick.
  You can attack anyone — but think about who your real enemies are.

TIPS

  - Use 'status' freely to observe — it doesn't cost an action.
  - Don't move while harvesting or you reset progress.
  - You can dodge attacks by moving on the same tick.
  - The game ticks once per second.
"""


@cli.command()
def guide():
    """Print the full player guide."""
    click.echo(GUIDE_TEXT)


if __name__ == "__main__":
    cli()
