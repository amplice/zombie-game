-- Zombie Manager: maintains ambient zombie population across the world.
-- Respawns zombies far from player when population drops.
-- No waves — DayZ-style persistent zombie presence.

function update(world, dt)
    if world.get_var("game_over") then return end

    -- Throttle to every 60 frames (~1 second)
    local timer = world.get_var("zombie_respawn_timer") or 0
    timer = timer + 1
    world.set_var("zombie_respawn_timer", timer)
    if timer % 60 ~= 0 then return end

    local px = world.get_var("player_x")
    local py = world.get_var("player_y")
    if px == nil or py == nil then return end

    local tile_size = world.get_var("tile_size") or 16
    local map_w = world.get_var("map_width") or 300
    local map_h = world.get_var("map_height") or 200
    local max_zombies = world.get_var("max_zombies") or 50

    -- Read living zombie count from game variable (tracked by zombie_ai.lua)
    local alive_count = world.get_var("alive_zombie_count") or 0

    -- Respawn if below threshold
    local needed = max_zombies - alive_count
    if needed <= 0 then return end

    -- Spawn up to 3 per cycle (don't lag the engine)
    local to_spawn = math.min(needed, 3)

    for s = 1, to_spawn do
        -- Find a valid spawn point far from player
        for attempt = 1, 30 do
            local tx = math.random(2, map_w - 3)
            local ty = math.random(2, map_h - 3)
            local wx = tx * tile_size + tile_size / 2
            local wy = ty * tile_size + tile_size / 2

            if not world.is_solid(wx, wy) then
                local dx = wx - px
                local dy = wy - py
                local dist = math.sqrt(dx * dx + dy * dy)

                -- Spawn far from player (at least 500px away)
                if dist > 500 then
                    local variant = pick_variant(dist)
                    spawn_zombie(world, wx, wy, variant)
                    break
                end
            end
        end
    end
end

function pick_variant(dist_from_player)
    local roll = math.random(1, 100)
    if roll <= 15 then
        return "runner"
    elseif roll <= 25 then
        return "tank"
    else
        return "normal"
    end
end

function spawn_zombie(world, x, y, variant)
    local hp = 3
    local speed = 90
    local tags = {"enemy", "zombie", "zombie_normal"}
    local collider_w = 34
    local collider_h = 42
    local damage = 1

    if variant == "tank" then
        hp = 8
        speed = 55
        tags = {"enemy", "zombie", "zombie_tank"}
        collider_w = 44
        collider_h = 52
        damage = 2
    elseif variant == "runner" then
        hp = 1
        speed = 160
        tags = {"enemy", "zombie", "zombie_runner"}
        collider_w = 28
        collider_h = 36
    end

    local sprite_name = "zombie_" .. variant

    world.spawn({
        x = x,
        y = y,
        tags = tags,
        script = "zombie_ai",
        health = hp,
        is_player = false,
        components = {
            {type = "collider", width = collider_w, height = collider_h},
            {type = "top_down_mover", speed = speed},
            {type = "hitbox", width = collider_w + 10, height = collider_h + 10, offset_x = 0, offset_y = 0, active = false, damage = damage, damage_tag = "player"},
            {type = "animation_controller", graph = sprite_name, auto_from_velocity = true},
        },
    })
    -- Track alive count (single writer: this manager script)
    local count = world.get_var("alive_zombie_count") or 0
    world.set_var("alive_zombie_count", count + 1)
end
