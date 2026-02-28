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
MAP_W, MAP_H = (60, 60) if TILE_TEST_MODE else (600, 420)

# ── Isometric tile dimensions ────────────────────────────────────────
ISO_TILE_W = 128   # Diamond base width (pixels)
ISO_TILE_H = 64    # Diamond base height (pixels)

# ── Material tile IDs ──────────────────────────────────────────────
# The engine handles autotiling via /terrain/materials.
MATERIAL_LABELS = {
    "A": "water", "B": "brown_earth", "C": "stone", "D": "dirt",
    "E": "gravel", "F": "dark_earth", "G": "grass",
}

# Dirt (tile_id=8) is the base material — no autotiling, just a plain fill tile.
T_DIRT = 8
DIRT_FILL_TILE = "sprites/tilesets/rural_tileset/Isometric Tiles/Ground A1_N.png"

# Road center line overlay tile IDs — registered as non-autotile single-frame materials.
# Placed on an extra_layer on top of regular road (B) to show B14 center lines
# only along the middle of straight corridors.
T_ROAD_CENTER_H = 200  # Horizontal corridor center → B14_E
T_ROAD_CENTER_V = 201  # Vertical corridor center → B14_N
ROAD_CENTER_H_TILE = "sprites/tilesets/rural_tileset/Isometric Tiles/Ground B14_E.png"
ROAD_CENTER_V_TILE = "sprites/tilesets/rural_tileset/Isometric Tiles/Ground B14_N.png"

# Flora overlay tile IDs — placed as extra_layer on top of ground tiles.
# Flora B (green, under A trees) and Flora E (brown, under B/C trees).
# Variants: 1/4 = full tiles, 2 = edge, 5 = outside corner, 6 = inside corner.
# Directions: N/E/S/W control rotation.
FLORA_TILE_IDS = {}
_next_flora_id = 210
for _series in ["B", "E"]:
    for _var in [1, 2, 4, 5, 6]:
        for _d in ["N", "E", "S", "W"]:
            key = f"flora_{_series.lower()}{_var}_{_d.lower()}"
            FLORA_TILE_IDS[key] = _next_flora_id
            _next_flora_id += 1

FLORA_GEN_DIR = "sprites/tilesets/generated"

# Autotile atlas directory (produced by autotile_slot_tool.py Export button).
AUTOTILE_ATLAS_DIR = Path(
    os.environ.get(
        "ZOMBIE_AUTOTILE_ATLAS_DIR",
        r"C:\Users\cobra\zombie-game\assets\generated\autotile_profiles",
    )
)
GAME_ASSETS_DIR = Path(r"C:\Users\cobra\zombie-game\assets")


def discover_autotile_materials() -> tuple[dict, dict]:
    """Auto-discover all atlases in AUTOTILE_ATLAS_DIR.

    Returns:
        material_to_tile_id: {"G": 9, "A": 10, "B": 11, ...}
        materials: {series_letter: {label, series, atlas_rel, tile_id}, ...}
    """
    materials = {}
    next_tile_id = T_DIRT + 1  # 9, 10, 11, ...
    for meta_path in sorted(AUTOTILE_ATLAS_DIR.glob("*/*_autotile_atlas.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("format") != "axiom_autotile_atlas":
                continue
            mat = meta.get("material", {})
            series = mat.get("series", "")
            label = mat.get("label", "")
            if not series:
                continue
            atlas_png = meta_path.with_suffix(".png")
            if not atlas_png.exists():
                continue
            atlas_rel = str(atlas_png.relative_to(GAME_ASSETS_DIR)).replace("\\", "/")
            tile_id = next_tile_id
            next_tile_id += 1
            materials[series] = {
                "label": label, "series": series,
                "atlas_rel": atlas_rel, "tile_id": tile_id,
                "frame_width": meta.get("frame_width", 128),
                "frame_height": meta.get("frame_height", 256),
                "columns": meta.get("columns", 13),
            }
        except Exception as e:
            print(f"   WARNING: skipping {meta_path}: {e}")

    material_to_tile_id = {"D": T_DIRT}
    for series, info in materials.items():
        material_to_tile_id[series] = info["tile_id"]
    return material_to_tile_id, materials


MATERIAL_TO_TILE_ID, DISCOVERED_MATERIALS = discover_autotile_materials()

RECTANGLE_EDGE_DEBUG_TEST = os.environ.get("ZOMBIE_RECTANGLE_EDGE_DEBUG_TEST", "").strip() == "1"


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

def place_road_corridors(material_map, width, height, rng):
    """Paint 3-wide road corridors with organic variation.

    Core structure: one horizontal + one vertical 3-wide corridor crossing near center.
    Variation: scatter small blobs along the corridors to create wider sections,
    plazas near the intersection, and organic edges that break up the straight lines.

    Returns (h_row, v_col, center_line_overlay) where center_line_overlay is a flat
    array of tile IDs (0 = no overlay, T_ROAD_CENTER_H/V = center line).
    """
    def idx(tx, ty):
        return ty * width + tx

    # Horizontal road: 3 rows at ~40% height, at least 2 cells from edge
    h_row = max(4, int(height * 0.4))
    h_row = min(h_row, height - 5)

    # Vertical road: 3 cols at ~60% width, at least 2 cells from edge
    v_col = max(4, int(width * 0.6))
    v_col = min(v_col, width - 5)

    # ── Step 1: Paint the core 3-wide corridors ──────────────────────
    # Center line overlay: B14 only on the center row of these two main roads.
    overlay = [0] * (width * height)

    for dy in range(-1, 2):
        row = h_row + dy
        if 0 <= row < height:
            for x in range(2, width - 2):
                material_map[idx(x, row)] = "B"
                if dy == 0:
                    overlay[idx(x, row)] = T_ROAD_CENTER_H

    for dx in range(-1, 2):
        col = v_col + dx
        if 0 <= col < width:
            for y in range(2, height - 2):
                material_map[idx(col, y)] = "B"
                if dx == 0 and y != h_row:
                    overlay[idx(col, y)] = T_ROAD_CENTER_V

    # Clear the intersection center tile (where both roads cross)
    overlay[idx(v_col, h_row)] = 0

    # ── Step 2: Plaza at intersection (wider area where roads cross) ─
    plaza_rx = rng.randint(3, 5)
    plaza_ry = rng.randint(3, 5)
    for dy in range(-plaza_ry, plaza_ry + 1):
        for dx in range(-plaza_rx, plaza_rx + 1):
            x, y = v_col + dx, h_row + dy
            if 2 <= x < width - 2 and 2 <= y < height - 2:
                dist = (dx / plaza_rx) ** 2 + (dy / plaza_ry) ** 2
                if dist < 1.0 + rng.random() * 0.2:
                    material_map[idx(x, y)] = "B"

    # ── Step 3: Widenings along corridors ────────────────────────────
    n_bulges = rng.randint(4, 8)
    for _ in range(n_bulges):
        if rng.random() < 0.5:
            bx = rng.randint(4, width - 5)
            by = h_row + rng.choice([-2, -1, 1, 2])
        else:
            bx = v_col + rng.choice([-2, -1, 1, 2])
            by = rng.randint(4, height - 5)
        brx = rng.randint(1, 3)
        bry = rng.randint(1, 3)
        for dy in range(-bry, bry + 1):
            for dx in range(-brx, brx + 1):
                x, y = bx + dx, by + dy
                if 2 <= x < width - 2 and 2 <= y < height - 2:
                    dist = (dx / max(brx, 0.5)) ** 2 + (dy / max(bry, 0.5)) ** 2
                    if dist < 1.0 + rng.random() * 0.3:
                        material_map[idx(x, y)] = "B"

    # ── Step 4: Random road stubs branching off corridors ────────────
    n_stubs = rng.randint(2, 5)
    for _ in range(n_stubs):
        if rng.random() < 0.5:
            sx = rng.randint(6, width - 7)
            direction = rng.choice([-1, 1])
            stub_len = rng.randint(3, 7)
            for step in range(stub_len):
                sy = h_row + direction * (2 + step)
                if 2 <= sy < height - 2:
                    for dx in range(-1, 2):
                        x = sx + dx
                        if 2 <= x < width - 2:
                            material_map[idx(x, sy)] = "B"
        else:
            sy = rng.randint(6, height - 7)
            direction = rng.choice([-1, 1])
            stub_len = rng.randint(3, 7)
            for step in range(stub_len):
                sx = v_col + direction * (2 + step)
                if 2 <= sx < width - 2:
                    for dy in range(-1, 2):
                        y = sy + dy
                        if 2 <= y < height - 2:
                            material_map[idx(sx, y)] = "B"

    road_cells = sum(1 for m in material_map if m == "B")
    center_cells = sum(1 for t in overlay if t != 0)
    print(f"   Road corridors: h_row={h_row}, v_col={v_col}, road={road_cells}, center_lines={center_cells}")
    return h_row, v_col, overlay


def generate_world(width, height, seed=42):
    """Generate terrain: roads first as 3-wide corridors, then grass, water, gravel.

    Returns a flat tile array with material tile IDs.
    Generation order:
      1. Roads (B): 3-wide cross corridors (town crossroads)
      2. Grass (G): large blobs, avoiding road + 2-cell buffer
      3. Water (A): medium blobs, avoiding grass + road + 2-cell buffer
      4. Gravel (E): small blobs, avoiding all above + 2-cell buffer
    The engine handles border autotiling via /terrain/materials.
    """
    rng = random.Random(seed)

    def idx(tx, ty):
        return ty * width + tx

    material_map = ["D"] * (width * height)

    # ── Helper: material lookup with bounds ──────────────────────────
    def mat_at(tx, ty):
        if tx < 0 or ty < 0 or tx >= width or ty >= height:
            return "D"
        return material_map[idx(tx, ty)]

    # ── Helper: compute buffer mask around a material ────────────────
    def compute_buffer(mat_letter, radius=2):
        buf = [False] * (width * height)
        for y in range(height):
            for x in range(width):
                if material_map[idx(x, y)] == mat_letter:
                    for dy2 in range(-radius, radius + 1):
                        for dx2 in range(-radius, radius + 1):
                            nx, ny = x + dx2, y + dy2
                            if 0 <= nx < width and 0 <= ny < height:
                                buf[idx(nx, ny)] = True
        return buf

    # ── Helper: combine multiple buffers ─────────────────────────────
    def combine_buffers(*bufs):
        combined = [False] * (width * height)
        for b in bufs:
            for i in range(width * height):
                combined[i] = combined[i] or b[i]
        return combined

    # ── Helper: place blobs of a material on dirt-only cells ─────────
    def place_blobs(mat_letter, exclude_buf, n_blobs, r_range, min_sep=6):
        valid = []
        for y in range(4, height - 4):
            for x in range(4, width - 4):
                if not exclude_buf[idx(x, y)] and material_map[idx(x, y)] == "D":
                    valid.append((x, y))
        rng.shuffle(valid)
        blobs = []
        chosen_centers = []
        for cx, cy in valid:
            if any(abs(cx - ox) + abs(cy - oy) < min_sep for ox, oy in chosen_centers):
                continue
            chosen_centers.append((cx, cy))
            rx = rng.randint(r_range[0], r_range[1])
            ry = rng.randint(r_range[0], r_range[1])
            blobs.append((cx, cy, rx, ry))
            if len(blobs) >= n_blobs:
                break
        for y in range(height):
            for x in range(width):
                i = idx(x, y)
                if material_map[i] != "D" or exclude_buf[i]:
                    continue
                for cx, cy, rx, ry in blobs:
                    ddx = (x - cx) / rx
                    ddy = (y - cy) / ry
                    if ddx * ddx + ddy * ddy < 1.0 + rng.random() * 0.3:
                        material_map[i] = mat_letter
                        break

    # ── Helper: remove thin strips of a material ─────────────────────
    def remove_thin_strips(mat_letter):
        changed = True
        while changed:
            changed = False
            for y in range(height):
                for x in range(width):
                    if material_map[idx(x, y)] != mat_letter:
                        continue
                    n = mat_at(x, y - 1) == mat_letter
                    s = mat_at(x, y + 1) == mat_letter
                    e = mat_at(x + 1, y) == mat_letter
                    w2 = mat_at(x - 1, y) == mat_letter
                    if not (n or s) or not (e or w2):
                        material_map[idx(x, y)] = "D"
                        changed = True

    road_overlay = None

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
        # ── Step 1: Roads first (3-wide corridors) ───────────────────
        if "B" in MATERIAL_TO_TILE_ID:
            _, _, road_overlay = place_road_corridors(material_map, width, height, rng)

        # ── Step 2: Grass blobs (buffered away from roads) ───────────
        road_buf = compute_buffer("B")
        min_dim = min(width, height)
        n_grass = max(20, width * height // 600)
        grass_r_lo = max(5, min_dim // 20)
        grass_r_hi = max(grass_r_lo + 3, min_dim // 8)
        grass_blobs = []
        for _ in range(n_grass):
            cx = rng.randint(3, width - 4)
            cy = rng.randint(3, height - 4)
            rx = rng.randint(grass_r_lo, grass_r_hi)
            ry = rng.randint(grass_r_lo, grass_r_hi)
            grass_blobs.append((cx, cy, rx, ry))

        for y in range(height):
            for x in range(width):
                i = idx(x, y)
                if material_map[i] != "D" or road_buf[i]:
                    continue
                for cx, cy, rx, ry in grass_blobs:
                    dx = (x - cx) / rx
                    dy = (y - cy) / ry
                    dist = dx * dx + dy * dy
                    jitter = rng.random() * 0.3
                    if dist < 1.0 + jitter:
                        material_map[i] = "G"
                        break

        # Remove thin grass strips
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

        # ── Step 3: Water blobs (buffered away from grass + roads) ───
        grass_buf = compute_buffer("G")
        water_exclude = combine_buffers(grass_buf, road_buf)
        n_water = max(4, width * height // 4000)
        water_r_lo = max(3, min_dim // 40)
        water_r_hi = max(water_r_lo + 2, min_dim // 15)
        place_blobs("A", water_exclude, n_blobs=n_water, r_range=(water_r_lo, water_r_hi))
        remove_thin_strips("A")

        # ── Step 4: Gravel blobs (buffered away from all above) ──────
        if "E" in MATERIAL_TO_TILE_ID:
            water_buf = compute_buffer("A")
            gravel_exclude = combine_buffers(grass_buf, water_buf, road_buf)
            n_gravel = max(6, width * height // 3000)
            gravel_r_lo = max(2, min_dim // 50)
            gravel_r_hi = max(gravel_r_lo + 2, min_dim // 20)
            place_blobs("E", gravel_exclude, n_blobs=n_gravel, r_range=(gravel_r_lo, gravel_r_hi), min_sep=5)
            remove_thin_strips("E")

    # ── Convert material map to tile IDs ─────────────────────────────
    counts = {}
    for m in material_map:
        counts[m] = counts.get(m, 0) + 1
    parts = ", ".join(f"{MATERIAL_LABELS.get(k, k)}={v}" for k, v in sorted(counts.items()))
    print(f"   Material cells: {parts}")

    tiles = [MATERIAL_TO_TILE_ID[m] for m in material_map]
    return tiles, road_overlay, material_map


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

def find_decoration_positions(tiles, material_map, width, height, rng):
    """Biome-aware clustered decoration placement.

    Trees form forest clumps on grass. Flora A placed as understory near trees
    and scattered on grass. Flora B placed along road edges.
    Returns dict of category -> list of (world_x, world_y, variant_name).
    """
    tree_positions = []
    flora_positions = []
    object_positions = []

    def idx(tx, ty):
        return ty * width + tx

    def mat_at(tx, ty):
        if 0 <= tx < width and 0 <= ty < height:
            return material_map[idx(tx, ty)]
        return "D"

    # ── Variant pools ──
    TREES_LUSH = [f"tree_a{i}" for i in range(5, 11)]       # 6 variants (A5-A10)
    TREES_AUTUMN = [f"tree_b{i}" for i in range(1, 3)]      # 2 variants (B1-B2)
    TREES_EVERGREEN = [f"tree_c{i}" for i in range(1, 3)]   # 2 variants (C1-C2)
    FLORA_A = [f"flora_a{i}" for i in [1,2,3,4,5,6,7,8,9,10,11,12,15,16,17,18,19,20]]
    FLORA_B = [f"flora_b{i}" for i in range(1, 20)]

    # ── Collect candidate tiles by material ──
    grass_tiles = []
    grass_set = set()
    dirt_set = set()
    road_tiles = []
    road_edge_tiles = []
    dirt_gravel_tiles = []
    for ty in range(3, height - 3):
        for tx in range(3, width - 3):
            m = material_map[idx(tx, ty)]
            if m == "G":
                grass_tiles.append((tx, ty))
                grass_set.add((tx, ty))
            elif m == "B":
                road_tiles.append((tx, ty))
                # Check if this road tile is at an edge (adjacent to non-road)
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    nbr = mat_at(tx + dx, ty + dy)
                    if nbr != "B":
                        road_edge_tiles.append((tx, ty))
                        break
            elif m in ("D", "E"):
                dirt_gravel_tiles.append((tx, ty))
                dirt_set.add((tx, ty))  # Flora E goes on both dirt and gravel

    # Global occupied set for spacing checks
    tree_occupied = set()
    flora_occupied = set()

    def is_clear_of(tx, ty, occupied_set, min_dist):
        """Check if (tx,ty) is at least min_dist from any tile in occupied_set."""
        for dx in range(-min_dist, min_dist + 1):
            for dy in range(-min_dist, min_dist + 1):
                if (tx + dx, ty + dy) in occupied_set:
                    return False
        return True

    # ── Flora patches: terrain-based, partial coverage ──
    # Flora B on grass, Flora E on dirt. Generated BEFORE trees so trees
    # can be constrained to only spawn on flora patch cells.

    def make_flora_blobs(candidate_set, coverage=0.65):
        """Create organic flora blobs covering ~coverage fraction of candidates."""
        candidates = list(candidate_set)
        rng.shuffle(candidates)
        target = int(len(candidates) * coverage)
        blob_cells = set()
        n_seeds = max(3, len(candidates) // 40)
        seed_pts = candidates[:n_seeds]
        blob_radius = max(3, min(width, height) // 12)
        for sx, sy in seed_pts:
            r = rng.randint(max(2, blob_radius - 2), blob_radius + 2)
            for ddx in range(-r, r + 1):
                for ddy in range(-r, r + 1):
                    nx, ny = sx + ddx, sy + ddy
                    if (nx, ny) not in candidate_set:
                        continue
                    dist = (ddx * ddx + ddy * ddy) ** 0.5
                    if dist < r + rng.random() * 2 - 1:
                        blob_cells.add((nx, ny))
            if len(blob_cells) >= target:
                break
        return blob_cells

    def remove_singletons(cells):
        """Remove cells with no cardinal neighbors in the set."""
        return {(tx, ty) for tx, ty in cells
                if any((tx + dx, ty + dy) in cells
                       for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)])}

    # Build buffer zones: exclude cells within 3 tiles of incompatible terrain
    FLORA_BUFFER = 3
    incompatible_for_grass = set()  # grass flora shouldn't be near water, roads
    incompatible_for_dirt = set()   # dirt flora shouldn't be near water, roads
    for ty in range(height):
        for tx in range(width):
            m = material_map[idx(tx, ty)]
            if m in ("A", "B", "C"):  # water, road, stone
                for ddx in range(-FLORA_BUFFER, FLORA_BUFFER + 1):
                    for ddy in range(-FLORA_BUFFER, FLORA_BUFFER + 1):
                        incompatible_for_grass.add((tx + ddx, ty + ddy))
                        incompatible_for_dirt.add((tx + ddx, ty + ddy))
            elif m == "D":  # dirt is incompatible neighbor for grass flora
                for ddx in range(-FLORA_BUFFER, FLORA_BUFFER + 1):
                    for ddy in range(-FLORA_BUFFER, FLORA_BUFFER + 1):
                        incompatible_for_grass.add((tx + ddx, ty + ddy))
            elif m == "G":  # grass is incompatible neighbor for dirt flora
                for ddx in range(-FLORA_BUFFER, FLORA_BUFFER + 1):
                    for ddy in range(-FLORA_BUFFER, FLORA_BUFFER + 1):
                        incompatible_for_dirt.add((tx + ddx, ty + ddy))

    buffered_grass = grass_set - incompatible_for_grass
    buffered_dirt = dirt_set - incompatible_for_dirt

    flora_b_patch = remove_singletons(make_flora_blobs(buffered_grass, coverage=0.3))
    flora_e_patch = remove_singletons(make_flora_blobs(buffered_dirt, coverage=0.4))
    flora_patch_all = flora_b_patch | flora_e_patch

    # ── TREES: Dense forest clumps (only on flora patches) ──
    flora_patch_list = list(flora_patch_all)
    rng.shuffle(flora_patch_list)
    n_patch = len(flora_patch_all)
    # Very dense: ~1 tree per 3 flora patch tiles, tightly packed clusters
    max_trees = min(8000, max(n_patch // 3, 50))
    min_dim = min(width, height)
    seed_spacing = max(2, min_dim // 15)  # very tight seeds
    n_seeds = max(8, n_patch // 10)

    # Pick seed positions (only from dense interior of flora patches)
    seed_positions = []
    seed_set = set()
    for tx, ty in flora_patch_list:
        if len(seed_positions) >= n_seeds:
            break
        # Seed must be deep inside a flora patch
        nearby = sum(1 for ddx in range(-2, 3) for ddy in range(-2, 3)
                     if (tx + ddx, ty + ddy) in flora_patch_all)
        if nearby < 15:
            continue
        if is_clear_of(tx, ty, seed_set, seed_spacing):
            seed_positions.append((tx, ty))
            seed_set.add((tx, ty))

    # Assign cluster type based on which flora patch the seed is in:
    # Flora B patch (grass) → lush trees (A), Flora E patch (dirt) → autumn/evergreen (B/C)
    cluster_types = []
    for sx, sy in seed_positions:
        if (sx, sy) in flora_b_patch:
            cluster_types.append(("lush", TREES_LUSH))
        else:
            # On dirt (flora E patch) — 50/50 autumn vs evergreen
            if rng.random() < 0.5:
                cluster_types.append(("autumn", TREES_AUTUMN))
            else:
                cluster_types.append(("evergreen", TREES_EVERGREEN))

    # Scatter trees densely around each seed
    for (sx, sy), (ctype, variants) in zip(seed_positions, cluster_types):
        if len(tree_positions) >= max_trees:
            break
        cluster_size = rng.randint(10, 25)
        cluster_radius = rng.randint(2, 4)
        placed = 0
        attempts = 0
        while placed < cluster_size and attempts < cluster_size * 6:
            attempts += 1
            dx = rng.randint(-cluster_radius, cluster_radius)
            dy = rng.randint(-cluster_radius, cluster_radius)
            tx, ty = sx + dx, sy + dy
            if not (3 <= tx < width - 3 and 3 <= ty < height - 3):
                continue
            if (tx, ty) not in flora_patch_all:
                continue
            # Only place tree if well inside a flora patch (not on thin edges)
            nearby_flora = sum(1 for ddx in range(-1, 2) for ddy in range(-1, 2)
                               if (tx + ddx, ty + ddy) in flora_patch_all)
            if nearby_flora < 7:
                continue
            if (tx, ty) in tree_occupied:
                continue
            tree_occupied.add((tx, ty))
            variant = rng.choice(variants)
            wx, wy = iso_grid_to_world(tx, ty)
            tree_positions.append((wx, wy, variant))
            placed += 1
            if len(tree_positions) >= max_trees:
                break

    # ── FLORA A: Understory near trees + scattered on grass ──
    max_flora_a = min(3000, len(grass_tiles) // 20)
    flora_a_count = 0

    # 60% near trees (understory)
    understory_target = int(max_flora_a * 0.6)
    tree_tile_positions = [(tx, ty) for tx, ty in tree_occupied]
    rng.shuffle(tree_tile_positions)
    for tree_tx, tree_ty in tree_tile_positions:
        if flora_a_count >= understory_target:
            break
        # Try to place 1-3 flora near each tree
        for _ in range(rng.randint(1, 3)):
            if flora_a_count >= understory_target:
                break
            dx = rng.randint(-3, 3)
            dy = rng.randint(-3, 3)
            fx, fy = tree_tx + dx, tree_ty + dy
            if not (3 <= fx < width - 3 and 3 <= fy < height - 3):
                continue
            if (fx, fy) not in grass_set:
                continue
            if (fx, fy) in tree_occupied:
                continue
            if not is_clear_of(fx, fy, flora_occupied, 1):
                continue
            # Skip if adjacent to water
            near_water = any(mat_at(fx + ddx, fy + ddy) == "A"
                             for ddx in range(-1, 2) for ddy in range(-1, 2))
            if near_water:
                continue
            flora_occupied.add((fx, fy))
            variant = rng.choice(FLORA_A)
            wx, wy = iso_grid_to_world(fx, fy)
            flora_positions.append((wx, wy, variant))
            flora_a_count += 1

    # 40% scattered on open grass
    scatter_target = max_flora_a - flora_a_count
    rng.shuffle(grass_tiles)
    for tx, ty in grass_tiles:
        if flora_a_count >= max_flora_a:
            break
        if (tx, ty) in tree_occupied:
            continue
        if not is_clear_of(tx, ty, flora_occupied, 2):
            continue
        near_water = any(mat_at(tx + ddx, ty + ddy) == "A"
                         for ddx in range(-1, 2) for ddy in range(-1, 2))
        if near_water:
            continue
        flora_occupied.add((tx, ty))
        variant = rng.choice(FLORA_A)
        wx, wy = iso_grid_to_world(tx, ty)
        flora_positions.append((wx, wy, variant))
        flora_a_count += 1

    # ── FLORA B: Bushes along road edges ──
    rng.shuffle(road_edge_tiles)
    max_flora_b = min(1500, len(road_edge_tiles) // 3)
    flora_b_count = 0
    for tx, ty in road_edge_tiles:
        if flora_b_count >= max_flora_b:
            break
        # Place on adjacent non-road tile (grass or dirt side of the road edge)
        candidates = []
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nx, ny = tx + dx, ty + dy
            if 3 <= nx < width - 3 and 3 <= ny < height - 3:
                nbr = mat_at(nx, ny)
                if nbr in ("G", "D", "E"):
                    candidates.append((nx, ny))
        if not candidates:
            continue
        fx, fy = rng.choice(candidates)
        if (fx, fy) in tree_occupied or (fx, fy) in flora_occupied:
            continue
        if not is_clear_of(fx, fy, flora_occupied, 2):
            continue
        flora_occupied.add((fx, fy))
        variant = rng.choice(FLORA_B)
        wx, wy = iso_grid_to_world(fx, fy)
        flora_positions.append((wx, wy, variant))
        flora_b_count += 1

    # ── Objects: on dirt/gravel (not roads), min 4-tile spacing ──
    OBJECT_VARIANTS = [f"object_{i}" for i in list(range(1, 5)) + list(range(16, 19)) + list(range(21, 24)) + [33] + list(range(39, 45))]
    rng.shuffle(dirt_gravel_tiles)
    max_objects = min(500, len(dirt_gravel_tiles) // 80)
    obj_occupied = set()
    for tx, ty in dirt_gravel_tiles:
        if len(object_positions) >= max_objects:
            break
        if not is_clear_of(tx, ty, obj_occupied, 4):
            continue
        if (tx, ty) in tree_occupied or (tx, ty) in flora_occupied:
            continue
        # Keep objects away from water
        near_water = any(mat_at(tx + dx, ty + dy) == "A"
                         for dx in range(-2, 3) for dy in range(-2, 3))
        if near_water:
            continue
        obj_occupied.add((tx, ty))
        variant = rng.choice(OBJECT_VARIANTS)
        wx, wy = iso_grid_to_world(tx, ty)
        object_positions.append((wx, wy, variant))

    # ── Cars: on or very near roads, min 4-tile spacing ──
    CAR_VARIANTS = [f"car_{i}" for i in range(1, 11)]
    car_positions = []
    car_occupied = set()
    rng.shuffle(road_tiles)
    max_cars = min(100, max(len(road_tiles) // 30, 3))
    for tx, ty in road_tiles:
        if len(car_positions) >= max_cars:
            break
        if not is_clear_of(tx, ty, car_occupied, 4):
            continue
        if (tx, ty) in tree_occupied or (tx, ty) in obj_occupied:
            continue
        car_occupied.add((tx, ty))
        variant = rng.choice(CAR_VARIANTS)
        wx, wy = iso_grid_to_world(tx, ty)
        car_positions.append((wx, wy, variant))

    return {
        "trees": tree_positions,
        "cars": car_positions,
        "flora": flora_positions,
        "objects": object_positions,
        "flora_b_patch": flora_b_patch,
        "flora_e_patch": flora_e_patch,
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

    # 1a. Background color — dark to match fog vignette edges (seamless darkness beyond sprite)
    post("/window", {"background": [0.02, 0.012, 0.008]})

    # 1b. Fog of war — vignette sprite overlay (engine lighting disabled)
    post("/lighting/config", {"enabled": False})

    # 1c. Register terrain materials (auto-discovered from atlas directory)
    print("   Registering terrain materials...")
    gen = "sprites/tilesets/generated"

    # Dirt: base material, no autotiling.
    post("/terrain/materials", {
        "name": "dirt",
        "tile_id": T_DIRT,
        "atlas": DIRT_FILL_TILE,
        "frame_width": 128,
        "frame_height": 256,
        "autotile": False,
    })
    print(f"   Registered dirt (tile_id={T_DIRT}) as plain fill tile")

    # Road center line tiles — overlaid on top of regular road (B) autotile.
    # Horizontal center uses B14_E, vertical center uses B14_N.
    post("/terrain/materials", {
        "name": "road_center_h",
        "tile_id": T_ROAD_CENTER_H,
        "atlas": ROAD_CENTER_H_TILE,
        "frame_width": 128,
        "frame_height": 256,
        "autotile": False,
    })
    post("/terrain/materials", {
        "name": "road_center_v",
        "tile_id": T_ROAD_CENTER_V,
        "atlas": ROAD_CENTER_V_TILE,
        "frame_width": 128,
        "frame_height": 256,
        "autotile": False,
    })
    print(f"   Registered road center line tiles (h={T_ROAD_CENTER_H}, v={T_ROAD_CENTER_V})")

    # Flora overlay tiles — registered as non-autotile single-frame materials.
    print("   Registering flora overlay tiles...")
    for key, tid in FLORA_TILE_IDS.items():
        # key like "flora_b1_n" → file Flora_B1_N.png
        parts = key.split("_")  # ["flora", "b1", "n"]
        series_var = parts[1].upper()  # "B1"
        direction = parts[2].upper()   # "N"
        png_name = f"Flora_{series_var}_{direction}.png"
        post("/terrain/materials", {
            "name": key,
            "tile_id": tid,
            "atlas": f"{FLORA_GEN_DIR}/{png_name}",
            "frame_width": 128,
            "frame_height": 256,
            "autotile": False,
        })
    print(f"   Registered {len(FLORA_TILE_IDS)} flora overlay tiles (IDs {min(FLORA_TILE_IDS.values())}-{max(FLORA_TILE_IDS.values())})")

    # All other materials: auto-discovered from AUTOTILE_ATLAS_DIR.
    for series, info in DISCOVERED_MATERIALS.items():
        mat_req = {
            "name": info["label"],
            "tile_id": info["tile_id"],
            "atlas": info["atlas_rel"],
            "frame_width": info["frame_width"],
            "frame_height": info["frame_height"],
        }
        if info.get("columns", 13) != 13:
            mat_req["columns"] = info["columns"]
        # Water (series "A") should be unwalkable
        if series == "A":
            mat_req["solid"] = True
        post("/terrain/materials", mat_req)
        cols = info.get("columns", 13)
        print(f"   Registered {info['label']} (tile_id={info['tile_id']}, columns={cols}) with atlas: {info['atlas_rel']}")

    # 2. Generate world
    print(f"2. Generating {MAP_W}x{MAP_H} open world (isometric)...")
    tiles, road_overlay, material_map = generate_world(MAP_W, MAP_H, seed)
    spawn_x, spawn_y = iso_grid_to_world(MAP_W // 2, MAP_H // 2)
    print(f"   Player spawn: ({spawn_x:.0f}, {spawn_y:.0f})")

    # 2b. Place decorations on terrain
    print("   Placing decorations...")
    decorations = find_decoration_positions(tiles, material_map, MAP_W, MAP_H, rng)
    print(f"   Trees: {len(decorations['trees'])}, Cars: {len(decorations['cars'])}, Flora: {len(decorations['flora'])}, Objects: {len(decorations['objects'])}")

    # 3. Load level
    print("3. Loading level...")
    level_data = {
        "width": MAP_W,
        "height": MAP_H,
        "tiles": tiles,
        "player_spawn": [spawn_x, spawn_y],
        "goal": [int(v) for v in iso_grid_to_world(1, 1)],
    }
    # Add extra layers
    extra_layers = []

    # Road center line overlay
    if road_overlay and any(t != 0 for t in road_overlay):
        extra_layers.append({
            "name": "road_center_lines",
            "tiles": road_overlay,
            "z_offset": 0.01,
        })
        print(f"   Road center line overlay: {sum(1 for t in road_overlay if t != 0)} tiles")

    # Flora overlay patches (under trees)
    flora_b_patch = decorations["flora_b_patch"]
    flora_e_patch = decorations["flora_e_patch"]
    if flora_b_patch or flora_e_patch:
        flora_overlay = [0] * (MAP_W * MAP_H)

        def pick_flora_tile(tx, ty, patch_set, series):
            """Pick flora tile using same direction mapping as ground autotiles.
            B5/E5 = outside corners, B6/E6 = inside corners, B2/E2 = alt corner.
            B1/B4 (E1/E4) = full tiles for everything else."""
            has_n = (tx, ty - 1) in patch_set
            has_s = (tx, ty + 1) in patch_set
            has_w = (tx - 1, ty) in patch_set
            has_e = (tx + 1, ty) in patch_set

            p = series.lower()  # "b" or "e"

            # Outside corner: exactly 2 adjacent cardinals present
            # Direction mapping matches ground autotile (from gravel E3 example):
            #   N+E present (SW exposed) → _e
            #   E+S present (NW exposed) → _n
            #   S+W present (NE exposed) → _w
            #   W+N present (SE exposed) → _s
            count = sum([has_n, has_s, has_w, has_e])
            if count == 2 and not (has_n and has_s) and not (has_e and has_w):
                v = rng.choice([2, 5, 6])  # outside corner variants
                if has_n and has_e:
                    return FLORA_TILE_IDS[f"flora_{p}{v}_e"]
                elif has_e and has_s:
                    return FLORA_TILE_IDS[f"flora_{p}{v}_n"]
                elif has_s and has_w:
                    return FLORA_TILE_IDS[f"flora_{p}{v}_w"]
                else:  # has_w and has_n
                    return FLORA_TILE_IDS[f"flora_{p}{v}_s"]

            # Everything else (full, edges, inside corners, isolated) → full tile
            v = rng.choice([1, 4])
            d = rng.choice(["n", "e", "s", "w"])
            return FLORA_TILE_IDS[f"flora_{p}{v}_{d}"]

        for tx, ty in flora_b_patch:
            flora_overlay[ty * MAP_W + tx] = pick_flora_tile(tx, ty, flora_b_patch, "B")
        for tx, ty in flora_e_patch:
            flora_overlay[ty * MAP_W + tx] = pick_flora_tile(tx, ty, flora_e_patch, "E")

        extra_layers.append({
            "name": "flora_overlay",
            "tiles": flora_overlay,
            "z_offset": 0.02,
        })
        print(f"   Flora overlay: {len(flora_b_patch)} B cells + {len(flora_e_patch)} E cells")

    if extra_layers:
        level_data["extra_layers"] = extra_layers
    post("/level", level_data)

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
        fog_path = scripts_dir / "fog_overlay.lua"
        if fog_path.exists():
            ok = load_script(client, "fog_overlay", str(fog_path), global_script=False)
            print(f"   {'OK' if ok else 'FAIL'}: fog_overlay (entity)")
        else:
            print("   SKIP: vision_cone.lua (not found)")
    else:
        print("5. Uploading scripts...")
        scripts_dir = Path(__file__).parent / "scripts"
        scripts = [
            ("zombie_ai", "zombie_ai.lua", False),
            ("zombie_manager", "zombie_manager.lua", True),
            ("player_combat", "player_combat.lua", False),
            ("fog_overlay", "fog_overlay.lua", False),
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

    # 7d. Register decoration sprite sheets (single-frame, idle-only, native sizes)
    print("   Registering decoration sprites...")
    decoration_sheets = []

    # Trees A5-A10 (lush), B1-B2 (autumn), C1-C2 (evergreen) — 128x344 (diamond anchored)
    for i in range(5, 11):
        decoration_sheets.append((f"tree_a{i}", f"{gen}/Tree_A{i}.png", 128, 317))
    for i in range(1, 3):
        decoration_sheets.append((f"tree_b{i}", f"{gen}/Tree_B{i}.png", 128, 317))
    for i in range(1, 3):
        decoration_sheets.append((f"tree_c{i}", f"{gen}/Tree_C{i}.png", 128, 317))

    # Tree shadow — single image for all trees — 546x512 native
    decoration_sheets.append(("tree_shadow", f"{gen}/Tree_A3_Shadow.png", 546, 512))

    # Flora A: general small plants (18 variants) — 128x256 native
    for i in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19, 20]:
        decoration_sheets.append((f"flora_a{i}", f"{gen}/Flora_A{i}.png", 128, 256))

    # Flora B: bushes/shrubs for brown earth (19 variants) — 128x256 native
    for i in range(1, 20):
        decoration_sheets.append((f"flora_b{i}", f"{gen}/Flora_B{i}.png", 128, 256))

    # Cars — 192x516 (diamond anchored, 10 variants)
    for i in range(1, 11):
        decoration_sheets.append((f"car_{i}", f"{gen}/Car{i}.png", 192, 476))

    # Objects — 128x344 (diamond anchored, 1-4, 16-23, 33, 39-44)
    for i in list(range(1, 5)) + list(range(16, 19)) + list(range(21, 24)) + [33] + list(range(39, 45)):
        decoration_sheets.append((f"object_{i}", f"{gen}/Object{i}.png", 128, 317))

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

    # 8b. Register fog vignette sprite and spawn overlay entity
    post("/sprites/sheets", {
        "name": "fog_v2",
        "path": "fog_v2.png",
        "frame_width": 1536, "frame_height": 1536, "columns": 1, "rows": 1,
        "anchor_y": 0.0,
        "animations": {"idle": {"frames": [0], "fps": 1, "looping": True}},
    })
    post("/entities", {
        "x": spawn_x,
        "y": spawn_y,
        "is_player": False,
        "tags": ["fog_overlay"],
        "script": "fog_overlay",
        "components": [
            {"type": "animation_controller", "graph": "fog_v2",
             "auto_from_velocity": False},
            {"type": "render_layer", "layer": 200},
        ],
    })
    print("   Spawned fog overlay")

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

    # 11b. Spawn decoration entities (variant selected by find_decoration_positions)
    print("   Spawning decorations...")
    deco_count = 0

    # Sprite images are now anchored so diamond base = canvas center.
    # Entity position directly matches visual ground footprint.

    # Trees
    for wx, wy, variant in decorations["trees"]:
        post("/entities", {
            "x": wx, "y": wy,
            "is_player": False,
            "tags": ["decoration", "tree"],
            "components": [
                {"type": "collider", "width": 20, "height": 20},
                {"type": "solid_body"},
                {"type": "animation_controller", "graph": variant, "auto_from_velocity": False},
            ],
        })
        deco_count += 1

    # Cars — larger collider for vehicle footprint
    for wx, wy, variant in decorations["cars"]:
        post("/entities", {
            "x": wx, "y": wy,
            "is_player": False,
            "tags": ["decoration", "car"],
            "components": [
                {"type": "collider", "width": 100, "height": 60},
                {"type": "solid_body"},
                {"type": "animation_controller", "graph": variant, "auto_from_velocity": False},
            ],
        })
        deco_count += 1

    # Objects
    for wx, wy, variant in decorations["objects"]:
        post("/entities", {
            "x": wx, "y": wy,
            "is_player": False,
            "tags": ["decoration", "object"],
            "components": [
                {"type": "collider", "width": 30, "height": 30},
                {"type": "solid_body"},
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
