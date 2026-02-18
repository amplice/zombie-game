-- Game restart handler: when game_over, wait for keypress then reset
-- Runs as a global script.

function update(world, dt)
    if not world.get_var("game_over") then return end

    -- Debounce: wait a moment after death before allowing restart
    local death_timer = world.get_var("death_timer") or 0
    death_timer = death_timer + dt
    world.set_var("death_timer", death_timer)
    if death_timer < 2.0 then return end

    -- Debug: write input state to game vars so we can inspect
    local atk_jp = world.input.just_pressed("attack")
    local atk_p = world.input.pressed("attack")
    local left_p = world.input.pressed("left")
    local right_p = world.input.pressed("right")
    local up_p = world.input.pressed("up")
    local down_p = world.input.pressed("down")
    local jump_jp = world.input.just_pressed("jump")

    local dbg = "jp_atk=" .. tostring(atk_jp)
        .. " p_atk=" .. tostring(atk_p)
        .. " p_lr=" .. tostring(left_p) .. "/" .. tostring(right_p)
        .. " p_ud=" .. tostring(up_p) .. "/" .. tostring(down_p)
        .. " jp_jmp=" .. tostring(jump_jp)
    world.set_var("restart_debug", dbg)

    -- Show restart hint with debug info from both entity and global script input
    local ent_dbg = world.get_var("entity_input_debug") or "none"
    world.ui.set_text("restart_hint", dbg .. " | " .. ent_dbg)

    -- Check for ANY input to restart (use both pressed and just_pressed)
    local restart = atk_jp or atk_p or left_p or right_p or up_p or down_p
        or world.input.pressed("jump")
        or world.input.pressed("sprint")
        or world.input.just_pressed("left")
        or world.input.just_pressed("right")
        or world.input.just_pressed("up")
        or world.input.just_pressed("down")
        or world.input.just_pressed("sprint")
        or jump_jp

    if not restart then return end

    -- Reset game variables
    world.set_var("game_over", false)
    world.set_var("score", 0)
    world.set_var("zombies_killed", 0)
    world.set_var("survival_time", 0)
    world.set_var("rules_tick", 0)
    world.set_var("death_timer", 0)
    world.set_var("weapon_level", 0)
    world.set_var("ammo", 0)
    world.set_var("attack_damage", 2)
    world.set_var("attack_range", 30)
    world.set_var("attack_speed", 12)
    world.set_var("max_zombies", 50)
    world.set_var("player_needs_reset", true)

    -- Hide game over screen
    world.ui.hide_screen("game_over")

    -- Reset HUD
    world.ui.set_text("score_label", "Score: 0")
    world.ui.set_text("kills_label", "Kills: 0")
    world.ui.set_text("time_label", "0:00")
    world.ui.set_text("weapon_label", "Fists")
    world.ui.set_text("threat_label", "")
    world.ui.set_text("restart_hint", "")

    -- Respawn player: find player and heal via command (property assignment
    -- on world.player() snapshots does NOT persist — must use :heal() method)
    local player = world.player()
    if player then
        -- heal() queues a real ECS command that actually restores health
        local missing = (player.max_health or 10) - (player.health or 0)
        if missing > 0 then
            player.heal(missing)
        end

        -- Teleport player back to spawn
        local spawn_x = world.get_var("spawn_x") or 2408
        local spawn_y = world.get_var("spawn_y") or 1608
        player.set_position(spawn_x, spawn_y)
        player.set_velocity(0, 0)
        world.set_var("player_x", spawn_x)
        world.set_var("player_y", spawn_y)

        local mhp = player.max_health or 10
        world.ui.set_progress("health_bar", mhp, mhp)
        world.ui.set_text("health_text", "HP: " .. math.floor(mhp) .. " / " .. math.floor(mhp))
        world.ui.set_progress("stamina_bar", 100, 100)
    end
end
