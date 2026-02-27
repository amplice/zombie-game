# Axiom Engine Feedback

## Critical: Sprite Anchor / Collider Offset

The #1 pain point. There's no way to offset a collider from the sprite center, and no way to set a sprite anchor/pivot point. Had to hack around this by padding sprite PNGs with transparent pixels to fake alignment. Either of these would fix it:
- A `pivot` or `anchor` field on `animation_controller` (e.g. `{"anchor": "bottom_center"}` or `{"anchor": [0.5, 0.84]}`)
- An `offset` field on `collider` (like `hitbox` already has)

## Other Improvements (by priority)

1. **Collider shapes** — Diamond/polygon colliders for isometric. Rect and circle don't fit isometric footprints well.

2. **Entity component inspection via API** — `GET /entities` returns component *types* but not their *values*. Can't query an entity's collider size, which made debugging hitbox alignment a guessing game.

3. **Sprite sheet should auto-read dimensions from the PNG** — Every time sprite padding changed, had to manually update `frame_width`/`frame_height` in the registration call. The engine could just read the image header.

4. **Isometric coordinate system documentation** — The relationship between grid coords, world coords, and screen coords in isometric mode is unclear. `iso_grid_to_world` lives in game code, not the engine. A built-in grid-to-world helper or at least clear docs on what coordinate space entities live in would help a lot.

5. **Debug visualization toggle via API** — Something like `POST /debug {"show_colliders": true}` to toggle collision box rendering on/off remotely.

## What Works Great

- The HTTP API + hot reload loop is excellent. Rebuilding the game live while it's running is a killer workflow.
- Extra layers for tile overlays work well
- The autotile system is solid
- Lua scripting API is clean and well-documented
- The `/diagnose` and `/health` endpoints are genuinely useful
