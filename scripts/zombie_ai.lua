-- Zombie AI: wander when idle, chase player when in range
-- Uses game variables for player position to avoid entity reference leaks.
-- PERF: AI recomputes every 4 frames (staggered), coasts on velocity between.
-- Damage dealt via hitbox component (activated mid-attack swing).
function update(entity, world, dt)
    -- Initialize state
    if entity.state.init == nil then
        entity.state.init = true
        entity.state.wander_timer = math.random(30, 120)
        entity.state.wander_dx = 0
        entity.state.wander_dy = 0
        entity.state.aggro = false
        entity.state.base_speed = entity.speed or 90
        entity.state.prev_health = entity.health or 3
        entity.state.attack_timer = 0
        entity.state.attack_frame = 0
        entity.state.hit_landed = false
        entity.state.tick = math.random(0, 3) -- stagger across zombies
        -- Per-variant knockback
        if entity.has_tag("zombie_tank") then
            entity.state.knockback_force = 160
        elseif entity.has_tag("zombie_runner") then
            entity.state.knockback_force = 80
        else
            entity.state.knockback_force = 120
        end
    end

    if not entity.alive then
        entity.vx = 0
        entity.vy = 0
        return
    end

    entity.state.tick = entity.state.tick + 1

    -- Track health changes for hurt animation (must run every frame)
    local cur_health = entity.health or 0
    if cur_health < entity.state.prev_health then
        entity.animation = "hurt"
        entity.state.prev_health = cur_health
    end
    entity.state.prev_health = cur_health

    -- If playing hurt or die, freeze completely and deactivate hitbox
    local anim = entity.animation or "idle"
    if anim == "hurt" or anim == "die" then
        entity.vx = 0
        entity.vy = 0
        entity.hitbox.active = false
        return
    end

    -- Tick attack cooldown (cheap, every frame)
    if entity.state.attack_timer > 0 then
        entity.state.attack_timer = entity.state.attack_timer - 1
    end

    -- During attack animation: lunge, activate hitbox at impact frame, apply knockback
    if anim == "attack" then
        entity.state.attack_frame = entity.state.attack_frame + 1

        local px = world.get_var("player_x")
        local py = world.get_var("player_y")

        -- Lunge toward player
        if px and py then
            local dx = px - entity.x
            local dy = py - entity.y
            local dist = math.sqrt(dx * dx + dy * dy)
            if dist > 6 then
                entity.vx = (dx / dist) * 30
                entity.vy = (dy / dist) * 30
            else
                entity.vx = 0
                entity.vy = 0
            end

            -- Activate hitbox during impact window (frames 6-10 of 15-frame animation)
            local f = entity.state.attack_frame
            if f >= 6 and f <= 10 then
                entity.hitbox.active = true

                -- Apply knockback once on first hit frame
                if not entity.state.hit_landed and dist < 35 then
                    entity.state.hit_landed = true
                    local player = world.player()
                    if player then
                        local kb = entity.state.knockback_force
                        if dist > 1 then
                            player.knockback((dx / dist) * kb, (dy / dist) * kb)
                        end
                    end
                end
            else
                entity.hitbox.active = false
            end
        end
        return
    end

    -- Not attacking: ensure hitbox is off
    entity.hitbox.active = false

    -- THROTTLE: full AI only every 4 frames, coast on current velocity otherwise
    if entity.state.tick % 4 ~= 0 then return end

    -- Read player position from game variables
    local px = world.get_var("player_x")
    local py = world.get_var("player_y")
    if px == nil or py == nil then return end

    local dx = px - entity.x
    local dy = py - entity.y
    local dist = math.sqrt(dx * dx + dy * dy)

    local chase_range = 350
    local lose_range = 500
    local attack_range = 22

    -- Aggro state machine
    if not entity.state.aggro then
        if dist < chase_range then
            entity.state.aggro = true
        end
    else
        if dist > lose_range then
            entity.state.aggro = false
        end
    end

    if entity.state.aggro then
        -- Attack if close enough and cooldown expired
        if dist < attack_range and entity.state.attack_timer <= 0 then
            entity.animation = "attack"
            entity.state.attack_timer = 45
            entity.state.attack_frame = 0
            entity.state.hit_landed = false
            return
        end

        -- Chase player
        if dist > 8 then
            local nx = dx / dist
            local ny = dy / dist
            local speed = entity.state.base_speed
            entity.vx = nx * speed
            entity.vy = ny * speed
        else
            entity.vx = 0
            entity.vy = 0
        end
    else
        -- Wander randomly
        entity.state.wander_timer = entity.state.wander_timer - 4 -- account for throttle
        if entity.state.wander_timer <= 0 then
            if math.random() > 0.4 then
                local angle = math.random() * 6.28
                entity.state.wander_dx = math.cos(angle)
                entity.state.wander_dy = math.sin(angle)
                entity.state.wander_timer = math.random(40, 100)
            else
                entity.state.wander_dx = 0
                entity.state.wander_dy = 0
                entity.state.wander_timer = math.random(60, 180)
            end
        end
        local wander_speed = 40
        entity.vx = entity.state.wander_dx * wander_speed
        entity.vy = entity.state.wander_dy * wander_speed
    end
end

-- Called by engine when entity dies (has PendingDeath).
function on_death(entity, world)
    -- Guard: on_death logic should only run once
    if entity.state.death_handled then return end
    entity.state.death_handled = true

    entity.animation = "die"
    entity.hitbox.active = false
    entity.vx = 0
    entity.vy = 0

    local score = world.get_var("score") or 0
    local kills = world.get_var("zombies_killed") or 0
    world.set_var("score", score + 10)
    world.set_var("zombies_killed", kills + 1)
    -- Decrement alive count
    local count = world.get_var("alive_zombie_count") or 1
    world.set_var("alive_zombie_count", math.max(0, count - 1))
    -- Death effect
    world.spawn_particles("blood", entity.x, entity.y)
end
