#!/usr/bin/env python3
"""
Pre-process isometric rural tileset PNGs for the Axiom engine.

Simplified: copy source tiles directly (NO recentering) so that the native
tile overlap creates seamless isometric ground when rendered back-to-front.

Decoration entity sprites (trees, cars, etc.) get recentered + scaled since
they're standalone entities, not overlapping tilemap tiles.
"""

from PIL import Image
import shutil
import os

SRC_DIR = os.path.join(os.path.dirname(__file__),
                       "assets/sprites/tilesets/rural_tileset/Isometric Tiles")
OUT_DIR = os.path.join(os.path.dirname(__file__),
                       "assets/sprites/tilesets/generated")

# Source tile dimensions
SRC_W, SRC_H = 128, 256
DIAMOND_CENTER_Y = 216  # approximate diamond center in source images


def copy_tile(name, direction="_S", include_dir=False):
    """Copy a source tile to output directory without modification."""
    src_path = os.path.join(SRC_DIR, f"{name}{direction}.png")
    if not os.path.exists(src_path):
        print(f"  SKIP: {name}{direction}.png not found")
        return False
    suffix = direction if include_dir else ""
    out_name = name.replace(" ", "_") + suffix + ".png"
    out_path = os.path.join(OUT_DIR, out_name)
    shutil.copy2(src_path, out_path)
    print(f"  OK: {name}{direction} -> {out_name} (direct copy)")
    return True


def recenter_and_scale(name, direction="_S", target_size=None):
    """Recenter a decoration tile (shift diamond to center) and optionally scale.
    Used for entity sprites (not tilemap tiles)."""
    src_path = os.path.join(SRC_DIR, f"{name}{direction}.png")
    if not os.path.exists(src_path):
        print(f"  SKIP: {name}{direction}.png not found")
        return False

    img = Image.open(src_path).convert("RGBA")
    w, h = img.size

    # Calculate shift to center the diamond
    diamond_y = int(h * DIAMOND_CENTER_Y / SRC_H)
    center_y = h // 2
    shift = diamond_y - center_y

    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(img, (0, -shift))

    if target_size:
        canvas = canvas.resize(target_size, Image.LANCZOS)

    out_name = name.replace(" ", "_") + ".png"
    out_path = os.path.join(OUT_DIR, out_name)
    canvas.save(out_path)

    suffix = f" ({target_size[0]}x{target_size[1]})" if target_size else ""
    print(f"  OK: {name} -> {out_name}{suffix}")
    return True


def resize_tile(name, target_size, direction="_S"):
    """Resize a tile to target_size without any recentering/shifting."""
    src_path = os.path.join(SRC_DIR, f"{name}{direction}.png")
    if not os.path.exists(src_path):
        print(f"  SKIP: {name}{direction}.png not found")
        return False

    img = Image.open(src_path).convert("RGBA")
    img = img.resize(target_size, Image.LANCZOS)

    out_name = name.replace(" ", "_") + ".png"
    out_path = os.path.join(OUT_DIR, out_name)
    img.save(out_path)
    print(f"  OK: {name} -> {out_name} ({target_size[0]}x{target_size[1]})")
    return True


def resize_and_anchor(name, target_size, direction="_S"):
    """Resize a tile and expand the canvas so the diamond base is at the
    canvas center. This way the engine's collider (at entity center) aligns
    with the visual ground footprint.

    The diamond base in the source tile is at DIAMOND_CENTER_Y (216 out of 256).
    After resize, we figure out where the diamond is, then pad the bottom of
    the image so the diamond ends up at the vertical center of the final canvas.
    """
    src_path = os.path.join(SRC_DIR, f"{name}{direction}.png")
    if not os.path.exists(src_path):
        print(f"  SKIP: {name}{direction}.png not found")
        return False

    img = Image.open(src_path).convert("RGBA")
    # Resize first
    img = img.resize(target_size, Image.LANCZOS)
    w, h = img.size

    # Diamond base position in resized image (proportional from source)
    diamond_y = int(DIAMOND_CENTER_Y / SRC_H * h)

    # The entity position from iso_grid_to_world is near the diamond top,
    # not the diamond center. Use ~40% of the full padding.
    extra_padding = int((diamond_y - h // 2) * 0.7)
    new_h = h + extra_padding
    if new_h <= h:
        # Diamond is already at or above center, no padding needed
        canvas = img
    else:
        canvas = Image.new("RGBA", (w, new_h), (0, 0, 0, 0))
        canvas.paste(img, (0, 0))  # artwork at top, padding at bottom

    out_name = name.replace(" ", "_") + ".png"
    out_path = os.path.join(OUT_DIR, out_name)
    canvas.save(out_path)
    final_w, final_h = canvas.size
    print(f"  OK: {name} -> {out_name} ({final_w}x{final_h}, diamond centered)")
    return True


def copy_tree(name, direction="_S"):
    """Copy a tree sprite with an alpha fade at the bottom.

    The bottom of tree sprites has painted ground detail (roots, shadow) that
    clashes with the ground tiles underneath.  We fade the bottom ~15% of the
    sprite to transparent so it blends smoothly into the ground.
    """
    src_path = os.path.join(SRC_DIR, f"{name}{direction}.png")
    if not os.path.exists(src_path):
        print(f"  SKIP: {name}{direction}.png not found")
        return False

    img = Image.open(src_path).convert("RGBA")
    w, h = img.size
    pixels = img.load()

    # Fade the bottom 35% of the sprite from full opacity to transparent
    fade_start = int(h * 0.65)  # where fade begins (65% down)
    fade_height = h - fade_start

    for y in range(fade_start, h):
        # 1.0 at fade_start → 0.0 at bottom
        t = 1.0 - (y - fade_start) / fade_height
        for x in range(w):
            r, g, b, a = pixels[x, y]
            pixels[x, y] = (r, g, b, int(a * t))

    out_name = name.replace(" ", "_") + ".png"
    out_path = os.path.join(OUT_DIR, out_name)
    img.save(out_path)
    print(f"  OK: {name} -> {out_name} (bottom alpha fade)")
    return True


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("=== Processing isometric tiles ===\n")

    # ── Tilemap tiles: DIRECT COPY (no recentering) ──
    # These rely on natural overlap for seamless isometric ground.

    # All Ground series — one representative tile from each for now,
    # plus extras for visual variety within each terrain type.
    print("Ground A tiles (dark earth/mud):")
    for i in range(1, 17):
        copy_tile(f"Ground A{i}")

    print("\nGround B tiles (brown earth):")
    for i in range(1, 15):
        copy_tile(f"Ground B{i}")

    print("\nGround C tiles (stone/special):")
    for i in range(1, 5):
        copy_tile(f"Ground C{i}")

    print("\nGround D tiles (dirt road):")
    for i in range(1, 6):
        copy_tile(f"Ground D{i}")

    print("\nGround E tiles (gravel/path):")
    for i in range(1, 11):
        copy_tile(f"Ground E{i}")

    print("\nGround F tiles (farmland):")
    for i in range(1, 6):
        copy_tile(f"Ground F{i}")

    print("\nGround G tiles (green grass):")
    for i in range(1, 11):
        copy_tile(f"Ground G{i}")

    print("\nWall tiles (direct copy):")
    for name in ["Wall A1", "Wall A2", "Wall A3", "Wall A4",
                  "Wall A5", "Wall A6", "Wall A7", "Wall A8"]:
        copy_tile(name)

    print("\nFence tiles (direct copy):")
    for name in ["Fence A1", "Fence A2", "Fence A3", "Fence A4"]:
        copy_tile(name)

    # ── Decoration entities: DIRECT COPY at original size ──

    # Trees A5-A10 (lush), B1-B2 (autumn), C1-C2 (evergreen) — resized, diamond anchored
    print("\nTree A decorations (resized + anchored):")
    for i in range(5, 11):
        resize_and_anchor(f"Tree A{i}", (128, 256))

    print("\nTree B decorations (resized + anchored):")
    for i in range(1, 3):
        resize_and_anchor(f"Tree B{i}", (128, 256))

    print("\nTree C decorations (resized + anchored):")
    for i in range(1, 3):
        resize_and_anchor(f"Tree C{i}", (128, 256))

    # Tree shadow — single shadow image used for all trees (no _S suffix)
    print("\nTree shadow (direct copy, 546x512):")
    copy_tile("Tree A3_Shadow", direction="")

    print("\nCar decorations (resized + anchored):")
    for i in range(1, 11):
        resize_and_anchor(f"Car{i}", (192, 384))

    # Flora A: general small plants (18 variants) — direct copy at 128x256
    print("\nFlora A decorations (direct copy):")
    for i in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19, 20]:
        copy_tile(f"Flora A{i}")

    # Flora B: autotile variants (B1/B4=full, B2/B5/B6=corners) x 4 directions
    print("\nFlora B autotile variants (direct copy):")
    for i in [1, 2, 4, 5, 6]:
        for d in ["_N", "_E", "_S", "_W"]:
            copy_tile(f"Flora B{i}", direction=d, include_dir=True)

    # Flora E: autotile variants (E1/E4=full, E2/E5/E6=corners) x 4 directions
    print("\nFlora E autotile variants (direct copy):")
    for i in [1, 2, 4, 5, 6]:
        for d in ["_N", "_E", "_S", "_W"]:
            copy_tile(f"Flora E{i}", direction=d, include_dir=True)

    print("\nObject decorations (anchored):")
    for i in list(range(1, 5)) + list(range(16, 19)) + list(range(21, 24)) + [33] + list(range(39, 45)):
        resize_and_anchor(f"Object{i}", (128, 256))

    print(f"\n=== Done! Processed tiles in: {OUT_DIR} ===")


if __name__ == "__main__":
    main()
