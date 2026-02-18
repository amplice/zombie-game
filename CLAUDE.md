# Zombie Survival — Axiom Engine Game Project

This is a game built on the **Axiom engine**, an AI-native 2D game engine controlled via HTTP API. The engine runs as a separate process; this project contains only game logic (Python build scripts, Lua gameplay scripts, assets).

## How This Works

1. The engine runs at `http://127.0.0.1:3000` (start it: `cd C:\Users\cobra\axiom && cargo run -- --headless`)
2. Python scripts in this folder call the HTTP API to build the game (generate maps, spawn entities, upload scripts)
3. Lua scripts in `scripts/` define gameplay behavior (AI, combat, waves, rules)
4. The engine executes everything — physics, collision, scripting, rendering

**CRITICAL RULES:**
- **NEVER edit ANY file under `C:\Users\cobra\axiom\`.** This includes Rust source, config, assets, or any other engine file. No exceptions.
- Only edit files under `C:\Users\cobra\zombie-game\`.
- For engine bugs, missing features, or needed engine changes, document them in `C:\Users\cobra\zombie-game\engine_feedback.md` and tell the user — NEVER attempt to fix them directly.
- All game logic goes through the API and Lua scripts.

## Project Structure

```
zombie-game/
  axiom_client.py        # HTTP client for the engine API
  build_game.py          # Main game builder script
  scripts/               # Lua gameplay scripts (uploaded to engine via API)
    zombie_ai.lua
    player_combat.lua
    wave_spawner.lua
    game_rules.lua
  assets/
    sprites/             # Sprite sheets (PNG)
    audio/               # Sound effects and music (OGG)
```

## Running

```bash
# Terminal 1: start the engine
cd C:\Users\cobra\axiom && cargo run -- --headless

# Terminal 2: build and run the game
cd C:\Users\cobra\zombie-game && python build_game.py

# To play with a window (after building) — entities and UI render automatically:
cd C:\Users\cobra\axiom && cargo run
```

---

# Axiom Engine API Reference

## Response Format

All API responses:
```json
{ "ok": true, "data": <result>, "error": null }
```

## Configuration

**POST /config** — Set physics and game config
```json
{
  "gravity": {"x": 0, "y": 0},
  "tile_size": 16,
  "move_speed": 200,
  "jump_velocity": 400,
  "fall_multiplier": 1.5,
  "coyote_frames": 5,
  "jump_buffer_frames": 4
}
```
For top-down games, set gravity to `{"x": 0, "y": 0}`. For platformers, use `{"x": 0, "y": -980}`.

**GET /config** — Read current config

## Level Generation

**POST /generate** — Generate a procedural level
```json
{
  "template": "top_down_dungeon",
  "difficulty": 0.3,
  "seed": 42,
  "width": 30,
  "height": 25,
  "constraints": ["top_down_reachable", "bounds_check"]
}
```

Templates: `platformer`, `top_down_dungeon`, `rts_arena`, `fighting_arena`, `metroidvania`, `roguelike_floor`, `puzzle_platformer`, `arena_waves`, `side_scroller`, `tower_defense_map`, `boss_arena`

Constraints: `reachable`, `top_down_reachable`, `bounds_check`, `has_ground`, `no_softlock`

Returns: `{ tilemap: { width, height, tiles }, player_spawn: [x,y], goal: [tx,ty], validation, difficulty_metrics }`

**POST /level** — Load a tilemap
```json
{
  "width": 30, "height": 25,
  "tiles": [0, 1, 1, ...],
  "player_spawn": [104, 184],
  "goal": [400, 56]
}
```

## Tile Types

| ID | Type | Notes |
|----|------|-------|
| 0 | Empty | Walkable floor |
| 1 | Solid | Wall |
| 2 | Spike | Damage on contact |
| 3 | Goal | Triggers goal event |
| 4 | Platform | One-way (platformer only) |
| 5 | SlopeUp | Diagonal surface |
| 6 | SlopeDown | Diagonal surface |
| 7 | Ladder | Climbable |

## Visual Rendering (Windowed Mode)

### Entity Sprites

Entities spawned via the API **automatically get colored rectangle sprites** based on their tags — no sprite sheets required for things to be visible. The engine assigns colors by tag:

| Tag | Color | RGB |
|-----|-------|-----|
| `enemy` | Red | (0.9, 0.2, 0.2) |
| `pickup` or `health` | Green | (0.2, 0.9, 0.2) |
| `projectile` | Yellow | (0.9, 0.9, 0.2) |
| `npc` | Cyan | (0.2, 0.8, 0.9) |
| *(default)* | Gray | (0.7, 0.7, 0.7) |

The player entity always gets a blue sprite. The rectangle size matches the entity's collider dimensions. If you upload a sprite sheet and set an animation_controller, the sprite sheet takes priority over the fallback color.

### UI Screen Rendering

UI screens defined via `POST /ui/screens` **now render visually** in windowed mode. Supported node types that render:

- **`text`** — Renders as Bevy text with font_size and color
- **`progress_bar`** — Renders as a colored bar (background + fill based on value/max)
- **`panel`** — Renders as a colored rectangle, supports children
- **`container`** — Layout container with flex direction (row/column) and gap, supports children

Node types `image`, `button`, `dialogue_box`, and `slot` are **not yet rendered** — they exist in the data model but won't appear visually.

**Position anchoring works:** `{"Anchored": {"anchor": "top_left", "offset": [16, 16]}}` positions the node absolutely. Supported anchors: `top_left`, `top_right`, `bottom_left`, `bottom_right`, `center`.

**Size works:** `{"fixed": [200, 20]}` sets pixel dimensions. Progress bars default to 120x14 if no size given.

**Colors:** Named colors (`white`, `black`, `red`, `green`, `blue`, `yellow`, `dark_red`, `dark_green`, `gray`) and hex (`#FF0000`, `#FF000080` with alpha) are supported.

## Entities

**POST /entities** — Spawn a custom entity
```json
{
  "x": 100, "y": 50,
  "is_player": false,
  "tags": ["enemy", "zombie"],
  "script": "zombie_ai",
  "components": [
    {"type": "collider", "width": 12, "height": 14},
    {"type": "top_down_mover", "speed": 100},
    {"type": "health", "current": 3, "max": 3},
    {"type": "contact_damage", "amount": 1, "cooldown_frames": 20, "knockback": 80, "damage_tag": "player"}
  ]
}
```

**POST /entities/preset** — Spawn from preset
```json
{
  "preset": "chase_enemy",
  "x": 200, "y": 100,
  "config": {
    "health": 5,
    "speed": 120,
    "contact_damage": 2,
    "detection_radius": 250,
    "script": "zombie_ai",
    "tags": ["enemy", "zombie"]
  }
}
```

**GET /entities** — List all entities
**GET /entities/{id}** — Get one entity
**DELETE /entities/{id}** — Remove entity
**POST /entities/{id}/damage** — Deal damage: `{"amount": 1}`
**POST /entities/reset_non_player** — Remove all non-player entities

### Entity Presets

| Preset | Description | Key Components |
|--------|-------------|----------------|
| `platformer_player` | Side-scrolling player | Collider, GravityBody, HorizontalMover, Jumper, Health(3) |
| `top_down_player` | Top-down player | Collider, TopDownMover(200), Health(3) |
| `patrol_enemy` | Walks between waypoints | HorizontalMover, AiBehavior(Patrol), Health(2), ContactDamage(1) |
| `chase_enemy` | Chases player | TopDownMover, AiBehavior(Chase), Health(2), ContactDamage(1) |
| `guard_enemy` | Guards a position | TopDownMover, AiBehavior(Guard), Health(3), ContactDamage(1) |
| `turret` | Stationary shooter | AiBehavior(Guard, speed=0), Health(3), ContactDamage(1) |
| `flying_enemy` | Fast aerial enemy | AiBehavior(Chase, speed=165), Health(1), ContactDamage(1) |
| `boss` | Large boss entity | Collider(22x26), Health(20), ContactDamage(2), Hitbox(28x24) |
| `health_pickup` | Heals on contact | Pickup(heal: 1.0) |
| `moving_platform` | Moving solid surface | MovingPlatform(ping_pong) |

### Component Types

- `collider` — `{width, height}`
- `gravity_body` — no fields (enables gravity)
- `horizontal_mover` — `{speed, left_action?, right_action?}`
- `jumper` — `{velocity, action?, fall_multiplier?, variable_height?, coyote_frames?, buffer_frames?}`
- `top_down_mover` — `{speed, up_action?, down_action?, left_action?, right_action?}`
- `health` — `{current, max}`
- `contact_damage` — `{amount, cooldown_frames?, knockback?, damage_tag}`
- `trigger_zone` — `{radius, trigger_tag, event_name, one_shot?}`
- `pickup` — `{pickup_tag, effect: {type: "heal"|"score_add"|"custom", amount?, name?}}`
- `projectile` — `{speed, direction: {x,y}, lifetime_frames, damage, owner_id, damage_tag}`
- `hitbox` — `{width, height, offset?: {x,y}, active?, damage, damage_tag}`
- `moving_platform` — `{waypoints: [{x,y}], speed, loop_mode?: "loop"|"ping_pong", pause_frames?, carry_riders?}`
- `animation_controller` — `{graph, state?, frame?, speed?, playing?, facing_right?, auto_from_velocity?}`
- `path_follower` — `{target: {x,y}, recalculate_interval?, path_type?: "top_down"|"platformer", speed}`
- `ai_behavior` — `{behavior: <see below>}`
- `particle_emitter` — particle effect definition

### AI Behaviors (for ai_behavior component)

- Patrol: `{type: "patrol", waypoints: [{x,y}], speed}`
- Chase: `{type: "chase", target_tag, speed, detection_radius, give_up_radius, require_line_of_sight?}`
- Flee: `{type: "flee", threat_tag, speed, detection_radius, give_up_radius}`
- Guard: `{type: "guard", position: {x,y}, radius, chase_radius, speed, target_tag}`
- Wander: `{type: "wander", speed, radius, pause_frames?}`
- Custom: `{type: "custom", script: "script_name"}`

## Scripts

**POST /scripts** — Upload a Lua script
```json
{"name": "zombie_ai", "source": "function update(entity, world, dt) ... end", "global": false}
```

- Entity scripts: `function update(entity, world, dt)` — runs per-entity per-frame
- Global scripts: `function update(world, dt)` — runs once per frame, set `"global": true`

**GET /scripts** — List loaded scripts
**DELETE /scripts/{name}** — Remove script
**POST /scripts/{name}/test** — Dry-run syntax check
**GET /scripts/errors** — Recent script errors
**DELETE /scripts/errors** — Clear all script errors (errors also auto-clear when a script is re-uploaded)
**POST /scripts/vars** — Set game variables: `{"score": 0, "wave": 1}`
**GET /scripts/vars** — Read game variables

## Lua Scripting API

### Entity Properties (read/write)

```lua
entity.id            -- NetworkId (read-only)
entity.x, entity.y   -- position
entity.vx, entity.vy -- velocity
entity.grounded      -- on ground (read-only)
entity.alive         -- alive status
entity.health        -- current health
entity.max_health    -- max health (read-only)
entity.state         -- persistent per-entity table (survives between frames)
entity.animation     -- current animation state name
entity.flip_x        -- facing direction
```

### Entity Methods

```lua
entity.has_tag("enemy")      -- check tag
entity.add_tag("burning")    -- add tag
entity.remove_tag("burning") -- remove tag
entity.damage(amount)        -- deal damage, returns new health
entity.heal(amount)          -- heal, returns new health
entity.knockback(dx, dy)     -- velocity impulse
entity.follow_path(path)     -- follow waypoint array
```

### Entity Hitbox (if hitbox component exists)

```lua
entity.hitbox.active = true
entity.hitbox.damage = 2
entity.hitbox.width, entity.hitbox.height
entity.hitbox.damage_tag
```

### World — Tile Queries

```lua
world.is_solid(x, y)        -- true if solid tile
world.is_platform(x, y)     -- true if one-way platform
world.is_climbable(x, y)    -- true if ladder/climbable
world.get_tile(x, y)        -- raw tile ID
world.tile_friction(x, y)   -- friction value
world.set_tile(x, y, id)    -- modify tilemap at runtime
```

### World — Entity Queries

```lua
world.player()                          -- player entity or nil
world.get_entity(id)                    -- entity by NetworkId or nil
world.find_all("zombie")               -- all entities with tag
world.find_all()                        -- all entities
world.find_in_radius(x, y, r, "enemy") -- entities in radius with tag
world.find_in_radius(x, y, r)          -- entities in radius (any tag)
world.find_nearest(x, y, "enemy")      -- nearest entity with tag
world.find_nearest(x, y)               -- nearest entity
```

**Queried entity properties and methods** (available on results from all query functions above):
```lua
local target = world.find_nearest(entity.x, entity.y, "enemy")
if target then
    target.id          -- NetworkId
    target.x, target.y -- position
    target.vx, target.vy -- velocity
    target.health      -- current health (nil if no Health component)
    target.max_health  -- max health
    target.alive       -- bool
    target.grounded    -- bool
    target.tags        -- table {tag_name = true}
    target.has_tag("zombie")   -- check tag
    target.damage(amount)      -- apply damage (reduces health, sets alive=false at 0)
    target.heal(amount)        -- heal (capped at max_health)
    target.knockback(dx, dy)   -- apply velocity impulse
end
```

### World — Raycasting & Pathfinding

```lua
local hit = world.raycast(ox, oy, dx, dy, max_dist)
-- hit = {x, y, tile_x, tile_y, distance, normal_x, normal_y} or nil

local hits = world.raycast_entities(ox, oy, dx, dy, max_dist, "enemy")
-- hits = [{id, x, y, distance}]

local path = world.find_path(from_x, from_y, to_x, to_y, "top_down")
-- path = [{x, y}]

local clear = world.line_of_sight(x1, y1, x2, y2) -- bool
```

### World — Spawning

```lua
-- Simple: component names as strings (default values)
world.spawn({
    x = 100, y = 50,
    components = {"collider", "gravity_body"},
    tags = {"enemy", "zombie"},
    script = "zombie_ai",
    health = 3,
    is_player = false,
})

-- Advanced: component tables with config
world.spawn({
    x = 100, y = 50,
    components = {
        {type = "collider", width = 12, height = 14},
        {type = "contact_damage", amount = 2, cooldown_frames = 20, knockback = 0, damage_tag = "player"},
        {type = "top_down_mover", speed = 90},
    },
    tags = {"enemy", "zombie"},
    script = "zombie_ai",
    health = 3,
})

world.spawn_projectile({
    x = entity.x, y = entity.y,
    speed = 300,
    direction = {x = 1, y = 0},
    damage = 1,
    damage_tag = "enemy",
    owner = entity.id,
    lifetime = 60,
})

world.despawn(entity_id)

world.spawn_particles("explosion", x, y)
```

### World — Events

```lua
world.emit("zombie_killed", {id = entity.id, score = 10})

world.on("zombie_killed", function(data)
    -- data.id, data.score
end)
```

### World — Input

```lua
world.input.pressed("left")        -- held down this frame
world.input.just_pressed("attack")  -- pressed this frame only
```

Actions: `left`, `right`, `up`, `down`, `jump`, `attack`

### World — Variables

```lua
world.get_var("score")           -- read global variable
world.set_var("score", 100)      -- write global variable
```

### World — Audio

```lua
world.play_sfx("hit")
world.play_sfx("hit", {volume = 0.5, pitch = 1.2})
world.play_music("dungeon", {fade_in = 2.0})
world.stop_music({fade_out = 1.0})
world.set_volume("master", 0.8)   -- "master", "sfx", "music"
```

### World — Camera

```lua
world.camera.shake(5.0, 0.3)      -- intensity, duration
world.camera.zoom(2.0)
world.camera.look_at(200, 100)
```

### World — UI

```lua
world.ui.show_screen("hud")
world.ui.hide_screen("game_over")
world.ui.set_text("score_label", "Score: 42")
world.ui.set_progress("health_bar", 3, 10)
```

### World — Dialogue

```lua
world.dialogue.start("shopkeeper_intro")
world.dialogue.choose(0)
```

### World — Game State

```lua
world.game.state                  -- current state string
world.game.pause()
world.game.resume()
world.game.transition("GameOver", {effect = "FadeBlack", duration = 0.5})
```

## Simulation

**POST /simulate** — Run headless simulation
```json
{
  "inputs": [
    {"frame": 0, "action": "right", "duration": 60},
    {"frame": 30, "action": "attack", "duration": 5}
  ],
  "max_frames": 300,
  "record_interval": 10,
  "goal_position": [400, 56],
  "goal_radius": 12
}
```

Returns: `{outcome, frames_elapsed, trace: [{x,y,vx,vy,grounded,frame}], events, entity_events, entity_states}`

## Camera

**POST /camera/config** — `{zoom, follow_speed, bounds: {min_x,max_x,min_y,max_y}, follow_target: entity_id}` (omit `bounds` for auto)
**POST /camera/shake** — `{intensity, duration, decay?}`
**POST /camera/look_at** — `{x, y, speed?}`
**GET /camera/state**

## UI Screens

**POST /ui/screens** — Define a UI screen
```json
{
  "name": "hud",
  "layer": 0,
  "nodes": [
    {"id": "health", "node_type": {"type": "progress_bar", "value": 10, "max": 10, "color": "red", "bg_color": "dark_red"}, "position": {"Anchored": {"anchor": "top_left", "offset": [16, 16]}}, "size": {"fixed": [200, 20]}},
    {"id": "score", "node_type": {"type": "text", "text": "Score: 0", "font_size": 24, "color": "white"}, "position": {"Anchored": {"anchor": "top_right", "offset": [-16, 16]}}}
  ]
}
```
**POST /ui/screens/{name}/show**
**POST /ui/screens/{name}/hide**
**POST /ui/screens/{name}/nodes/{id}** — Update node

`node_type` valid types: `panel`, `text`, `image`, `button`, `progress_bar`, `container`, `dialogue_box`, `slot`
Format: `{"type": "type_name", ...params}` (internally-tagged, snake_case)

## Audio

**POST /audio/sfx** — Define sound effects: `{effects: {name: {path, volume?, pitch_variance?}}}`
**POST /audio/music** — Define tracks: `{tracks: {name: {path, volume?, looping?}}}`
**POST /audio/play** — Play: `{sfx: "hit"}` or `{music: "theme", fade_in: 2}`
**POST /audio/triggers** — Map events to sounds: `{mappings: {"entity_died": "death_sfx"}}`

## Sprites

**POST /sprites/sheets** — Define sprite sheet with animations
```json
{
  "name": "zombie",
  "path": "assets/zombie.png",
  "frame_width": 32, "frame_height": 32, "columns": 8,
  "animations": {
    "idle": {"frames": [0,1,2,3], "fps": 8, "looping": true},
    "walk": {"frames": [4,5,6,7], "fps": 12, "looping": true},
    "die": {"frames": [8,9,10], "fps": 10, "looping": false}
  }
}
```

## Save/Load

**POST /save** — `{slot: "save1"}`
**POST /load** — `{slot: "save1"}`
**GET /saves** — List save slots

## Game State Machine

**GET /game/state** — Current state
**POST /game/state** — Set state: `{state: "Playing"}`
**POST /game/transition** — `{to: "GameOver", effect: "FadeBlack", duration: 0.5}`
**POST /game/restart** — Restart level

States: `Loading`, `Menu`, `Playing`, `Paused`, `GameOver`, `LevelTransition`, `Cutscene`

## Events

**GET /events** — Recent game events
**GET /events/subscribe** — SSE stream of events

## Debugging

**GET /perf** — Performance stats (fps, entity count, frame times)
**GET /screenshot** — PNG screenshot
**POST /debug/overlay** — `{show: true, features: ["colliders", "paths"]}`
**POST /scene/describe** — Structured scene description (entities, UI state, camera)

## Validation

**POST /validate** — `{constraints: ["top_down_reachable", "bounds_check"]}`

## Level Packs (Campaigns)

**POST /levels/pack** — Define multi-level campaign
**POST /levels/pack/{name}/start** — Start campaign
**POST /levels/pack/{name}/next** — Next level
**GET /levels/pack/{name}/progress** — Progress info

## Export

**POST /export/web** — Export as browser-playable HTML/WASM
**POST /export/desktop** — Export as native executable

---

## Engine Issues Log

When you hit an Axiom engine limitation, API bug, missing feature, or confusing behavior,
append it to `engine_feedback.md` in this folder. Format:

```
### [BUG] Short title
- **Endpoint/Feature:** e.g. POST /entities/preset
- **What happened:** Description of what went wrong
- **Expected:** What should have happened
- **Workaround:** How you worked around it (if applicable)

### [MISSING] Short title
- **What's needed:** Feature that should exist but doesn't
- **Use case:** Why the game needs it
- **Suggested API:** What the endpoint/function could look like

### [UNCLEAR] Short title
- **What was confusing:** API behavior that was surprising or undocumented
- **What you tried:** Steps that led to confusion
- **What worked:** How you figured it out
```

Do this automatically whenever you encounter an issue — don't just work around it silently.
The engine developer will read `engine_feedback.md` to improve the engine.
