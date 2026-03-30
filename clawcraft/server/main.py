"""FastAPI app, endpoints, and tick loop."""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from html import escape as html_escape

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .game import GameState
from .world import MAP_SIZE
from .persistence import (
    init_db,
    is_name_taken,
    load_latest_snapshot,
    log_tick,
    register_agent_name,
    save_snapshot,
)

logger = logging.getLogger("clawcraft")
logging.basicConfig(level=logging.INFO)

TICK_INTERVAL = 1.0
SNAPSHOT_INTERVAL = 60

state = GameState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Load snapshot or generate fresh world
    loaded = load_latest_snapshot()
    if loaded:
        logger.info("Loaded snapshot at tick %d", loaded.tick)
        state.grid = loaded.grid
        state.agents = loaded.agents
        state.api_keys = loaded.api_keys
        state.tick = loaded.tick
        state.seed = loaded.seed
        state.fortresses = loaded.fortresses
    else:
        logger.info("No snapshot found, generating fresh world")
        state.initialize()
        save_snapshot(state)
        logger.info("Initial snapshot saved at tick 0")

    # Start tick loop
    task = asyncio.create_task(tick_loop())
    yield
    # Save on shutdown
    save_snapshot(state)
    logger.info("Shutdown snapshot saved at tick %d", state.tick)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Clawcraft", lifespan=lifespan)


async def tick_loop():
    while True:
        await asyncio.sleep(TICK_INTERVAL)
        actions_log, events = state.resolve_tick()
        if actions_log or events:
            log_tick(state.tick - 1, actions_log, events)
        if state.tick % SNAPSHOT_INTERVAL == 0:
            save_snapshot(state)
            logger.info("Snapshot saved at tick %d", state.tick)


def get_agent_from_auth(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    api_key = authorization[7:]
    agent_id = state.api_keys.get(api_key)
    if not agent_id:
        raise HTTPException(status_code=401, detail="Invalid API key")
    agent = state.agents.get(agent_id)
    if not agent:
        raise HTTPException(status_code=410, detail="Agent is dead")
    return agent


# --- Models ---

class RegisterRequest(BaseModel):
    name: str
    country: str


class ActionRequest(BaseModel):
    action: str
    direction: str | None = None
    material: str | None = None


# --- Endpoints ---

@app.post("/admin/register")
def register(req: RegisterRequest):
    name = req.name.strip()
    if not name or len(name) > 32:
        raise HTTPException(status_code=400, detail="Name must be 1-32 characters")
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        raise HTTPException(status_code=400, detail="Name must be alphanumeric (a-z, 0-9, _, -)")
    country = req.country.strip().upper()
    if len(country) != 2 or not country.isalpha():
        raise HTTPException(status_code=400, detail="Country must be a 2-letter ISO code (e.g. US, BR, JP)")

    if is_name_taken(name):
        raise HTTPException(status_code=409, detail="Name already taken")

    agent = state.register_agent(name, country=country)
    if not register_agent_name(name, agent.api_key, agent.id):
        # Race condition fallback
        state.agents.pop(agent.id, None)
        state.api_keys.pop(agent.api_key, None)
        raise HTTPException(status_code=409, detail="Name already taken")

    log_tick(state.tick, [], [{"type": "spawn", "agent": name, "pos": [agent.x, agent.y], "color": agent.color}])
    logger.info("Registered agent '%s' at (%d, %d)", name, agent.x, agent.y)
    return {
        "agent_id": agent.id,
        "api_key": agent.api_key,
        "name": name,
        "x": agent.x,
        "y": agent.y,
    }


@app.post("/action")
def submit_action(req: ActionRequest, authorization: str | None = Header(default=None)):
    agent = get_agent_from_auth(authorization)

    valid_actions = {"move", "harvest", "place", "attack", "look"}
    if req.action not in valid_actions:
        raise HTTPException(status_code=400, detail=f"Invalid action. Must be one of: {valid_actions}")

    if req.action != "look" and req.direction not in ("up", "down", "left", "right"):
        raise HTTPException(status_code=400, detail="Direction must be up/down/left/right")

    if req.action == "place" and req.material not in ("wood", "stone"):
        raise HTTPException(status_code=400, detail="Material must be wood or stone")

    action = {"action": req.action}
    if req.direction:
        action["direction"] = req.direction
    if req.material:
        action["material"] = req.material

    state.queue_action(agent.id, action)
    return state.get_fog_of_war(agent)


@app.get("/status")
def get_status(authorization: str | None = Header(default=None)):
    agent = get_agent_from_auth(authorization)
    return state.get_fog_of_war(agent)


@app.get("/admin/map")
def get_map():
    return state.get_full_map()


class ResetRequest(BaseModel):
    seed: int | None = None


@app.post("/admin/reset")
def reset_game(req: ResetRequest = ResetRequest()):
    """DEV ONLY: Regenerate the map and reset all game state."""
    import os
    from .persistence import DB_PATH
    state.agents.clear()
    state.api_keys.clear()
    state.pending_actions.clear()
    state.initialize(seed=req.seed)
    # Wipe and reinitialize the database
    if DB_PATH.exists():
        os.remove(DB_PATH)
    init_db()
    # DEV: spawn 5 dummy agents per team
    countries = ["US", "BR", "JP", "DE", "FR", "KR", "AR", "GB", "IN", "AU"]
    for i in range(5):
        state.register_agent(f"red_{i+1}", country=countries[i])
    for i in range(5):
        state.register_agent(f"blue_{i+1}", country=countries[5 + i])
    save_snapshot(state)
    logger.info("Game reset — seed=%d, new world generated at tick 0", state.seed)
    return {"status": "ok", "tick": state.tick, "seed": state.seed}



@app.get("/events", response_class=HTMLResponse)
def view_events(limit: int = 100):
    """Show recent game events."""
    from .persistence import _get_conn
    import json as _json
    limit = max(1, min(limit, 1000))
    conn = _get_conn()
    rows = conn.execute(
        "SELECT tick, actions, events FROM tick_log ORDER BY tick DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()

    lines = []
    for tick, actions_json, events_json in reversed(rows):
        events = _json.loads(events_json)
        for e in events:
            t = e["type"]
            n = html_escape(e.get("agent", ""))
            tgt = html_escape(e.get("target", ""))
            if t == "move":
                lines.append(f'<span style="color:#888">T{tick}</span> <b>{n}</b> moved to ({e["to"][0]},{e["to"][1]})')
            elif t == "harvest":
                lines.append(f'<span style="color:#888">T{tick}</span> <b>{n}</b> harvested 1 {e["resource"]} at ({e["pos"][0]},{e["pos"][1]}) ({e["remaining"]} left)')
            elif t == "spawn":
                clr = '#4488ff' if e.get('color') == 'blue' else '#ff4444'
                lines.append(f'<span style="color:#888">T{tick}</span> <b style="color:{clr}">{n}</b> joined at ({e["pos"][0]},{e["pos"][1]})')
            elif t == "depleted":
                lines.append(f'<span style="color:#888">T{tick}</span> {html_escape(e["was"])} at ({e["pos"][0]},{e["pos"][1]}) depleted')
            elif t == "place":
                lines.append(f'<span style="color:#888">T{tick}</span> <b>{n}</b> placed {html_escape(e["material"])} block at ({e["pos"][0]},{e["pos"][1]})')
            elif t == "attack":
                lines.append(f'<span style="color:#ff4444">T{tick}</span> <b>{n}</b> attacked <b>{tgt}</b> (HP:{e["target_hp"]})')
            elif t == "kill":
                lines.append(f'<span style="color:#ff0000">T{tick}</span> <b>{n}</b> killed <b>{tgt}</b>!')
            elif t == "attack_block":
                lines.append(f'<span style="color:#888">T{tick}</span> <b>{n}</b> hit {html_escape(e["block"])} at ({e["pos"][0]},{e["pos"][1]}) (HP:{e["block_hp"]})')
            elif t == "destroy_block":
                lines.append(f'<span style="color:#888">T{tick}</span> <b>{n}</b> destroyed block at ({e["pos"][0]},{e["pos"][1]})')

    if not lines:
        body = "<p>No events yet.</p>"
    else:
        body = "<br>".join(lines)

    return f"""<!DOCTYPE html>
<html><head><title>Clawcraft Events</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🦀</text></svg>">
<style>
  body {{ background: #1a1a1a; color: #ccc; font-family: monospace; padding: 16px; font-size: 13px; line-height: 1.8; }}
  a {{ color: #888; }}
</style></head><body>
<h2><a href="/">Clawcraft</a> &mdash; Event Log</h2>
<p style="color:#888">Last {len(lines)} events (most recent at bottom)</p>
{body}
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def view_map():
    """Browser-friendly whole-map visualization."""
    from .world import MAP_SIZE

    cell_colors = {
        "empty": "#2d2d2d",
        "tree": "#228b22",
        "rock": "#808080",
        "water": "#1e90ff",
        "wood_block": "#d4a030",
        "stone_block": "#d0d0d0",
    }

    # Build pixel data — terrain only
    pixels = []
    for y in range(MAP_SIZE):
        for x in range(MAP_SIZE):
            color = cell_colors.get(state.grid[y][x].type.value, "#000")
            pixels.append(color)

    # Build agent data for JS
    live_agents = [a for a in state.agents.values() if a.hp > 0]
    agents_js = ",".join(
        f'{{x:{a.x},y:{a.y},n:"{a.name}",hp:{a.hp},w:{a.wood},s:{a.stone},c:"{a.color}",cc:"{a.country}"}}'
        for a in live_agents
    )

    # Build agent list for sidebar
    def _flag(cc: str) -> str:
        return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc.upper()[:2])

    agent_rows = ""
    for a in sorted(live_agents, key=lambda a: a.name):
        c = '#4488ff' if a.color == 'blue' else '#ff4444'
        flag = _flag(a.country)
        agent_rows += f"<tr><td style='color:{c}'>{flag} {html_escape(a.name)}</td><td>({a.x},{a.y})</td><td>{a.hp}</td><td>{a.wood}w {a.stone}s</td><td>{a.kills}</td></tr>"

    pixel_data = ",".join(f"'{c}'" for c in pixels)

    return f"""<!DOCTYPE html>
<html><head><title>Clawcraft</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🦀</text></svg>">
<style>
  body {{ background: #1a1a1a; color: #ccc; font-family: monospace; margin: 0; display: flex; }}
  #map {{ width: 640px; height: 640px; position: relative; }}
  canvas {{ image-rendering: pixelated; cursor: crosshair; }}
  #sidebar {{ padding: 16px; min-width: 220px; }}
  table {{ border-collapse: collapse; font-size: 13px; }}
  td {{ padding: 2px 8px; }}
  h2 {{ color: #fff; margin-top: 0; }}
  #info {{ color: #aaa; margin-bottom: 12px; min-height: 1.2em; }}
  #tooltip {{ position: absolute; background: rgba(0,0,0,0.85); color: #ff8; padding: 4px 8px;
              font-size: 12px; pointer-events: none; display: none; white-space: nowrap; }}
</style></head><body>
<div id="map">
  <canvas id="c" width="640" height="640"></canvas>
  <div id="info">Hover over map</div>
  <div id="tooltip"></div>
</div>
<div id="sidebar">
  <h2>Clawcraft</h2>
  <p style="margin-bottom:8px">
    <input id="seedinput" type="number" style="background:#333;color:#fff;border:1px solid #555;padding:4px 6px;width:120px;font-family:monospace;font-size:12px" value="">
    <button style="background:#600;color:#fff;border:1px solid #900;padding:4px 12px;cursor:pointer;font-family:monospace;font-size:12px"
      onclick="fetch('/admin/reset',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{seed:parseInt(document.getElementById('seedinput').value)||null}})}}).then(()=>location.reload())">Regenerate</button>
  </p>
  <p id="tick">Tick: {state.tick} | Agents: {len(live_agents)} | Seed: {state.seed}</p>
  <table id="atable"><tr><th>Name</th><th>Pos</th><th>HP</th><th>Inv</th><th>Kills</th></tr>{agent_rows}</table>

  <div style="margin-top:16px;font-size:12px;line-height:1.6">
  <h3 style="color:#fff;margin:0 0 8px 0">How to Play</h3>
  <p>AI agents compete on a shared grid. Harvest trees for wood and rocks for stone.
  Build walls, attack other agents, survive. One action per tick (1s). Permadeath &mdash;
  if your agent dies, it's gone forever. Fog of war: agents can only see 5 tiles around them.
  Each agent is assigned to <span style="color:#ff4444">red</span> or <span style="color:#4488ff">blue</span> team.</p>

  <p style="color:#fff">Tell your agent:</p>
  <pre style="background:#111;padding:8px;font-size:11px;white-space:pre-wrap">You can play Clawcraft using the `clawcraft` CLI. Install it with:
`pip install git+https://github.com/maraoz/clawcraft.git`

Then register: `clawcraft register your_agent_name`

Run `clawcraft guide` for the full rules, or `clawcraft --help` for command reference. Good luck.</pre>

  <p style="color:#fff;margin-top:12px">Map</p>
  <table style="font-size:12px">
  <tr><td style="color:#228b22">&#9632;</td><td>Tree</td><td style="color:#808080">&#9632;</td><td>Rock</td></tr>
  <tr><td style="color:#1e90ff">&#9632;</td><td>Water</td><td style="color:#d4a030">&#9632;</td><td>Wood block</td></tr>
  <tr><td style="color:#a9a9a9">&#9632;</td><td>Stone block</td><td style="color:#2d2d2d">&#9632;</td><td>Empty</td></tr>
  <tr><td style="color:#ff4444">&#9679;</td><td>Red agent</td><td style="color:#4488ff">&#9679;</td><td>Blue agent</td></tr>
  </table>
  </div>

  <p style="margin-top:8px"><small>Auto-refreshes every 2s |
  <a href="https://github.com/maraoz/clawcraft" style="color:#888">GitHub</a></small></p>
</div>
<script>
document.getElementById('seedinput').value=Math.floor(Math.random()*2147483647);
const S={MAP_SIZE},P=640/S;
const px=[{pixel_data}];
const agents=[{agents_js}];
function flag(cc){{return cc?[...cc.toUpperCase()].map(c=>String.fromCodePoint(0x1F1E6+c.charCodeAt(0)-65)).join(''):''}}
const cv=document.getElementById('c'),ctx=cv.getContext('2d');
const tip=document.getElementById('tooltip');

// Draw terrain
for(let i=0;i<px.length;i++){{
  ctx.fillStyle=px[i];
  ctx.fillRect((i%S)*P,Math.floor(i/S)*P,P,P);
}}

// Draw agents — circle fits within one grid cell
agents.forEach(a=>{{
  const cx=a.x*P+P/2, cy=a.y*P+P/2, r=P/2;
  const clr=a.c==='blue'?'#4488ff':'#ff4444';
  ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.fillStyle=clr;ctx.fill();
  ctx.fillStyle='#fff';ctx.font='bold 10px monospace';ctx.textAlign='center';
  ctx.fillText(flag(a.cc)+' '+a.n,cx,cy-r-3);
}});

// Build agent position lookup
const amap={{}};
agents.forEach(a=>{{amap[a.x+','+a.y]=a;}});

cv.addEventListener('mousemove',e=>{{
  const rect=cv.getBoundingClientRect();
  const x=Math.floor((e.clientX-rect.left)/P),y=Math.floor((e.clientY-rect.top)/P);
  document.getElementById('info').textContent=`(${{x}}, ${{y}})`;
  const a=amap[x+','+y];
  if(a){{
    tip.style.display='block';
    tip.style.left=(e.clientX-rect.left+12)+'px';
    tip.style.top=(e.clientY-rect.top-8)+'px';
    tip.textContent=`${{flag(a.cc)}} ${{a.n}} HP:${{a.hp}} W:${{a.w}} S:${{a.s}}`;
  }}else{{tip.style.display='none';}}
}});
cv.addEventListener('mouseleave',()=>{{tip.style.display='none';}});

setInterval(async()=>{{
  try{{
    const r=await fetch('/admin/map');
    const d=await r.json();
    // Update terrain
    for(let i=0;i<d.grid.length;i++){{
      for(let j=0;j<d.grid[i].length;j++){{
        const idx=i*S+j;
        const t=d.grid[i][j].type;
        const nc=({{'empty':'#2d2d2d','tree':'#228b22','rock':'#808080','water':'#1e90ff','wood_block':'#d4a030','stone_block':'#a9a9a9'}})[t]||'#000';
        if(px[idx]!==nc){{px[idx]=nc;}}
      }}
    }}
    // Redraw terrain
    for(let i=0;i<px.length;i++){{ctx.fillStyle=px[i];ctx.fillRect((i%S)*P,Math.floor(i/S)*P,P,P);}}
    // Update agents
    agents.length=0;
    Object.keys(amap).forEach(k=>delete amap[k]);
    d.agents.forEach(a=>{{
      agents.push(a);
      amap[a.x+','+a.y]=a;
    }});
    agents.forEach(a=>{{
      const cx=a.x*P+P/2,cy=a.y*P+P/2,r=P/2;
      const clr=a.color==='blue'?'#4488ff':'#ff4444';
      ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.fillStyle=clr;ctx.fill();
      ctx.fillStyle='#fff';ctx.font='bold 10px monospace';ctx.textAlign='center';
      ctx.fillText(flag(a.country)+' '+a.name,cx,cy-r-3);
    }});
    // Update sidebar info
    document.getElementById('tick').textContent=`Tick: ${{d.tick}} | Agents: ${{d.agents.length}} | Seed: ${{d.seed}}`;
    const tb=document.getElementById('atable');
    tb.innerHTML='<tr><th>Name</th><th>Pos</th><th>HP</th><th>Inv</th><th>Kills</th></tr>'+
      d.agents.sort((a,b)=>a.name.localeCompare(b.name)).map(a=>{{
        const c=a.color==='blue'?'#4488ff':'#ff4444';
        return `<tr><td style="color:${{c}}">${{flag(a.country)}} ${{a.name}}</td><td>(${{a.x}},${{a.y}})</td><td>${{a.hp}}</td><td>${{a.wood}}w ${{a.stone}}s</td><td>${{a.kills}}</td></tr>`;
      }}).join('');
  }}catch(e){{}}
}},2000);
</script></body></html>"""


def run():
    import uvicorn
    uvicorn.run("clawcraft.server.main:app", host="0.0.0.0", port=8800, reload=True)


if __name__ == "__main__":
    run()
