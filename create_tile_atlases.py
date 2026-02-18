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


def copy_tile(name, direction="_S"):
    """Copy a source tile to output directory without modification."""
    src_path = os.path.join(SRC_DIR, f"{name}{direction}.png")
    if not os.path.exists(src_path):
        print(f"  SKIP: {name}{direction}.png not found")
        return False
    out_name = name.replace(" ", "_") + ".png"
    out_path = os.path.join(OUT_DIR, out_name)
    shutil.copy2(src_path, out_path)
    print(f"  OK: {name} -> {out_name} (direct copy)")
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

    # ── Decoration entities: RECENTER + SCALE ──
    # These are standalone sprites, not overlapping tiles.

    print("\nTree decorations (recentered, scaled to 128x256):")
    for name in ["Tree A1", "Tree A2", "Tree A3", "Tree A4"]:
        recenter_and_scale(name, target_size=(128, 256))

    print("\nCar decorations (recentered, scaled to 128x256):")
    for name in ["Car1", "Car2", "Car3"]:
        recenter_and_scale(name, target_size=(128, 256))

    print("\nFlora decorations (recentered, scaled to 64x128):")
    for name in ["Flora A1", "Flora A10", "Flora A11", "Flora A12"]:
        recenter_and_scale(name, target_size=(64, 128))

    print("\nObject decorations (recentered, scaled to 64x128):")
    for name in ["Object1", "Object2", "Object3", "Object4"]:
        recenter_and_scale(name, target_size=(64, 128))

    print(f"\n=== Done! Processed tiles in: {OUT_DIR} ===")


if __name__ == "__main__":
    main()
