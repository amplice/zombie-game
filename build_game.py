#!/usr/bin/env python3
"""
Zombie Survival — singleplayer DayZ-like, built on the Axiom engine.

Large open world with towns, forests, military zones, and ambient zombies.

Run the engine first:
    cd axiom && cargo run

Then build the game:
    cd zombie-game && python build_game.py
"""

import sys
import os
import re
import json
import random
import math
import time
from pathlib import Path
from axiom_client import AxiomClient
from PIL import Image, ImageDraw


# ── Mode ────────────────────────────────────────────────────────────
TILE_TEST_MODE = True  # True = small map, no scripts/zombies/loot, ground tiles only

# ── Map dimensions ──────────────────────────────────────────────────
TILE_SIZE = 32
MAP_W, MAP_H = (40, 40) if TILE_TEST_MODE else (200, 140)

# ── Isometric tile dimensions ────────────────────────────────────────
ISO_TILE_W = 128   # Diamond base width (pixels)
ISO_TILE_H = 64    # Diamond base height (pixels)

USE_PRIMITIVE_RUNTIME_PROFILE = True
PRIMITIVE_TEACHER_DIR = Path(
    os.environ.get(
        "ZOMBIE_PRIMITIVE_TEACHER_DIR",
        r"C:\Users\cobra\axiom\artifacts\ground_autotile\primitive_teacher",
    )
)
# Legacy single-profile path (still works if pointed at old location)
PRIMITIVE_RUNTIME_PROFILE_JSON = Path(
    os.environ.get(
        "ZOMBIE_GRASS_PRIMITIVE_RUNTIME_JSON",
        str(PRIMITIVE_TEACHER_DIR / "grass_G" / "border_primitives_runtime.json"),
    )
)

# ── Material registration ──────────────────────────────────────────
# Each entry: (series_letter, family_count, label, minimap_color)
MATERIAL_FAMILIES = [
    ("A", 16, "dirt_water", [0.30, 0.25, 0.18]),
    ("G", 10, "grass", [0.35, 0.52, 0.28]),
    # Uncomment after teaching with the primitive teacher:
    # ("B", 14, "brown_earth", [0.42, 0.33, 0.22]),
    # ("C", 4, "stone", [0.55, 0.53, 0.50]),
    # ("F", 5, "dark_earth", [0.25, 0.22, 0.18]),
]

# Runtime profiles to load (series -> subdir). Profiles that don't exist are skipped.
MATERIAL_PROFILES = {
    "G": "grass_G",
    "A": "dirt_water_A",
    # "B": "brown_earth_B",
    # "C": "stone_C",
}

# Easy local debug toggles (preferred over env vars during tile-mapping iteration)
# Set to one of: "off", "edge", "outside_corner", "inside_corner", "all"
PRIMITIVE_DEBUG_MODE = "off"

# Optional env overrides (still supported for scripted runs)
_env_primitive_debug_mode = os.environ.get("ZOMBIE_PRIMITIVE_DEBUG_MODE", "").strip().lower()
_env_rect_debug = os.environ.get("ZOMBIE_RECTANGLE_EDGE_DEBUG_TEST", "").strip()
_env_primitive_step = os.environ.get("ZOMBIE_PRIMITIVE_DEBUG_STEP", "").strip().lower()

if _env_primitive_debug_mode:
    _primitive_debug_mode = _env_primitive_debug_mode
elif _env_rect_debug == "1":
    # Back-compat path for older env flags
    _primitive_debug_mode = _env_primitive_step or "all"
else:
    _primitive_debug_mode = (PRIMITIVE_DEBUG_MODE or "off").strip().lower()

if _primitive_debug_mode not in {"off", "edge", "outside_corner", "inside_corner", "all"}:
    _primitive_debug_mode = "off"

RECTANGLE_EDGE_DEBUG_TEST = _primitive_debug_mode != "off"
PRIMITIVE_DEBUG_STEP = "" if _primitive_debug_mode in {"off", "all"} else _primitive_debug_mode


def _tile_key_to_name(tile_key):
    """Convert tile key like G4_S into source asset basename Ground G4_S.png (without .png)."""
    m = re.match(r"^([A-Z])(\d+)_([NESW])$", str(tile_key))
    if not m:
        return None
    series, idx, d = m.group(1), int(m.group(2)), m.group(3)
    return f"Ground {series}{idx}_{d}"


def _parse_tile_key(tile_key):
    m = re.match(r"^([A-Z])(\d+)_([NESW])$", str(tile_key))
    if not m:
        return None
    return (m.group(1), int(m.group(2)), m.group(3))


def mask8_at(is_grass, width, height, x, y):
    def at(xx, yy):
        if xx < 0 or yy < 0 or xx >= width or yy >= height:
            return False
        return bool(is_grass[yy * width + xx])
    m = 0
    if at(x, y - 1): m |= 1
    if at(x + 1, y - 1): m |= 2
    if at(x + 1, y): m |= 4
    if at(x + 1, y + 1): m |= 8
    if at(x, y + 1): m |= 16
    if at(x - 1, y + 1): m |= 32
    if at(x - 1, y): m |= 64
    if at(x - 1, y - 1): m |= 128
    return m


def classify_border_primitive(mask8):
    """Grid-frame primitive classification aligned with the primitive teacher runtime export."""
    m = int(mask8) & 0xFF
    n = 1 if (m & 1) else 0
    ne = 1 if (m & 2) else 0
    e = 1 if (m & 4) else 0
    se = 1 if (m & 8) else 0
    s = 1 if (m & 16) else 0
    sw = 1 if (m & 32) else 0
    w = 1 if (m & 64) else 0
    nw = 1 if (m & 128) else 0
    orth = {"N": n, "E": e, "S": s, "W": w}
    orth_count = n + e + s + w
    if orth_count == 4:
        missing = []
        if not ne: missing.append("NE")
        if not se: missing.append("SE")
        if not sw: missing.append("SW")
        if not nw: missing.append("NW")
        if len(missing) == 0:
            return None
        if len(missing) == 1:
            return ("inside_corner", missing[0])
        return ("degenerate", "default")
    if orth_count == 3:
        for side in ("N", "E", "S", "W"):
            if not orth[side]:
                return ("edge", side)
    if orth_count == 2:
        if n and e: return ("outside_corner", "SW")
        if e and s: return ("outside_corner", "NW")
        if s and w: return ("outside_corner", "NE")
        if w and n: return ("outside_corner", "SE")
        return ("degenerate", "default")
    return ("degenerate", "default")


def load_primitive_runtime_profile(path, tile_ids):
    """Load a border primitive runtime profile (v2 or v3 format)."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"   Failed to read primitive runtime profile: {exc}")
        return None
    fmt = str(payload.get("format", ""))
    if fmt not in ("zombie_game_grass_border_primitives_runtime", "axiom_border_primitives_runtime"):
        print(f"   Primitive runtime profile has unexpected format '{fmt}'; ignoring")
        return None
    slots = (payload.get("compiled_grid_frame_slots") or {})
    out = {
        "fill": int(tile_ids.get(str(slots.get("fill", "")), 0)),
        "fallback": int(tile_ids.get(str(slots.get("fallback", "")), 0)),
        "edge": {},
        "outside_corner": {},
        "inside_corner": {},
        "degenerate": {"default": 0},
    }
    for orient in ("N", "E", "S", "W"):
        tk = str(((slots.get("edge") or {}).get(orient)) or "")
        out["edge"][orient] = int(tile_ids.get(tk, 0))
    for orient in ("NE", "SE", "SW", "NW"):
        tk_o = str(((slots.get("outside_corner") or {}).get(orient)) or "")
        tk_i = str(((slots.get("inside_corner") or {}).get(orient)) or "")
        out["outside_corner"][orient] = int(tile_ids.get(tk_o, 0))
        out["inside_corner"][orient] = int(tile_ids.get(tk_i, 0))
    tk_d = str((((slots.get("degenerate") or {}).get("default")) or ""))
    out["degenerate"]["default"] = int(tile_ids.get(tk_d, 0))
    if not out["fill"]:
        print("   Primitive runtime profile did not map to current tile IDs; ignoring")
        return None
    inv_tile_ids = {v: k for k, v in tile_ids.items()}
    edge_dbg = ", ".join(
        f"{o}->{inv_tile_ids.get(out['edge'].get(o, 0), '?')}"
        for o in ("N", "E", "S", "W")
    )
    mat = payload.get("material") or {}
    label = mat.get("label", "?")
    print(f"   Using {label} primitive runtime profile from: {path}")
    print(f"   Primitive edge slots (grid): {edge_dbg}")
    return out


def iso_grid_to_world(col, row):
    """Convert grid (col, row) to isometric world (x, y) — matches engine's grid_to_world."""
    x = (col - row) * ISO_TILE_W * 0.5
    y = (col + row) * ISO_TILE_H * 0.5
    return (x, y)


def load_script(client, name, path, global_script=False):
    source = Path(path).read_text()
    result = client.post("/scripts", {"name": name, "source": source, "global": global_script})
    if not result.get("ok"):
        print(f"  ERROR loading script '{name}': {result.get('error')}")
        return False
    return True


# ── World Generation ────────────────────────────────────────────────

def apply_material_profile(tiles, is_material, width, height, profile_ids, fallback_fill,
                           tile_ids=None, primitive_pick_counts=None, primitive_kind_counts=None,
                           debug_step=""):
    """Classify borders and assign tile IDs for one material layer.

    For each cell where is_material[cell] is True, compute the 8-neighbor mask
    against the is_material boolean array, classify the border primitive, and
    write the appropriate tile ID from profile_ids into tiles[].

    Non-material cells are left untouched.
    """
    if primitive_pick_counts is None:
        primitive_pick_counts = {}
    if primitive_kind_counts is None:
        primitive_kind_counts = {}

    fill_id = profile_ids.get("fill", fallback_fill) or fallback_fill

    for y in range(height):
        for x in range(width):
            i = y * width + x
            if not is_material[i]:
                continue
            m8 = mask8_at(is_material, width, height, x, y)
            cls = classify_border_primitive(m8)
            if cls is None:
                tiles[i] = fill_id
                continue
            kind, orient = cls
            if debug_step and kind != debug_step:
                tiles[i] = fill_id
                continue
            if kind == "edge":
                tile_id = int(((profile_ids.get("edge") or {}).get(orient)) or 0)
            elif kind == "outside_corner":
                tile_id = int(((profile_ids.get("outside_corner") or {}).get(orient)) or 0)
            elif kind == "inside_corner":
                tile_id = int(((profile_ids.get("inside_corner") or {}).get(orient)) or 0)
            else:
                tile_id = int((((profile_ids.get("degenerate") or {}).get("default")) or 0))
            if not tile_id:
                tile_id = int(profile_ids.get("fallback", 0) or fill_id)
            tiles[i] = tile_id
            primitive_kind_counts[kind] = primitive_kind_counts.get(kind, 0) + 1
            primitive_pick_counts[(kind, orient, tile_id)] = primitive_pick_counts.get((kind, orient, tile_id), 0) + 1

    return primitive_pick_counts, primitive_kind_counts


def generate_world(width, height, seed=42, tile_ids=None, grass_primitive_profile_ids=None, material_profiles=None):
    """Generate terrain with three materials: grass, water, and dirt.

    material_profiles: dict of series -> profile_ids (from load_primitive_runtime_profile).
    grass_primitive_profile_ids: legacy param, equivalent to material_profiles["G"].

    Grass and water are never adjacent — a 2-cell dirt buffer always separates them.
    """
    rng = random.Random(seed)

    # Merge legacy param into material_profiles
    if material_profiles is None:
        material_profiles = {}
    if grass_primitive_profile_ids and "G" not in material_profiles:
        material_profiles["G"] = grass_primitive_profile_ids

    def tid(key, default=0):
        if isinstance(tile_ids, dict):
            return int(tile_ids.get(key, default))
        return int(default)

    # Fallback fixed IDs for legacy mode (when tile_ids is None).
    T_DIRT = tid("A1_S", 8)
    T_GRASS = tid("G1_S", 9)
    T_EDGE_N = tid("G2_N", 10)
    T_EDGE_E = tid("G3_E", 11)
    T_EDGE_S = tid("G4_S", 12)
    T_EDGE_W = tid("G5_W", 13)
    T_CORNER_NE = tid("G6_N", 14)
    T_CORNER_SE = tid("G7_E", 15)
    T_CORNER_SW = tid("G8_S", 16)
    T_CORNER_NW = tid("G9_W", 17)
    T_SPARSE = tid("G10_S", 18)

    def idx(tx, ty):
        return ty * width + tx

    use_profiles = bool(material_profiles.get("G"))

    # ── Step 1: Grass blobs ──────────────────────────────────────────
    # material_map: "G" = grass, "A" = water, "D" = dirt
    material_map = ["D"] * (width * height)

    if RECTANGLE_EDGE_DEBUG_TEST:
        x1 = max(3, width // 2 - 8)
        x2 = min(width - 4, width // 2 + 8)
        y1 = max(3, height // 2 - 6)
        y2 = min(height - 4, height // 2 + 6)
        for y in range(y1, y2 + 1):
            for x in range(x1, x2 + 1):
                material_map[idx(x, y)] = "G"
        print(f"   Rectangle edge debug test: grass rect=({x1},{y1})..({x2},{y2})")
    else:
        # Several grass blobs of varying size for organic terrain
        grass_blobs = []
        for _ in range(12):
            cx = rng.randint(3, width - 4)
            cy = rng.randint(3, height - 4)
            rx = rng.randint(4, 12)
            ry = rng.randint(4, 12)
            grass_blobs.append((cx, cy, rx, ry))

        for y in range(height):
            for x in range(width):
                for cx, cy, rx, ry in grass_blobs:
                    dx = (x - cx) / rx
                    dy = (y - cy) / ry
                    dist = dx * dx + dy * dy
                    jitter = rng.random() * 0.3
                    if dist < 1.0 + jitter:
                        material_map[idx(x, y)] = "G"
                        break

    # ── Step 1b: Remove thin grass strips ────────────────────────────
    def mat_at(tx, ty):
        if tx < 0 or ty < 0 or tx >= width or ty >= height:
            return "D"
        return material_map[idx(tx, ty)]

    changed = True
    while changed:
        changed = False
        for y in range(height):
            for x in range(width):
                if material_map[idx(x, y)] != "G":
                    continue
                n = mat_at(x, y - 1) == "G"
                s = mat_at(x, y + 1) == "G"
                e = mat_at(x + 1, y) == "G"
                w = mat_at(x - 1, y) == "G"
                if not (n or s) or not (e or w):
                    material_map[idx(x, y)] = "D"
                    changed = True

    # ── Step 1c: Water blobs (with 2-cell grass buffer) ──────────────
    if not RECTANGLE_EDGE_DEBUG_TEST:
        # Build buffer mask: cells within 2 tiles of any grass cell are off-limits for water
        grass_buffer = [False] * (width * height)
        for y in range(height):
            for x in range(width):
                if material_map[idx(x, y)] == "G":
                    for dy in range(-2, 3):
                        for dx in range(-2, 3):
                            nx, ny = x + dx, y + dy
                            if 0 <= nx < width and 0 <= ny < height:
                                grass_buffer[idx(nx, ny)] = True

        # Place ~4 smaller water blobs only where dirt AND outside buffer.
        # Find valid candidate cells (non-buffered dirt) for blob centers so
        # water actually appears even on small maps.
        valid_centers = []
        for y in range(4, height - 4):
            for x in range(4, width - 4):
                if not grass_buffer[idx(x, y)] and material_map[idx(x, y)] == "D":
                    valid_centers.append((x, y))
        rng.shuffle(valid_centers)

        water_blobs = []
        blob_target = min(4, len(valid_centers))
        # Space blob centers apart (min 6 cells) to get distinct ponds
        chosen = []
        for cx, cy in valid_centers:
            too_close = False
            for ox, oy in chosen:
                if abs(cx - ox) + abs(cy - oy) < 6:
                    too_close = True
                    break
            if not too_close:
                chosen.append((cx, cy))
                rx = rng.randint(3, 7)
                ry = rng.randint(3, 7)
                water_blobs.append((cx, cy, rx, ry))
                if len(water_blobs) >= blob_target:
                    break

        for y in range(height):
            for x in range(width):
                i = idx(x, y)
                if material_map[i] != "D" or grass_buffer[i]:
                    continue
                for cx, cy, rx, ry in water_blobs:
                    dx = (x - cx) / rx
                    dy = (y - cy) / ry
                    dist = dx * dx + dy * dy
                    jitter = rng.random() * 0.3
                    if dist < 1.0 + jitter:
                        material_map[i] = "A"
                        break

        # Remove thin water strips (same logic as grass)
        changed = True
        while changed:
            changed = False
            for y in range(height):
                for x in range(width):
                    if material_map[idx(x, y)] != "A":
                        continue
                    n = mat_at(x, y - 1) == "A"
                    s = mat_at(x, y + 1) == "A"
                    e = mat_at(x + 1, y) == "A"
                    w = mat_at(x - 1, y) == "A"
                    if not (n or s) or not (e or w):
                        material_map[idx(x, y)] = "D"
                        changed = True

    # ── Step 2: Derive boolean masks ─────────────────────────────────
    is_grass = [m == "G" for m in material_map]
    is_water = [m == "A" for m in material_map]

    # Debug: material cell counts
    n_grass = sum(is_grass)
    n_water = sum(is_water)
    n_dirt = width * height - n_grass - n_water
    print(f"   Material cells: grass={n_grass}, water={n_water}, dirt={n_dirt}")

    # ── Step 3: Assign tile IDs ──────────────────────────────────────
    tiles = [0] * (width * height)

    primitive_pick_counts = {}
    primitive_kind_counts = {}

    grass_profile = material_profiles.get("G")
    water_profile = material_profiles.get("A")

    if use_profiles:
        # Fill all dirt first
        for i in range(width * height):
            tiles[i] = T_DIRT

        # Apply grass profile
        if grass_profile:
            apply_material_profile(
                tiles, is_grass, width, height, grass_profile, T_GRASS,
                tile_ids=tile_ids,
                primitive_pick_counts=primitive_pick_counts,
                primitive_kind_counts=primitive_kind_counts,
                debug_step=PRIMITIVE_DEBUG_STEP,
            )

        # Apply water profile
        if water_profile:
            water_pick_counts = {}
            water_kind_counts = {}
            water_fill = water_profile.get("fill", T_DIRT) or T_DIRT
            apply_material_profile(
                tiles, is_water, width, height, water_profile, water_fill,
                tile_ids=tile_ids,
                primitive_pick_counts=water_pick_counts,
                primitive_kind_counts=water_kind_counts,
                debug_step=PRIMITIVE_DEBUG_STEP,
            )
            # Merge water stats into main counters for reporting
            for k, v in water_kind_counts.items():
                primitive_kind_counts[k] = primitive_kind_counts.get(k, 0) + v
            for k, v in water_pick_counts.items():
                primitive_pick_counts[k] = primitive_pick_counts.get(k, 0) + v
    else:
        # Legacy mask-based path (no profiles loaded)
        def grass_at(tx, ty):
            if tx < 0 or ty < 0 or tx >= width or ty >= height:
                return False
            return is_grass[idx(tx, ty)]

        for y in range(height):
            for x in range(width):
                if not is_grass[idx(x, y)]:
                    grass_neighbors = sum([
                        grass_at(x, y-1), grass_at(x, y+1),
                        grass_at(x-1, y), grass_at(x+1, y),
                    ])
                    if grass_neighbors >= 1 and rng.random() < 0.3:
                        tiles[idx(x, y)] = T_SPARSE
                    else:
                        tiles[idx(x, y)] = T_DIRT
                    continue

                n = not grass_at(x, y - 1)
                s = not grass_at(x, y + 1)
                e = not grass_at(x + 1, y)
                w = not grass_at(x - 1, y)

                if not n and not s and not e and not w:
                    tiles[idx(x, y)] = T_GRASS
                elif n and not s and not e and not w:
                    tiles[idx(x, y)] = T_EDGE_N
                elif not n and not s and e and not w:
                    tiles[idx(x, y)] = T_EDGE_E
                elif not n and s and not e and not w:
                    tiles[idx(x, y)] = T_EDGE_S
                elif not n and not s and not e and w:
                    tiles[idx(x, y)] = T_EDGE_W
                elif n and e:
                    tiles[idx(x, y)] = T_CORNER_NE
                elif s and e:
                    tiles[idx(x, y)] = T_CORNER_SE
                elif s and w:
                    tiles[idx(x, y)] = T_CORNER_SW
                elif n and w:
                    tiles[idx(x, y)] = T_CORNER_NW
                else:
                    tiles[idx(x, y)] = T_SPARSE

    if primitive_kind_counts:
        print("   Primitive placement counts:", ", ".join(f"{k}:{v}" for k, v in sorted(primitive_kind_counts.items(), key=lambda kv: (-kv[1], kv[0]))))
    if primitive_pick_counts:
        top = sorted(primitive_pick_counts.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1], kv[0][2]))[:12]
        inv_tile_ids = {v: k for k, v in (tile_ids or {}).items()} if tile_ids else {}
        print(
            "   Primitive tile picks (top): " +
            ", ".join(
                f"{k}/{o}->{inv_tile_ids.get(tid, tid)}:{c}"
                for (k, o, tid), c in top
            )
        )

    return tiles


# Walkable tile types (ground-only test uses only walkable tile IDs; keep broad for dynamic registration)
WALKABLE_TILES = set(range(256))


def find_zombie_spawns(tiles, width, height, player_spawn, count, rng, min_dist=80):
    """Find random walkable tiles for zombie placement, away from player."""
    px, py = player_spawn
    spawns = []
    for _ in range(count * 50):
        tx = rng.randint(2, width - 3)
        ty = rng.randint(2, height - 3)
        if tiles[ty * width + tx] not in WALKABLE_TILES:
            continue
        wx, wy = iso_grid_to_world(tx, ty)
        dist = math.sqrt((wx - px) ** 2 + (wy - py) ** 2)
        if dist >= min_dist:
            spawns.append((wx, wy))
            if len(spawns) >= count:
                break
    return spawns


def find_loot_spawns(tiles, width, height, rng, count=20):
    """Find floor tiles near walls (inside buildings) for loot placement."""
    solid_tiles = {1, 10}  # wall, fence
    candidates = []
    for ty in range(2, height - 2):
        for tx in range(2, width - 2):
            tile = tiles[ty * width + tx]
            if tile not in WALKABLE_TILES:
                continue
            # Check if adjacent to a wall (likely inside a building)
            adj_walls = 0
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                if tiles[(ty + dy) * width + (tx + dx)] in solid_tiles:
                    adj_walls += 1
            if adj_walls >= 2:
                candidates.append(iso_grid_to_world(tx, ty))
    rng.shuffle(candidates)
    return candidates[:count]


# ── Decoration placement ───────────────────────────────────────────

def find_decoration_positions(tiles, width, height, rng):
    """Scan generated tiles to find positions for decoration entities.
    Returns dict of category -> list of (world_x, world_y) positions.
    Also modifies tiles in-place: converts some tree solid tiles to grass.
    """
    tree_positions = []
    car_positions = []
    flora_positions = []
    object_positions = []
    fence_entity_positions = []

    def idx(tx, ty):
        return ty * width + tx

    def get(tx, ty):
        if 0 <= tx < width and 0 <= ty < height:
            return tiles[idx(tx, ty)]
        return 1

    def count_neighbors(tx, ty, tile_type):
        """Count orthogonal + diagonal neighbors of given type."""
        n = 0
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                if dx == 0 and dy == 0:
                    continue
                if get(tx + dx, ty + dy) == tile_type:
                    n += 1
        return n

    # Tile constants (must match generate_world)
    T_WALL = 1
    T_GRASS = 2
    T_EARTH_DARK = 3
    T_ROAD = 6
    T_GRAVEL = 7
    T_FLOOR = 9
    T_FENCE = 10

    # ── Trees: find isolated solid tiles (scattered forest, not building walls) ──
    tree_candidates = []
    for ty in range(2, height - 2):
        for tx in range(2, width - 2):
            if tiles[idx(tx, ty)] != T_WALL:
                continue
            solid_neighbors = count_neighbors(tx, ty, T_WALL)
            floor_neighbors = count_neighbors(tx, ty, T_FLOOR)
            if floor_neighbors == 0 and solid_neighbors <= 3:
                tree_candidates.append((tx, ty))

    rng.shuffle(tree_candidates)
    for i, (tx, ty) in enumerate(tree_candidates):
        tiles[idx(tx, ty)] = T_EARTH_DARK  # Remove wall tile — earth underneath (forest)
        if i < 250:
            wx, wy = iso_grid_to_world(tx, ty)
            tree_positions.append((wx, wy))

    # ── Cars: place along roads near buildings ──
    car_candidates = []
    for ty in range(2, height - 2):
        for tx in range(2, width - 2):
            if tiles[idx(tx, ty)] != T_ROAD:
                continue
            near_building = False
            for dx in range(-5, 6):
                for dy in range(-5, 6):
                    if get(tx + dx, ty + dy) == T_WALL:
                        near_building = True
                        break
                if near_building:
                    break
            if near_building:
                car_candidates.append((tx, ty))
    rng.shuffle(car_candidates)
    # Space cars out (min 8 tiles apart)
    for tx, ty in car_candidates:
        wx, wy = iso_grid_to_world(tx, ty)
        too_close = False
        for cx, cy in car_positions:
            if abs(wx - cx) < 8 * ISO_TILE_W and abs(wy - cy) < 8 * ISO_TILE_H:
                too_close = True
                break
        if not too_close:
            car_positions.append((wx, wy))
            if len(car_positions) >= 20:
                break

    # ── Flora: scatter on grass tiles, away from buildings ──
    flora_candidates = []
    for ty in range(2, height - 2):
        for tx in range(2, width - 2):
            if tiles[idx(tx, ty)] != T_GRASS:
                continue
            wall_near = False
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    if get(tx + dx, ty + dy) in (T_WALL, T_FENCE):
                        wall_near = True
                        break
                if wall_near:
                    break
            if not wall_near:
                flora_candidates.append((tx, ty))
    rng.shuffle(flora_candidates)
    for tx, ty in flora_candidates[:200]:
        wx, wy = iso_grid_to_world(tx, ty)
        flora_positions.append((wx, wy))

    # ── Objects: scatter inside buildings (floor tiles adjacent to 2+ walls) ──
    obj_candidates = []
    for ty in range(2, height - 2):
        for tx in range(2, width - 2):
            if tiles[idx(tx, ty)] != T_FLOOR:
                continue
            wall_count = 0
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                if get(tx + dx, ty + dy) == T_WALL:
                    wall_count += 1
            if wall_count >= 2:  # Corner of a room
                obj_candidates.append((tx, ty))
    rng.shuffle(obj_candidates)
    for tx, ty in obj_candidates[:50]:
        wx, wy = iso_grid_to_world(tx, ty)
        object_positions.append((wx, wy))

    return {
        "trees": tree_positions,
        "cars": car_positions,
        "flora": flora_positions,
        "objects": object_positions,
    }


# ── Zone definitions for zombie density ─────────────────────────────

ZONES = [
    {"name": "town", "cx": 100, "cy": 70, "radius": 140, "density": 0.8},
    {"name": "military", "cx": 170, "cy": 22, "radius": 120, "density": 1.5},
    {"name": "industrial", "cx": 25, "cy": 55, "radius": 110, "density": 0.6},
    {"name": "farm", "cx": 35, "cy": 115, "radius": 110, "density": 0.3},
    {"name": "residential", "cx": 170, "cy": 110, "radius": 110, "density": 0.5},
]


# ── Main build ──────────────────────────────────────────────────────

def build_game(client, seed=42):
    post = client.post
    get = client.get
    rng = random.Random(seed)

    print("=== ZOMBIE SURVIVAL — DayZ Edition ===\n")

    # 0. Cleanup
    print("0. Cleaning up...")
    try:
        post("/debug/overlay", {"show": True, "features": ["hitboxes", "colliders"]})
    except Exception:
        pass
    try:
        client.delete("/scripts/errors")
    except Exception:
        pass

    # 1. Top-down physics with isometric rendering
    print("1. Configuring top-down physics (isometric)...")
    post("/config", {
        "gravity": {"x": 0, "y": 0},
        "move_speed": 200,
        "tile_size": TILE_SIZE,
        "tile_mode": {"isometric": {"tile_width": ISO_TILE_W, "tile_height": ISO_TILE_H, "depth_sort": True}},
        "jump_velocity": 0,
        "fall_multiplier": 1.0,
        "coyote_frames": 0,
        "jump_buffer_frames": 0,
        "pixel_snap": True,
        "interpolate_transforms": True,
        "debug_mode": True,
        "screenshot_path": "C:/Users/cobra/zombie-game/screenshots",
        "asset_path": "C:/Users/cobra/zombie-game/assets",
    })

    # 1a. Background color — olive green to match tileset aesthetic
    post("/window", {"background": [0.38, 0.40, 0.30]})

    # 1b. Register directional ground tiles (A1 + G1..G10 with authored NESW variants)
    print("   Registering tile types with tilesets...")
    gen = "sprites/tilesets/generated"  # still used later for non-ground decoration assets
    src_rel = "sprites/tilesets/rural_tileset/Isometric Tiles"

    def ground_tileset_from_key(tile_key):
        base = _tile_key_to_name(tile_key)
        if not base:
            return None
        return {"path": f"{src_rel}/{base}.png", "tile_width": 128, "tile_height": 256, "columns": 1, "rows": 1}

    tile_types = []
    tile_ids = {}
    for i in range(8):
        tile_types.append({"name": f"reserved_{i}", "flags": 0, "color": None})

    next_id = 8
    for mat_series, mat_count, mat_label, mat_color in MATERIAL_FAMILIES:
        for i in range(1, mat_count + 1):
            for d in ("N", "E", "S", "W"):
                key = f"{mat_series}{i}_{d}"
                ts = ground_tileset_from_key(key)
                if not ts:
                    continue
                name = f"{mat_label}_{mat_series.lower()}{i}_{d.lower()}"
                tile_types.append({"name": name, "flags": 0, "color": mat_color, "tileset": ts})
                tile_ids[key] = next_id
                next_id += 1
    post("/config/tile_types", {"types": tile_types})
    print(f"   Registered {len(tile_ids)} directional ground tiles ({len(tile_types)} total incl. reserved)")

    # 2. Generate world
    print(f"2. Generating {MAP_W}x{MAP_H} open world (isometric)...")
    # Load material primitive profiles
    material_profiles = {}
    if USE_PRIMITIVE_RUNTIME_PROFILE:
        for mat_series, mat_subdir in MATERIAL_PROFILES.items():
            profile_path = PRIMITIVE_TEACHER_DIR / mat_subdir / "border_primitives_runtime.json"
            profile = load_primitive_runtime_profile(profile_path, tile_ids)
            if profile:
                material_profiles[mat_series] = profile
    grass_primitive_profile_ids = material_profiles.get("G")
    if RECTANGLE_EDGE_DEBUG_TEST:
        print(f"   Primitive debug mode: rectangle test (step={PRIMITIVE_DEBUG_STEP or 'all'})")
    tiles = generate_world(MAP_W, MAP_H, seed, tile_ids=tile_ids,
                           grass_primitive_profile_ids=grass_primitive_profile_ids,
                           material_profiles=material_profiles)
    spawn_x, spawn_y = iso_grid_to_world(MAP_W // 2, MAP_H // 2)
    print(f"   Player spawn: ({spawn_x:.0f}, {spawn_y:.0f})")

    # 2b. Decorations disabled — focusing on ground tiles only
    decorations = {"trees": [], "cars": [], "flora": [], "objects": []}

    # 3. Load level
    print("3. Loading level...")
    post("/level", {
        "width": MAP_W,
        "height": MAP_H,
        "tiles": tiles,
        "player_spawn": [spawn_x, spawn_y],
        "goal": [int(v) for v in iso_grid_to_world(1, 1)],
    })

    # 4. Clear entities
    print("4. Clearing entities...")
    try:
        post("/entities/reset_non_player", {})
    except Exception:
        pass
    for e in (get("/entities").get("data") or []):
        try:
            client.delete(f"/entities/{e['id']}")
        except Exception:
            pass

    # 5. Upload scripts
    if TILE_TEST_MODE:
        print("5. Loading player script only (tile test mode)...")
        scripts_dir = Path(__file__).parent / "scripts"
        combat_path = scripts_dir / "player_combat.lua"
        if combat_path.exists():
            ok = load_script(client, "player_combat", str(combat_path), global_script=False)
            print(f"   {'OK' if ok else 'FAIL'}: player_combat (entity)")
        else:
            print("   SKIP: player_combat.lua (not found)")
    else:
        print("5. Uploading scripts...")
        scripts_dir = Path(__file__).parent / "scripts"
        scripts = [
            ("zombie_ai", "zombie_ai.lua", False),
            ("zombie_manager", "zombie_manager.lua", True),
            ("player_combat", "player_combat.lua", False),
            ("game_rules", "game_rules.lua", True),
            ("game_restart", "game_restart.lua", True),
        ]
        for name, filename, is_global in scripts:
            path = scripts_dir / filename
            if path.exists():
                ok = load_script(client, name, str(path), global_script=is_global)
                print(f"   {'OK' if ok else 'FAIL'}: {name} ({'global' if is_global else 'entity'})")
            else:
                print(f"   SKIP: {filename} (not found)")

    # 6. HUD
    print("6. Creating HUD...")
    post("/ui/screens", {
        "name": "hud",
        "layer": 0,
        "nodes": [
            # Health bar
            {
                "id": "health_bar",
                "node_type": {"type": "progress_bar", "value": 10, "max": 10, "color": "red", "bg_color": "dark_red"},
                "position": {"Anchored": {"anchor": "top_left", "offset": [16, 16]}},
                "size": {"fixed": [160, 14]},
            },
            {
                "id": "health_text",
                "node_type": {"type": "text", "text": "HP: 10 / 10", "font_size": 11, "color": "white"},
                "position": {"Anchored": {"anchor": "top_left", "offset": [16, 34]}},
            },
            # Stamina bar
            {
                "id": "stamina_bar",
                "node_type": {"type": "progress_bar", "value": 100, "max": 100, "color": "yellow", "bg_color": "gray"},
                "position": {"Anchored": {"anchor": "top_left", "offset": [16, 52]}},
                "size": {"fixed": [120, 8]},
            },
            # Kills / score
            {
                "id": "kills_label",
                "node_type": {"type": "text", "text": "Kills: 0", "font_size": 14, "color": "white"},
                "position": {"Anchored": {"anchor": "top_right", "offset": [-16, 16]}},
            },
            {
                "id": "score_label",
                "node_type": {"type": "text", "text": "Score: 0", "font_size": 14, "color": "white"},
                "position": {"Anchored": {"anchor": "top_right", "offset": [-16, 34]}},
            },
            # Survival time
            {
                "id": "time_label",
                "node_type": {"type": "text", "text": "0:00", "font_size": 16, "color": "white"},
                "position": {"Anchored": {"anchor": "top_right", "offset": [-16, 54]}},
            },
            # Zone indicator
            {
                "id": "zone_label",
                "node_type": {"type": "text", "text": "Town Center", "font_size": 12, "color": "yellow"},
                "position": {"Anchored": {"anchor": "bottom_left", "offset": [16, -16]}},
            },
            # Zombie count nearby
            {
                "id": "threat_label",
                "node_type": {"type": "text", "text": "", "font_size": 11, "color": "red"},
                "position": {"Anchored": {"anchor": "bottom_left", "offset": [16, -32]}},
            },
            # Weapon info
            {
                "id": "weapon_label",
                "node_type": {"type": "text", "text": "Fists", "font_size": 12, "color": "white"},
                "position": {"Anchored": {"anchor": "bottom_right", "offset": [-16, -16]}},
            },
            # Pickup notification
            {
                "id": "pickup_text",
                "node_type": {"type": "text", "text": "", "font_size": 14, "color": "green"},
                "position": {"Anchored": {"anchor": "bottom_center", "offset": [0, -52]}},
            },
        ],
    })
    post("/ui/screens/hud/show")

    # 6b. Particle presets
    print("   Defining particle presets...")
    post("/particles/presets", {
        "name": "blood",
        "preset": {
            "color_start": [0.8, 0.1, 0.1, 1.0],
            "color_end": [0.5, 0.0, 0.0, 0.0],
            "size_start": 3.0,
            "size_end": 1.0,
            "lifetime": 0.4,
            "speed_min": 30.0,
            "speed_max": 80.0,
            "spread_angle": 360.0,
            "one_shot": True,
            "burst_count": 12,
            "gravity_multiplier": 0.0,
        },
    })
    post("/particles/presets", {
        "name": "slash",
        "preset": {
            "color_start": [1.0, 1.0, 0.8, 1.0],
            "color_end": [1.0, 0.8, 0.2, 0.0],
            "size_start": 4.0,
            "size_end": 1.0,
            "lifetime": 0.15,
            "speed_min": 60.0,
            "speed_max": 120.0,
            "spread_angle": 45.0,
            "one_shot": True,
            "burst_count": 6,
            "gravity_multiplier": 0.0,
        },
    })
    post("/particles/presets", {
        "name": "heal",
        "preset": {
            "color_start": [0.2, 1.0, 0.3, 1.0],
            "color_end": [0.1, 0.8, 0.2, 0.0],
            "size_start": 3.0,
            "size_end": 0.5,
            "lifetime": 0.6,
            "speed_min": 10.0,
            "speed_max": 40.0,
            "spread_angle": 360.0,
            "one_shot": True,
            "burst_count": 8,
            "gravity_multiplier": -0.5,
        },
    })

    # 7. Game Over screen
    print("7. Creating Game Over screen...")
    post("/ui/screens", {
        "name": "game_over",
        "layer": 10,
        "nodes": [
            {
                "id": "game_over_bg",
                "node_type": {"type": "panel", "color": "#000000AA"},
                "position": {"Anchored": {"anchor": "center", "offset": [0, 0]}},
                "size": {"fixed": [400, 250]},
                "children": [
                    {
                        "id": "game_over_title",
                        "node_type": {"type": "text", "text": "YOU DIED", "font_size": 48, "color": "red"},
                        "position": {"Anchored": {"anchor": "top_left", "offset": [100, 20]}},
                    },
                    {
                        "id": "survived_time",
                        "node_type": {"type": "text", "text": "Survived: 0:00", "font_size": 22, "color": "white"},
                        "position": {"Anchored": {"anchor": "top_left", "offset": [120, 80]}},
                    },
                    {
                        "id": "final_kills",
                        "node_type": {"type": "text", "text": "Zombies killed: 0", "font_size": 18, "color": "white"},
                        "position": {"Anchored": {"anchor": "top_left", "offset": [110, 120]}},
                    },
                    {
                        "id": "final_score",
                        "node_type": {"type": "text", "text": "Score: 0", "font_size": 18, "color": "white"},
                        "position": {"Anchored": {"anchor": "top_left", "offset": [140, 155]}},
                    },
                    {
                        "id": "restart_hint",
                        "node_type": {"type": "text", "text": "", "font_size": 16, "color": "yellow"},
                        "position": {"Anchored": {"anchor": "top_left", "offset": [95, 200]}},
                    },
                ],
            },
        ],
    })
    post("/ui/screens/game_over/hide")

    # 7b. Register sprite sheets for 8-directional character
    print("   Registering sprite sheets...")
    bat_anims = {
        "idle":           {"path": "sprites/character_with_bat/Idle.png",          "frames": list(range(15)), "fps": 10, "looping": True},
        "run":            {"path": "sprites/character_with_bat/Run.png",           "frames": list(range(15)), "fps": 12, "looping": True},
        "walk":           {"path": "sprites/character_with_bat/Walk.png",          "frames": list(range(15)), "fps": 10, "looping": True},
        "strafe_left":    {"path": "sprites/character_with_bat/StrafeLeft.png",    "frames": list(range(15)), "fps": 12, "looping": True},
        "strafe_right":   {"path": "sprites/character_with_bat/StrafeRight.png",   "frames": list(range(15)), "fps": 12, "looping": True},
        "run_backwards":  {"path": "sprites/character_with_bat/RunBackwards.png",  "frames": list(range(15)), "fps": 12, "looping": True},
        "attack": {"path": "sprites/character_with_bat/Attack3.png",    "frames": list(range(15)), "fps": 22, "looping": False, "next": "idle"},
        "hurt":   {"path": "sprites/character_with_bat/TakeDamage.png", "frames": list(range(15)), "fps": 18, "looping": False, "next": "idle"},
        "die":    {"path": "sprites/character_with_bat/Die.png",        "frames": list(range(15)), "fps": 8,  "looping": False},
    }
    post("/sprites/sheets", {
        "name": "player_bat",
        "path": "sprites/character_with_bat/Idle.png",
        "frame_width": 128, "frame_height": 128,
        "columns": 15, "rows": 8,
        "animations": bat_anims,
    })

    shotgun_anims = {
        "idle":           {"path": "sprites/character_with_shotgun/Idle.png",          "frames": list(range(15)), "fps": 10, "looping": True},
        "run":            {"path": "sprites/character_with_shotgun/Run.png",           "frames": list(range(15)), "fps": 12, "looping": True},
        "walk":           {"path": "sprites/character_with_shotgun/Walk.png",          "frames": list(range(15)), "fps": 10, "looping": True},
        "strafe_left":    {"path": "sprites/character_with_shotgun/StrafeLeft.png",    "frames": list(range(15)), "fps": 12, "looping": True},
        "strafe_right":   {"path": "sprites/character_with_shotgun/StrafeRight.png",   "frames": list(range(15)), "fps": 12, "looping": True},
        "run_backwards":  {"path": "sprites/character_with_shotgun/RunBackwards.png",  "frames": list(range(15)), "fps": 12, "looping": True},
        "attack":       {"path": "sprites/character_with_shotgun/Attack1.png",  "frames": list(range(15)), "fps": 40, "looping": False, "next": "idle"},
        "attack_melee": {"path": "sprites/character_with_shotgun/Attack2.png",  "frames": list(range(15)), "fps": 22, "looping": False, "next": "idle"},
        "hurt":   {"path": "sprites/character_with_shotgun/TakeDamage.png", "frames": list(range(15)), "fps": 18, "looping": False, "next": "idle"},
        "die":    {"path": "sprites/character_with_shotgun/Die.png",        "frames": list(range(15)), "fps": 8,  "looping": False},
    }
    post("/sprites/sheets", {
        "name": "player_shotgun",
        "path": "sprites/character_with_shotgun/Idle.png",
        "frame_width": 128, "frame_height": 128,
        "columns": 15, "rows": 8,
        "animations": shotgun_anims,
    })

    # 7c. Register zombie sprite sheets
    print("   Registering zombie sprite sheets...")

    def zombie_anims(folder, frame_w=128, frame_h=128):
        """Build animation dict for a zombie sprite folder."""
        base = f"sprites/all_zombies/{folder}"
        return {
            "idle":   {"path": f"{base}/Idle.png",       "frames": list(range(15)), "fps": 10, "looping": True},
            "run":    {"path": f"{base}/Run.png",        "frames": list(range(15)), "fps": 12, "looping": True},
            "walk":   {"path": f"{base}/Walk.png",       "frames": list(range(15)), "fps": 10, "looping": True},
            "attack": {"path": f"{base}/Attack1.png",    "frames": list(range(15)), "fps": 20, "looping": False, "next": "idle"},
            "hurt":   {"path": f"{base}/TakeDamage.png", "frames": list(range(15)), "fps": 18, "looping": False, "next": "idle"},
            "die":    {"path": f"{base}/Die.png",        "frames": list(range(15)), "fps": 8,  "looping": False},
        }

    post("/sprites/sheets", {
        "name": "zombie_normal",
        "path": "sprites/all_zombies/ZombieMale1/Idle.png",
        "frame_width": 128, "frame_height": 128,
        "columns": 15, "rows": 8,
        "animations": zombie_anims("ZombieMale1"),
    })
    post("/sprites/sheets", {
        "name": "zombie_runner",
        "path": "sprites/all_zombies/ZombieCop1/Idle.png",
        "frame_width": 128, "frame_height": 128,
        "columns": 15, "rows": 8,
        "animations": zombie_anims("ZombieCop1"),
    })
    post("/sprites/sheets", {
        "name": "zombie_tank",
        "path": "sprites/all_zombies/ZombieHulk1/Idle.png",
        "frame_width": 192, "frame_height": 192,
        "columns": 15, "rows": 8,
        "animations": zombie_anims("ZombieHulk1", 192, 192),
    })

    # 7d. Register decoration sprite sheets (single-frame, idle-only, recentered)
    print("   Registering decoration sprites...")
    decoration_sheets = [
        # Trees (256x512 → recentered + scaled to 128x256)
        ("tree_a1", f"{gen}/Tree_A1.png", 128, 256),
        ("tree_a2", f"{gen}/Tree_A2.png", 128, 256),
        ("tree_a3", f"{gen}/Tree_A3.png", 128, 256),
        ("tree_a4", f"{gen}/Tree_A4.png", 128, 256),
        # Cars (256x512 → recentered + scaled to 128x256)
        ("car_1", f"{gen}/Car1.png", 128, 256),
        ("car_2", f"{gen}/Car2.png", 128, 256),
        ("car_3", f"{gen}/Car3.png", 128, 256),
        # Flora (128x256 → recentered + scaled to 64x128)
        ("flora_a1", f"{gen}/Flora_A1.png", 64, 128),
        ("flora_a10", f"{gen}/Flora_A10.png", 64, 128),
        ("flora_a11", f"{gen}/Flora_A11.png", 64, 128),
        ("flora_a12", f"{gen}/Flora_A12.png", 64, 128),
        # Objects (128x256 → recentered + scaled to 64x128)
        ("object_1", f"{gen}/Object1.png", 64, 128),
        ("object_2", f"{gen}/Object2.png", 64, 128),
        ("object_3", f"{gen}/Object3.png", 64, 128),
        ("object_4", f"{gen}/Object4.png", 64, 128),
    ]
    for name, path, fw, fh in decoration_sheets:
        post("/sprites/sheets", {
            "name": name,
            "path": path,
            "frame_width": fw, "frame_height": fh,
            "columns": 1, "rows": 1,
            "animations": {"idle": {"frames": [0], "fps": 1, "looping": True}},
        })

    # 8. Spawn player
    print("8. Spawning player...")
    player_def = {
        "x": spawn_x,
        "y": spawn_y,
        "is_player": True,
        "tags": ["player"],
        "components": [
            {"type": "collider", "width": 36, "height": 44},
            {"type": "top_down_mover", "speed": 170},
            {"type": "health", "current": 10, "max": 10},
            {"type": "animation_controller", "graph": "player_bat",
             "auto_from_velocity": False, "facing_direction": 5},
            {"type": "hitbox", "width": 30, "height": 30, "offset_x": 0, "offset_y": 0,
             "active": False, "damage": 2, "damage_tag": "enemy"},
        ],
    }
    player_def["script"] = "player_combat"
    player = post("/entities", player_def)
    player_id = player["data"]["id"]
    print(f"   Player id={player_id}")

    # 9. Camera
    print("9. Setting up camera...")
    # Isometric world bounds — the diamond-shaped map extends in all directions
    corner_tl = iso_grid_to_world(0, 0)
    corner_tr = iso_grid_to_world(MAP_W - 1, 0)
    corner_bl = iso_grid_to_world(0, MAP_H - 1)
    corner_br = iso_grid_to_world(MAP_W - 1, MAP_H - 1)
    world_min_x = min(corner_tl[0], corner_bl[0])
    world_max_x = max(corner_tr[0], corner_br[0])
    world_min_y = min(corner_tl[1], corner_tr[1])
    world_max_y = max(corner_bl[1], corner_br[1])
    post("/camera/config", {
        "zoom": 2.0,
        "follow_speed": 5.0,
        "follow_target": player_id,
        "deadzone": [0, 0],
        "bounds": {
            "min_x": world_min_x,
            "min_y": world_min_y,
            "max_x": world_max_x,
            "max_y": world_max_y,
        },
    })

    # 10. Spawn ambient zombies
    if TILE_TEST_MODE:
        print("10. Skipping zombies (tile test mode)...")
        zombie_spawns = []
    else:
        print("10. Spawning ambient zombies...")
        zombie_spawns = find_zombie_spawns(tiles, MAP_W, MAP_H, [spawn_x, spawn_y], 25, rng, min_dist=600)
    zombie_count = 0
    for zx, zy in zombie_spawns:
        # Determine variant by proximity to zones
        variant = "normal"
        zone_name = "wilderness"
        for zone in ZONES:
            zcx, zcy = iso_grid_to_world(zone["cx"], zone["cy"])
            dist = math.sqrt((zx - zcx) ** 2 + (zy - zcy) ** 2)
            if dist < zone["radius"] * ISO_TILE_W / 4:
                zone_name = zone["name"]
                break

        if zone_name == "military":
            variant = rng.choice(["tank", "tank", "normal", "runner"])
        elif zone_name == "industrial":
            variant = rng.choice(["normal", "normal", "runner"])
        elif zone_name == "town":
            variant = rng.choice(["normal", "normal", "normal", "runner"])
        else:
            variant = "normal"

        hp = {"normal": 3, "runner": 1, "tank": 8}[variant]
        speed = {"normal": 90, "runner": 160, "tank": 55}[variant]
        coll_w = {"normal": 34, "runner": 28, "tank": 44}[variant]
        coll_h = {"normal": 42, "runner": 36, "tank": 52}[variant]
        dmg = {"normal": 1, "runner": 1, "tank": 2}[variant]
        tags = ["enemy", "zombie", f"zombie_{variant}"]

        post("/entities", {
            "x": zx, "y": zy,
            "is_player": False,
            "tags": tags,
            "script": "zombie_ai",
            "components": [
                {"type": "collider", "width": coll_w, "height": coll_h},
                {"type": "top_down_mover", "speed": speed},
                {"type": "health", "current": hp, "max": hp},
                {"type": "hitbox", "width": coll_w + 10, "height": coll_h + 10, "offset_x": 0, "offset_y": 0, "active": False, "damage": dmg, "damage_tag": "player"},
                {"type": "animation_controller", "graph": f"zombie_{variant}", "auto_from_velocity": True},
            ],
        })
        zombie_count += 1
    print(f"   Spawned {zombie_count} ambient zombies")

    # 11. Spawn loot
    if TILE_TEST_MODE:
        print("11. Skipping loot (tile test mode)...")
        loot_spots = []
    else:
        print("11. Placing loot...")
        loot_spots = find_loot_spawns(tiles, MAP_W, MAP_H, rng, count=70)
    loot_counts = {"health": 0, "ammo": 0, "weapon": 0}
    for i, (lx, ly) in enumerate(loot_spots):
        # Determine loot type based on zone proximity and random
        zone_name = "wilderness"
        for zone in ZONES:
            zcx, zcy = iso_grid_to_world(zone["cx"], zone["cy"])
            dist = math.sqrt((lx - zcx) ** 2 + (ly - zcy) ** 2)
            if dist < zone["radius"] * ISO_TILE_W / 4:
                zone_name = zone["name"]
                break

        roll = rng.random()
        if zone_name == "military":
            # Military: heavy on weapons and ammo
            if roll < 0.45:
                tags = ["pickup", "ammo_pickup"]
                loot_counts["ammo"] += 1
            elif roll < 0.8:
                tags = ["pickup", "weapon_pickup"]
                loot_counts["weapon"] += 1
            else:
                tags = ["pickup", "health_pickup"]
                loot_counts["health"] += 1
        elif zone_name == "industrial":
            # Industrial: ammo, some weapons
            if roll < 0.45:
                tags = ["pickup", "ammo_pickup"]
                loot_counts["ammo"] += 1
            elif roll < 0.6:
                tags = ["pickup", "weapon_pickup"]
                loot_counts["weapon"] += 1
            else:
                tags = ["pickup", "health_pickup"]
                loot_counts["health"] += 1
        elif zone_name == "farm":
            # Farm: mostly health, rare ammo
            if roll < 0.15:
                tags = ["pickup", "ammo_pickup"]
                loot_counts["ammo"] += 1
            else:
                tags = ["pickup", "health_pickup"]
                loot_counts["health"] += 1
        else:
            # Town/residential/wilderness: balanced
            if roll < 0.25:
                tags = ["pickup", "ammo_pickup"]
                loot_counts["ammo"] += 1
            elif roll < 0.4:
                tags = ["pickup", "weapon_pickup"]
                loot_counts["weapon"] += 1
            else:
                tags = ["pickup", "health_pickup"]
                loot_counts["health"] += 1

        post("/entities", {
            "x": lx, "y": ly,
            "is_player": False,
            "tags": tags,
            "components": [
                {"type": "collider", "width": 14, "height": 14},
            ],
        })
    print(f"   Placed {sum(loot_counts.values())} pickups: {loot_counts['health']} health, {loot_counts['ammo']} ammo, {loot_counts['weapon']} weapon")

    # 11b. Spawn decoration entities
    print("   Spawning decorations...")
    tree_variants = ["tree_a1", "tree_a2", "tree_a3", "tree_a4"]
    car_variants = ["car_1", "car_2", "car_3"]
    flora_variants = ["flora_a1", "flora_a10", "flora_a11", "flora_a12"]
    object_variants = ["object_1", "object_2", "object_3", "object_4"]
    deco_count = 0

    # Trees — large collidable decorations that block movement
    for wx, wy in decorations["trees"]:
        variant = rng.choice(tree_variants)
        post("/entities", {
            "x": wx, "y": wy,
            "is_player": False,
            "tags": ["decoration", "tree"],
            "components": [
                {"type": "collider", "width": 20, "height": 20},
                {"type": "animation_controller", "graph": variant, "auto_from_velocity": False},
            ],
        })
        deco_count += 1

    # Cars — large collidable decorations along roads
    for wx, wy in decorations["cars"]:
        variant = rng.choice(car_variants)
        post("/entities", {
            "x": wx, "y": wy,
            "is_player": False,
            "tags": ["decoration", "car"],
            "components": [
                {"type": "collider", "width": 40, "height": 24},
                {"type": "animation_controller", "graph": variant, "auto_from_velocity": False},
            ],
        })
        deco_count += 1

    # Flora — small non-collidable decorations on grass
    for wx, wy in decorations["flora"]:
        variant = rng.choice(flora_variants)
        post("/entities", {
            "x": wx, "y": wy,
            "is_player": False,
            "tags": ["decoration", "flora"],
            "components": [
                {"type": "animation_controller", "graph": variant, "auto_from_velocity": False},
            ],
        })
        deco_count += 1

    # Objects — small collidable decorations inside buildings
    for wx, wy in decorations["objects"]:
        variant = rng.choice(object_variants)
        post("/entities", {
            "x": wx, "y": wy,
            "is_player": False,
            "tags": ["decoration", "object"],
            "components": [
                {"type": "collider", "width": 14, "height": 14},
                {"type": "animation_controller", "graph": variant, "auto_from_velocity": False},
            ],
        })
        deco_count += 1

    print(f"   Spawned {deco_count} decoration entities")

    # 12. Set game variables
    print("12. Setting game variables...")
    # Zone info as flat variables for Lua (can't pass tables)
    zone_vars = {
        "score": 0,
        "zombies_killed": 0,
        "game_over": False,
        "player_x": spawn_x,
        "player_y": spawn_y,
        "spawn_x": spawn_x,
        "spawn_y": spawn_y,
        "death_timer": 0,
        "tile_size": TILE_SIZE,
        "iso_tile_w": ISO_TILE_W,
        "iso_tile_h": ISO_TILE_H,
        "map_width": MAP_W,
        "map_height": MAP_H,
        "survival_time": 0,
        "max_zombies": 30,
        "zombie_respawn_timer": 0,
        "alive_zombie_count": zombie_count,
        "stamina": 100,
        "sprinting": False,
        "weapon_level": 0,
        "ammo": 0,
        "attack_damage": 2,
        "attack_range": 30,
        "attack_speed": 20,
        "player_needs_reset": False,
    }
    # Zone data (flattened for Lua)
    for i, zone in enumerate(ZONES):
        zone_vars[f"zone_{i}_name"] = zone["name"]
        zcx, zcy = iso_grid_to_world(zone["cx"], zone["cy"])
        zone_vars[f"zone_{i}_cx"] = zcx
        zone_vars[f"zone_{i}_cy"] = zcy
        zone_vars[f"zone_{i}_radius"] = zone["radius"] * ISO_TILE_W / 4
        zone_vars[f"zone_{i}_density"] = zone["density"]
    zone_vars["zone_count"] = len(ZONES)

    post("/scripts/vars", zone_vars)

    # 13. Start game
    print("13. Starting game...")
    post("/game/state", {"state": "Playing"})

    # 14. Report
    entities = get("/entities").get("data") or []
    zombies = [e for e in entities if "zombie" in e.get("tags", [])]
    pickups = [e for e in entities if "pickup" in e.get("tags", [])]
    print(f"\n   Total entities: {len(entities)}")
    print(f"   Zombies: {len(zombies)}")
    print(f"   Pickups: {len(pickups)}")

    errors = get("/scripts/errors").get("data") or []
    if errors:
        print(f"\n   Script errors ({len(errors)}):")
        for err in errors[:5]:
            print(f"     - {err}")

    # 15. Capture screenshots for visual verification
    print("\n--- Step 15: Capturing screenshots ---")
    time.sleep(3)  # Wait for game to render
    for i in range(3):
        try:
            resp = get("/screenshot")
            data = resp.get("data", "")
            path = data.get("path", data) if isinstance(data, dict) else data
            print(f"  Screenshot {i+1}: {path}")
        except Exception as e:
            print(f"  Screenshot {i+1}: failed ({e})")
        time.sleep(2)

    print("\n=== Zombie Survival built successfully ===")
    print(f"World: {MAP_W}x{MAP_H} grid (isometric {ISO_TILE_W}x{ISO_TILE_H})")
    print("Open windowed: cd axiom && cargo run")
    print("\nControls: Arrow keys = move, Shift = sprint, Z/X/Enter or Left Click = attack")
    return 0


def main():
    client = AxiomClient(timeout=60.0)
    print("Connecting to Axiom engine...")
    if not client.wait_for_server(timeout=5.0):
        print("ERROR: Engine not running. Start it first:")
        print("  cd C:\\Users\\cobra\\axiom && cargo run")
        return 1
    print("Connected!\n")
    return build_game(client)


if __name__ == "__main__":
    sys.exit(main())
