-- Game rules: survival timer, zone detection, HUD updates
-- Runs as global script. Throttled to avoid Lua reference leaks.

function update(world, dt)
    if world.get_var("game_over") then return end

    -- Track survival time
    local surv = world.get_var("survival_time") or 0
    surv = surv + dt
    world.set_var("survival_time", surv)

    -- Update time display every 30 frames
    local tick = world.get_var("rules_tick") or 0
    tick = tick + 1
    world.set_var("rules_tick", tick)

    if tick % 30 == 0 then
        local mins = math.floor(surv / 60)
        local secs = math.floor(surv % 60)
        world.ui.set_text("time_label", mins .. ":" .. string.format("%02d", secs))
    end

    -- Zone detection (every 60 frames)
    if tick % 60 == 0 then
        local px = world.get_var("player_x")
        local py = world.get_var("player_y")
        if px == nil or py == nil then return end

        local zone_count = world.get_var("zone_count") or 0
        local current_zone = "Wilderness"

        for i = 0, zone_count - 1 do
            local cx = world.get_var("zone_" .. i .. "_cx")
            local cy = world.get_var("zone_" .. i .. "_cy")
            local radius = world.get_var("zone_" .. i .. "_radius")
            local name = world.get_var("zone_" .. i .. "_name")

            if cx and cy and radius and name then
                local dx = px - cx
                local dy = py - cy
                local dist = math.sqrt(dx * dx + dy * dy)
                if dist < radius then
                    -- Capitalize first letter
                    current_zone = name:sub(1,1):upper() .. name:sub(2)
                    break
                end
            end
        end

        world.ui.set_text("zone_label", current_zone)
    end

    -- Gradually increase max zombies over time (every 30 seconds)
    if tick % 1800 == 0 then
        local max_z = world.get_var("max_zombies") or 50
        if max_z < 80 then
            world.set_var("max_zombies", max_z + 5)
        end
    end
end
