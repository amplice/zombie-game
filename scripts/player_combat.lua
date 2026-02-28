-- Player combat + survival: melee attack, contact damage, pickups, stamina/sprint, game over
-- 8-directional sprite support: faces mouse, attacks toward mouse cursor.
-- Minimizes API calls per frame to avoid Lua reference leaks.
function update(entity, world, dt)
    if world.get_var("game_over") then return end

    -- Initialize state (or re-init after restart via game variable signal)
    local needs_reset = world.get_var("player_needs_reset")
    if entity.state.init == nil or needs_reset then
        entity.state.init = true
        if needs_reset then world.set_var("player_needs_reset", false) end
        entity.state.prev_health = entity.health
        entity.state.prev_score = -1
        entity.state.prev_kills = -1
        entity.state.facing_x = 0
        entity.state.facing_y = 1
        entity.state.tick = 0
        entity.state.stamina = 100
        entity.state.sprint_active = false
        entity.state.attack_cd = 0
        entity.state.attacking = false
        entity.state.attack_hit_done = false
        entity.state.attack_fx = 0
        entity.state.attack_fy = 0
        entity.state.base_speed = entity.speed or 200
        entity.state.pickup_text = ""
        entity.state.pickup_text_timer = 0
        entity.state.dying = false
    end

    entity.state.tick = entity.state.tick + 1

    -- Dying state: keep entity frozen and alive while die animation plays
    if entity.state.dying then
        entity.alive = true  -- prevent engine death_system from respawning
        entity.vx = 0
        entity.vy = 0
        entity.speed = 0
        return
    end

    -- Publish player position for zombie AI
    world.set_var("player_x", entity.x)
    world.set_var("player_y", entity.y)

    -- ── 8-direction facing from mouse cursor ──
    local mx = world.input.mouse_x
    local my = world.input.mouse_y
    local dx = mx - entity.x
    local dy = my - entity.y
    local dist = math.sqrt(dx * dx + dy * dy)
    if dist > 1 then
        entity.state.facing_x = dx / dist
        entity.state.facing_y = dy / dist
        -- Publish facing for vision cone light
        world.set_var("facing_x", entity.state.facing_x)
        world.set_var("facing_y", entity.state.facing_y)
        -- Quantize angle to 8 sectors (45° each, centered on cardinal/ordinal directions)
        local angle = math.atan2(dy, dx)
        local sector = math.floor((angle + math.pi + math.pi / 8) / (math.pi / 4)) % 8
        -- Sectors (world Y-up): 0=W, 1=SW, 2=S, 3=SE, 4=E, 5=NE, 6=N, 7=NW
        -- Sheet rows (CW from E): 0=E, 1=SE, 2=S, 3=SW, 4=W, 5=NW, 6=N, 7=NE
        local sector_to_row = {[0]=4, [1]=3, [2]=2, [3]=1, [4]=0, [5]=7, [6]=6, [7]=5}
        entity.facing_direction = sector_to_row[sector]
    end

    -- ── Strafe-aware animation from velocity + facing ──
    local speed_sq = entity.vx * entity.vx + entity.vy * entity.vy
    local anim = entity.animation
    if anim ~= "attack" and anim ~= "attack_melee" and anim ~= "hurt" and anim ~= "die" then
        if speed_sq > 25 then
            local spd = math.sqrt(speed_sq)
            local mvx = entity.vx / spd
            local mvy = entity.vy / spd
            local fx = entity.state.facing_x
            local fy = entity.state.facing_y
            local dot = mvx * fx + mvy * fy
            local cross = fx * mvy - fy * mvx
            if dot > 0.5 then
                if entity.state.sprint_active then
                    entity.animation = "run"
                else
                    entity.animation = "walk"
                end
            elseif dot < -0.5 then
                entity.animation = "run_backwards"
            elseif cross > 0.3 then
                entity.animation = "strafe_left"
            elseif cross < -0.3 then
                entity.animation = "strafe_right"
            else
                entity.animation = "run"
            end
        else
            entity.animation = "idle"
        end
    end

    -- Sprint: hold Shift — use entity.speed to control TopDownMover directly
    local base_speed = entity.state.base_speed
    if entity.state.attacking then
        entity.speed = 0
        entity.state.sprint_active = false
    else
        local wants_sprint = world.input.pressed("sprint")
        if wants_sprint and entity.state.stamina > 0 then
            entity.state.sprint_active = true
            entity.state.stamina = entity.state.stamina - 0.4
            if entity.state.stamina < 0 then
                entity.state.stamina = 0
                entity.state.sprint_active = false
                entity.speed = base_speed
            else
                entity.speed = base_speed * 1.8
            end
        else
            entity.state.sprint_active = false
            entity.speed = base_speed
            -- Regenerate stamina
            if entity.state.stamina < 100 then
                entity.state.stamina = entity.state.stamina + 0.15
                if entity.state.stamina > 100 then entity.state.stamina = 100 end
            end
        end
    end

    -- Update stamina bar (every 10 frames)
    if entity.state.tick % 10 == 0 then
        world.ui.set_progress("stamina_bar", math.floor(entity.state.stamina), 100)
    end

    -- Camera shake on damage (engine handles contact damage via ContactDamage component)
    -- Don't interrupt attack animation with hurt (player committed to the swing)
    if entity.health < entity.state.prev_health then
        world.camera.shake(4.0, 0.2)
        if entity.animation ~= "attack" and entity.animation ~= "attack_melee" then
            entity.animation = "hurt"
        end
    end

    -- Health HUD update only on change
    if entity.health ~= entity.state.prev_health then
        world.ui.set_progress("health_bar", entity.health, entity.max_health)
        world.ui.set_text("health_text", "HP: " .. math.floor(entity.health) .. " / " .. math.floor(entity.max_health))
        entity.state.prev_health = entity.health
    end

    -- Score/kills HUD (every 15 frames)
    if entity.state.tick % 15 == 0 then
        local score = world.get_var("score") or 0
        local kills = world.get_var("zombies_killed") or 0
        if score ~= entity.state.prev_score then
            world.ui.set_text("score_label", "Score: " .. score)
            entity.state.prev_score = score
        end
        if kills ~= entity.state.prev_kills then
            world.ui.set_text("kills_label", "Kills: " .. kills)
            entity.state.prev_kills = kills
        end
    end

    -- Attack cooldown
    if entity.state.attack_cd > 0 then
        entity.state.attack_cd = entity.state.attack_cd - 1
    end

    -- Read weapon stats from game variables
    local atk_damage = world.get_var("attack_damage") or 2
    local atk_range = world.get_var("attack_range") or 30
    local atk_speed = world.get_var("attack_speed") or 20
    local atk_width = atk_range * 1.6  -- hitbox wider than deep for melee sweep

    -- Attack trigger (Z, X, Enter, or left mouse click)
    local want_attack = world.input.just_pressed("attack") or world.input.mouse_just_pressed("left")
    if want_attack and entity.state.attack_cd <= 0 and not entity.state.attacking then
        -- Pick animation: ranged weapons use Attack1 (ranged) when firing, Attack2 (melee) otherwise
        local weapon_level = world.get_var("weapon_level") or 0
        local ammo = world.get_var("ammo") or 0
        if weapon_level >= 2 and ammo > 0 then
            entity.animation = "attack"        -- ranged shot animation (Attack1)
            entity.state.is_ranged_attack = true
        else
            if weapon_level >= 2 then
                entity.animation = "attack_melee"  -- melee swing for ranged weapons (Attack2)
            else
                entity.animation = "attack"         -- melee weapons only have "attack"
            end
            entity.state.is_ranged_attack = false
        end
        entity.state.attacking = true
        entity.state.attack_frame = 0
        entity.state.attack_hit_done = false
        entity.state.attack_fx = entity.state.facing_x
        entity.state.attack_fy = entity.state.facing_y
    end

    -- Active attack: hitbox in front of player during impact window
    if entity.state.attacking then
        entity.state.attack_frame = entity.state.attack_frame + 1
        local fx = entity.state.attack_fx
        local fy = entity.state.attack_fy
        local f = entity.state.attack_frame

        -- Impact window: frames 6-10 of 15-frame animation
        if f >= 6 and f <= 10 then
            -- Position hitbox in front of player based on facing
            entity.hitbox.offset_x = fx * atk_range * 0.6
            entity.hitbox.offset_y = fy * atk_range * 0.6
            entity.hitbox.width = atk_width
            entity.hitbox.height = atk_width
            entity.hitbox.damage = atk_damage
            entity.hitbox.active = true

            -- Slash particles + camera shake on first impact frame
            if not entity.state.attack_hit_done then
                entity.state.attack_hit_done = true
                local hit_x = entity.x + fx * atk_range * 0.6
                local hit_y = entity.y + fy * atk_range * 0.6
                world.spawn_particles("slash", hit_x, hit_y)
                world.camera.shake(1.5, 0.1)
            end
        else
            entity.hitbox.active = false
        end

        -- Ranged attack: fire projectile at frame 7 if this is a ranged attack
        if f == 7 and entity.state.is_ranged_attack then
            local ammo = world.get_var("ammo") or 0
            if ammo > 0 then
                world.set_var("ammo", ammo - 1)
                world.spawn_projectile({
                    x = entity.x + fx * 16,
                    y = entity.y + fy * 16,
                    speed = 400,
                    direction = {x = fx, y = fy},
                    damage = 4,
                    damage_tag = "enemy",
                    owner = entity.id,
                    lifetime = 30,
                })
            end
        end

        -- Attack ends when animation transitions away
        local anim = entity.animation
        if anim ~= "attack" and anim ~= "attack_melee" then
            entity.state.attacking = false
            entity.state.attack_cd = atk_speed
            entity.hitbox.active = false
        end
    else
        -- Not attacking: ensure hitbox is off
        entity.hitbox.active = false
    end

    -- Pick up items (every 45 frames to reduce ref creation)
    -- Rotate between pickup types each check to spread ref load
    local pickup_phase = math.floor(entity.state.tick / 45) % 3
    if entity.state.tick % 45 == 0 then
        if pickup_phase == 0 then
            -- Health pickups
            local pickups = world.find_in_radius(entity.x, entity.y, 40, "health_pickup")
            if #pickups > 0 then
                local heal = 3
                entity.heal(heal)
                pickups[1].set_alive(false)
                world.spawn_particles("heal", entity.x, entity.y)
                local s = world.get_var("score") or 0
                world.set_var("score", s + 5)
                entity.state.pickup_text = "Medkit +3 HP"
                entity.state.pickup_text_timer = 120
            end
        elseif pickup_phase == 1 then
            -- Ammo pickups
            local pickups = world.find_in_radius(entity.x, entity.y, 40, "ammo_pickup")
            if #pickups > 0 then
                pickups[1].set_alive(false)
                local ammo = world.get_var("ammo") or 0
                world.set_var("ammo", ammo + 10)
                local s = world.get_var("score") or 0
                world.set_var("score", s + 3)
                entity.state.pickup_text = "Ammo +6"
                entity.state.pickup_text_timer = 120
            end
        else
            -- Weapon pickups — upgrade weapon and switch sprite sheet
            local pickups = world.find_in_radius(entity.x, entity.y, 40, "weapon_pickup")
            if #pickups > 0 then
                pickups[1].set_alive(false)
                local wlvl = world.get_var("weapon_level") or 0
                if wlvl < 3 then
                    wlvl = wlvl + 1
                    world.set_var("weapon_level", wlvl)
                    -- Upgrade stats based on level
                    if wlvl == 1 then
                        world.set_var("attack_damage", 3)
                        world.set_var("attack_range", 36)
                        world.set_var("attack_speed", 10)
                        -- Switch to bat sprites (already default)
                        entity.animation_graph = "player_bat"
                    elseif wlvl == 2 then
                        world.set_var("attack_damage", 4)
                        world.set_var("attack_range", 40)
                        world.set_var("attack_speed", 8)
                        -- Switch to shotgun sprites
                        entity.animation_graph = "player_shotgun"
                    elseif wlvl == 3 then
                        world.set_var("attack_damage", 6)
                        world.set_var("attack_range", 48)
                        world.set_var("attack_speed", 6)
                    end
                end
                world.spawn_particles("slash", entity.x, entity.y)
                local s = world.get_var("score") or 0
                world.set_var("score", s + 15)
                local upgrade_names = {"Bat acquired!", "Shotgun acquired!", "Arsenal upgraded!"}
                entity.state.pickup_text = upgrade_names[wlvl] or "Weapon upgraded!"
                entity.state.pickup_text_timer = 120
            end
        end
    end

    -- ── Pickup text notification countdown ──
    if entity.state.pickup_text_timer > 0 then
        entity.state.pickup_text_timer = entity.state.pickup_text_timer - 1
        if entity.state.pickup_text_timer % 15 == 0 then
            world.ui.set_text("pickup_text", entity.state.pickup_text)
        end
        if entity.state.pickup_text_timer <= 0 then
            entity.state.pickup_text = ""
            world.ui.set_text("pickup_text", "")
        end
    end

    -- Status updates (every 60 frames, uses game variables to avoid ref leaks)
    if entity.state.tick % 60 == 0 then
        local total = world.get_var("alive_zombie_count") or 0
        if total > 0 then
            world.ui.set_text("threat_label", total .. " zombies in world")
        else
            world.ui.set_text("threat_label", "")
        end

        -- Weapon/ammo HUD
        local wlvl = world.get_var("weapon_level") or 0
        local ammo = world.get_var("ammo") or 0
        local weapon_names = {"Fists", "Bat", "Shotgun", "Arsenal"}
        local wname = weapon_names[wlvl + 1] or "Fists"
        if ammo > 0 then
            world.ui.set_text("weapon_label", wname .. " | Ammo: " .. ammo)
        else
            world.ui.set_text("weapon_label", wname)
        end
    end

    -- Death -> dying state (play die animation before game over)
    if entity.health ~= nil and entity.health <= 0 and not entity.state.dying then
        entity.state.dying = true
        entity.state.death_timer = 0
        entity.alive = true  -- override engine auto-death, keep entity alive for animation
        entity.animation = "die"
        entity.vx = 0
        entity.vy = 0
        entity.speed = 0
        entity.hitbox.active = false
        world.set_var("game_over", true)

        local final_score = world.get_var("score") or 0
        local final_kills = world.get_var("zombies_killed") or 0
        local surv_time = world.get_var("survival_time") or 0
        local mins = math.floor(surv_time / 60)
        local secs = math.floor(surv_time % 60)
        local time_str = mins .. ":" .. string.format("%02d", secs)

        world.ui.set_text("survived_time", "Survived: " .. time_str)
        world.ui.set_text("final_kills", "Zombies killed: " .. final_kills)
        world.ui.set_text("final_score", "Score: " .. final_score)
        world.ui.show_screen("game_over")

        world.camera.shake(8.0, 0.5)
    end
end
