-- Fog of war overlay: large vignette sprite (transparent center → opaque edges)
-- Follows the player with a slight offset toward the mouse cursor direction,
-- creating an egg-shaped visibility area.
function update(entity, world, dt)
    local p = world.player()
    if not p then return end

    -- Read player's facing direction (published by player_combat.lua)
    local fx = world.get_var("facing_x") or 0
    local fy = world.get_var("facing_y") or 1

    -- Offset toward facing direction for directional vision
    local offset = 50

    entity.x = p.x + fx * offset
    entity.y = p.y + fy * offset
end
