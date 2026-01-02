-- Nubmage + Daish (NPC spawns) for AzerothCore (acore_world)
-- NOTE:
-- - This file is safe to keep uncommitted while you iterate.
-- - It does NOT attempt to simulate "player gear" visuals. NPCs can reliably show weapons, not full tier/pvp sets.
-- - Apply later when you’re ready, e.g.:
--   kubectl -n wotlk exec -i wotlk-mariadb-0 -- sh -lc 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" acore_world' < ops/wotlk-npcs/nubmage-dresk.sql

USE acore_world;

-- --------------------------------------------------------------------
-- IDs (adjust if you already use these ranges)
-- --------------------------------------------------------------------
SET @ENTRY_NUBMAGE := 4000000;
SET @ENTRY_DRESK   := 4000001; -- Daish
SET @ENTRY_HEALER1 := 4000002;
SET @ENTRY_HEALER2 := 4000003;

-- --------------------------------------------------------------------
-- Reference models (copied from existing templates in your DB)
--   - Undercity Mage: entry 18971 -> display 18454, faction 1759 (undead caster look)
--   - Stormwind Marshal: entry 19386 -> display 19431 (more "player-like" Alliance plate look)
--   - Alliance Cleric: entry 26805 -> display 24356 (we override faction to 11)
-- --------------------------------------------------------------------
SET @DISPLAY_UNDERCITY_MAGE := 18454;
SET @DISPLAY_STORMWIND_GUARD := 19431;
SET @DISPLAY_ALLIANCE_CLERIC := 24356;

-- Factions
SET @FACTION_HORDE_CITY := 1759;
SET @FACTION_ALLIANCE := 11;

-- --------------------------------------------------------------------
-- Cleanup (re-runnable)
-- --------------------------------------------------------------------
DELETE FROM creature_formations
WHERE leaderGUID IN (SELECT guid FROM creature WHERE id1 IN (@ENTRY_DRESK, @ENTRY_HEALER1, @ENTRY_HEALER2))
   OR memberGUID IN (SELECT guid FROM creature WHERE id1 IN (@ENTRY_DRESK, @ENTRY_HEALER1, @ENTRY_HEALER2));
DELETE FROM waypoint_data WHERE id IN (SELECT guid FROM creature WHERE id1 = @ENTRY_DRESK);
DELETE FROM creature WHERE id1 IN (@ENTRY_NUBMAGE, @ENTRY_DRESK, @ENTRY_HEALER1, @ENTRY_HEALER2);
DELETE FROM creature_template_model WHERE CreatureID IN (@ENTRY_NUBMAGE, @ENTRY_DRESK, @ENTRY_HEALER1, @ENTRY_HEALER2);
DELETE FROM creature_template WHERE entry IN (@ENTRY_NUBMAGE, @ENTRY_DRESK, @ENTRY_HEALER1, @ENTRY_HEALER2);

-- Allocate GUIDs dynamically (so the file works on any DB state)
SET @GUID_BASE := (SELECT IFNULL(MAX(guid), 0) + 1 FROM creature);
SET @GUID_NUBMAGE := @GUID_BASE;
SET @GUID_DRESK   := @GUID_BASE + 1;
SET @GUID_HEALER1 := @GUID_BASE + 2;
SET @GUID_HEALER2 := @GUID_BASE + 3;

-- --------------------------------------------------------------------
-- Templates
-- unit_class: 8 = mage/caster, 2 = paladin-ish, 1 = warrior-ish
-- We keep defaults modest; tune later (damage/health) if you want them tougher.
-- --------------------------------------------------------------------
INSERT INTO creature_template (entry, name, subname, minlevel, maxlevel, exp, faction, npcflag, speed_walk, speed_run, scale, `rank`, dmgschool, unit_class, unit_flags, unit_flags2, dynamicflags, type, type_flags, AIName, MovementType, HoverHeight, HealthModifier, ManaModifier, ArmorModifier, ExperienceModifier, RacialLeader, RegenHealth, mechanic_immune_mask, spell_school_immune_mask, flags_extra)
VALUES
  (@ENTRY_NUBMAGE, 'Nubmage', 'Portal Service', 80, 80, 2, @FACTION_HORDE_CITY, 0, 1, 1.14286, 1, 0, 0, 8, 0, 0, 0, 7, 0, 'SmartAI', 0, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0),
  (@ENTRY_DRESK,   'Daish',   'Wintergrasp Champion', 60, 60, 1, @FACTION_ALLIANCE, 0, 1, 1.14286, 1, 1, 0, 2, 0, 0, 0, 7, 0, 'SmartAI', 2, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0),
  (@ENTRY_HEALER1, 'Daish\'s Healer', NULL, 60, 60, 1, @FACTION_ALLIANCE, 0, 1, 1.14286, 1, 0, 0, 8, 0, 0, 0, 7, 0, 'SmartAI', 0, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0),
  (@ENTRY_HEALER2, 'Daish\'s Healer', NULL, 60, 60, 1, @FACTION_ALLIANCE, 0, 1, 1.14286, 1, 0, 0, 8, 0, 0, 0, 7, 0, 'SmartAI', 0, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0);

INSERT INTO creature_template_model (CreatureID, Idx, CreatureDisplayID, DisplayScale, Probability)
VALUES
  (@ENTRY_NUBMAGE, 0, @DISPLAY_UNDERCITY_MAGE, 1, 1),
  (@ENTRY_DRESK,   0, @DISPLAY_STORMWIND_GUARD, 1, 1),
  (@ENTRY_HEALER1, 0, @DISPLAY_ALLIANCE_CLERIC, 1, 1),
  (@ENTRY_HEALER2, 0, @DISPLAY_ALLIANCE_CLERIC, 1, 1);

-- --------------------------------------------------------------------
-- Equipment (weapons)
--   - The Untamed Blade: item 19334
--   - Soulseeker: item 22799
-- --------------------------------------------------------------------
DELETE FROM creature_equip_template WHERE CreatureID IN (@ENTRY_NUBMAGE, @ENTRY_DRESK);
INSERT INTO creature_equip_template (CreatureID, ID, ItemID1, ItemID2, ItemID3)
VALUES
  (@ENTRY_DRESK,   1, 19334, 0, 0),
  (@ENTRY_NUBMAGE, 1, 22799, 0, 0);

-- --------------------------------------------------------------------
-- Spawns
-- Nubmage (Orgrimmar bank)
-- From your .gps:
--   Map: 1 (Kalimdor) Zone/Area: 1637 (Orgrimmar)
-- --------------------------------------------------------------------
INSERT INTO creature (guid, id1, map, zoneId, areaId, spawnMask, phaseMask, equipment_id, position_x, position_y, position_z, orientation, spawntimesecs, wander_distance, currentwaypoint, curhealth, curmana, MovementType)
VALUES
  (@GUID_NUBMAGE, @ENTRY_NUBMAGE, 1, 1637, 1637, 1, 1, 1, 1611.2494, -4388.4680, 10.524337, 3.6819048, 300, 0, 0, 0, 0, 0);

-- Daish + healers (currently using your Blackrock Mountain path points)
INSERT INTO creature (guid, id1, map, zoneId, areaId, spawnMask, phaseMask, equipment_id, position_x, position_y, position_z, orientation, spawntimesecs, wander_distance, currentwaypoint, curhealth, curmana, MovementType)
VALUES
  (@GUID_DRESK,   @ENTRY_DRESK,   0, 25, 25, 1, 1, 1, -7692.5690, -1087.0372, 217.71353, 1.1909260, 300, 0, 0, 0, 0, 2),
  (@GUID_HEALER1, @ENTRY_HEALER1, 0, 25, 25, 1, 1, 0, -7692.5690, -1087.0372, 217.71353, 1.1909260, 300, 0, 0, 0, 0, 0),
  (@GUID_HEALER2, @ENTRY_HEALER2, 0, 25, 25, 1, 1, 0, -7692.5690, -1087.0372, 217.71353, 1.1909260, 300, 0, 0, 0, 0, 0);

-- --------------------------------------------------------------------
-- Formation: healers follow Daish
-- dist/angle are in yards/radians-ish (angle is used by core as facing offset)
-- --------------------------------------------------------------------
INSERT INTO creature_formations (leaderGUID, memberGUID, dist, angle, groupAI, point_1, point_2)
VALUES
  (@GUID_DRESK, @GUID_HEALER1, 2.5, 2.2, 2, 0, 0),
  (@GUID_DRESK, @GUID_HEALER2, 2.5, 4.1, 2, 0, 0);

-- --------------------------------------------------------------------
-- Waypoints: Daish patrol loop (Blackrock Mountain points you provided)
-- --------------------------------------------------------------------
INSERT INTO waypoint_data (id, point, position_x, position_y, position_z, orientation, delay, move_type, action, action_chance, wpguid)
VALUES
  (@GUID_DRESK, 1,  -7692.5690,  -1087.0372, 217.71353, NULL, 0, 0, 0, 100, 0),
  (@GUID_DRESK, 2,  -7662.7380,  -1041.3998, 225.61966, NULL, 0, 0, 0, 100, 0),
  (@GUID_DRESK, 3,  -7609.1570,  -1017.90125,240.53793, NULL, 0, 0, 0, 100, 0),
  (@GUID_DRESK, 4,  -7546.7495,  -1027.6194, 255.44817, NULL, 0, 0, 0, 100, 0),
  (@GUID_DRESK, 5,  -7498.7217,  -1081.6483, 264.92593, NULL, 0, 0, 0, 100, 0),
  (@GUID_DRESK, 6,  -7490.6772,  -1143.7604, 264.80300, NULL, 0, 0, 0, 100, 0),
  (@GUID_DRESK, 7,  -7521.4220,  -1186.5950, 256.97855, NULL, 0, 0, 0, 100, 0),
  (@GUID_DRESK, 8,  -7568.8290,  -1217.5265, 244.40808, NULL, 0, 0, 0, 100, 0),
  (@GUID_DRESK, 9,  -7617.3340,  -1217.7589, 232.17680, NULL, 0, 0, 0, 100, 0),
  (@GUID_DRESK, 10, -7668.8916,  -1183.1165, 218.79893, NULL, 0, 0, 0, 100, 0),
  (@GUID_DRESK, 11, -7696.5960,  -1128.0096, 215.45802, NULL, 0, 0, 0, 100, 0),
  (@GUID_DRESK, 12, -7693.8057,  -1089.9984, 217.53280, NULL, 0, 0, 0, 100, 0);

-- --------------------------------------------------------------------
-- Daish: yells while patrolling
-- --------------------------------------------------------------------
DELETE FROM creature_text WHERE CreatureID = @ENTRY_DRESK;
INSERT INTO creature_text (CreatureID, GroupID, ID, Text, Type, Language, Probability, Emote, Duration, Sound, BroadcastTextId, TextRange, comment)
VALUES
  (@ENTRY_DRESK, 0, 0, 'Daish! Daish! Daish!', 14, 0, 100, 0, 0, 0, 0, 0, 'Daish loop yell');

DELETE FROM smart_scripts WHERE entryorguid = @ENTRY_DRESK AND source_type = 0;
INSERT INTO smart_scripts (entryorguid, source_type, id, link, event_type, event_phase_mask, event_chance, event_flags,
  event_param1, event_param2, event_param3, event_param4, event_param5, event_param6,
  action_type, action_param1, action_param2, action_param3, action_param4, action_param5, action_param6,
  target_type, target_param1, target_param2, target_param3, target_param4, target_x, target_y, target_z, target_o, comment)
VALUES
  -- Out of combat: yell periodically while patrolling.
  (@ENTRY_DRESK, 0, 0, 0, 1, 0, 100, 0,
   15000, 30000, 45000, 90000, 0, 0,
   1, 0, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish - OOC - Yell line 0');

-- --------------------------------------------------------------------
-- Daish pocket healers: copy the useful parts of Alliance Cleric (26805)
--   - 17139: Power Word: Shield (aura)
--   - 17843: Flash Heal
-- This keeps Dresk alive without adding offensive spells.
-- --------------------------------------------------------------------
DELETE FROM smart_scripts WHERE entryorguid IN (@ENTRY_HEALER1, @ENTRY_HEALER2) AND source_type = 0;
INSERT INTO smart_scripts (entryorguid, source_type, id, link, event_type, event_phase_mask, event_chance, event_flags,
  event_param1, event_param2, event_param3, event_param4, event_param5, event_param6,
  action_type, action_param1, action_param2, action_param3, action_param4, action_param5, action_param6,
  target_type, target_param1, target_param2, target_param3, target_param4, target_x, target_y, target_z, target_o, comment)
VALUES
  -- Friendly missing aura: shield.
  (@ENTRY_HEALER1, 0, 0, 0, 16, 0, 100, 2,
   17139, 40, 6000, 6000, 1, 0,
   11, 17139, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish Healer 1 - Friendly Missing Aura - Cast Power Word: Shield'),
  (@ENTRY_HEALER2, 0, 0, 0, 16, 0, 100, 2,
   17139, 40, 6000, 6000, 1, 0,
   11, 17139, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish Healer 2 - Friendly Missing Aura - Cast Power Word: Shield'),

  -- Friendly missing health: flash heal.
  (@ENTRY_HEALER1, 0, 1, 0, 14, 0, 100, 2,
   10000, 40, 3000, 3000, 0, 0,
   11, 17843, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish Healer 1 - Friendly Missing Health - Cast Flash Heal'),
  (@ENTRY_HEALER2, 0, 1, 0, 14, 0, 100, 2,
   10000, 40, 3000, 3000, 0, 0,
   11, 17843, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish Healer 2 - Friendly Missing Health - Cast Flash Heal');

-- --------------------------------------------------------------------
-- Nubmage: portal spam + occasional trash talk (simple timed chatter)
-- --------------------------------------------------------------------
DELETE FROM creature_text WHERE CreatureID = @ENTRY_NUBMAGE;
INSERT INTO creature_text (CreatureID, GroupID, ID, Text, Type, Language, Probability, Emote, Duration, Sound, BroadcastTextId, TextRange, comment)
VALUES
  (@ENTRY_NUBMAGE, 0, 0, 'Nubmage portal service, best portal service for you!', 12, 0, 100, 0, 0, 0, 0, 0, 'Nubmage ad'),
  (@ENTRY_NUBMAGE, 0, 1, 'Nubmage portal service! Step right up!', 12, 0, 100, 0, 0, 0, 0, 0, 'Nubmage ad'),
  (@ENTRY_NUBMAGE, 1, 0, 'Nubmage sees you. Nubmage is not impressed.', 12, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash'),
  (@ENTRY_NUBMAGE, 1, 1, 'Nubmage offers portals, and you offer... nothing.', 12, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash'),
  (@ENTRY_NUBMAGE, 1, 2, 'Nubmage waits. Nubmage grows bored. Nubmage judges you.', 12, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash'),
  (@ENTRY_NUBMAGE, 1, 3, 'Nubmage could port you far away. Nubmage chooses not to.', 12, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash'),
  (@ENTRY_NUBMAGE, 1, 4, 'Nubmage hears your footsteps. Nubmage hears your indecision.', 12, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash'),
  (@ENTRY_NUBMAGE, 1, 5, 'Nubmage is a master of space and time. You are a master of wasting it.', 12, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash'),
  (@ENTRY_NUBMAGE, 1, 6, 'Nubmage has many portals. You have many excuses.', 12, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash');

DELETE FROM smart_scripts WHERE entryorguid = @ENTRY_NUBMAGE AND source_type = 0;
INSERT INTO smart_scripts (entryorguid, source_type, id, link, event_type, event_phase_mask, event_chance, event_flags,
  event_param1, event_param2, event_param3, event_param4, event_param5, event_param6,
  action_type, action_param1, action_param2, action_param3, action_param4, action_param5, action_param6,
  target_type, target_param1, target_param2, target_param3, target_param4, target_x, target_y, target_z, target_o, comment)
VALUES
  -- Portal sales pitch (often).
  (@ENTRY_NUBMAGE, 0, 0, 0, 1, 0, 100, 0,
   15000, 30000, 60000, 120000, 0, 0,
   1, 0, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - OOC - Say ad lines'),
  -- Occasional third-person trash talk.
  (@ENTRY_NUBMAGE, 0, 1, 0, 1, 0, 100, 0,
   60000, 90000, 180000, 360000, 0, 0,
   1, 1, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - OOC - Say trash lines');

-- Done.
