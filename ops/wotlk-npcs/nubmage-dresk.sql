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
SET @ENTRY_DAISH   := 4000001; -- Daish
SET @ENTRY_HEALER1 := 4000002;
SET @ENTRY_HEALER2 := 4000003;

-- --------------------------------------------------------------------
-- Reference models (copied from existing templates in your DB)
--   - Undercity Mage: entry 18971 -> display 18454, faction 1759 (undead caster look) - MALE
--   - Stormwind Marshal: entry 19386 -> display 19431 (more "player-like" Alliance plate look) - MALE
--   - Female Human Priest: display 24374 (white robes, female) - FEMALE
-- --------------------------------------------------------------------
SET @DISPLAY_UNDERCITY_MAGE := 18454;    -- Male undead mage (Nubmage)
SET @DISPLAY_STORMWIND_GUARD := 19431;   -- Male human paladin (Daish)
SET @DISPLAY_FEMALE_PRIEST := 24374;     -- Female human priest (Healers)

-- Factions
SET @FACTION_HORDE_CITY := 1759;
SET @FACTION_ALLIANCE := 11;

-- --------------------------------------------------------------------
-- Cleanup (re-runnable)
-- --------------------------------------------------------------------
DELETE FROM creature_formations
WHERE leaderGUID IN (SELECT guid FROM creature WHERE id1 IN (@ENTRY_DRESK, @ENTRY_HEALER1, @ENTRY_HEALER2))
   OR memberGUID IN (SELECT guid FROM creature WHERE id1 IN (@ENTRY_DRESK, @ENTRY_HEALER1, @ENTRY_HEALER2));
DELETE FROM waypoint_data WHERE id IN (SELECT guid FROM creature WHERE id1 = @ENTRY_DAISH);
DELETE FROM smart_scripts WHERE entryorguid IN (@ENTRY_NUBMAGE, @ENTRY_DAISH, @ENTRY_HEALER1, @ENTRY_HEALER2) AND source_type = 0;
DELETE FROM creature_text WHERE CreatureID IN (@ENTRY_NUBMAGE, @ENTRY_DAISH);
DELETE FROM creature_equip_template WHERE CreatureID IN (@ENTRY_NUBMAGE, @ENTRY_DAISH);
DELETE FROM conditions WHERE SourceTypeOrReferenceId = 15 AND SourceGroup = @ENTRY_NUBMAGE;
DELETE FROM gossip_menu_option WHERE MenuID = @ENTRY_NUBMAGE;
DELETE FROM gossip_menu WHERE MenuID = @ENTRY_NUBMAGE;
DELETE FROM creature_template_gossip WHERE CreatureID = @ENTRY_NUBMAGE
DELETE FROM creature WHERE id1 IN (@ENTRY_NUBMAGE, @ENTRY_DRESK, @ENTRY_HEALER1, @ENTRY_HEALER2);
DELETE FROM creature_template_model WHERE CreatureID IN (@ENTRY_NUBMAGE, @ENTRY_DRESK, @ENTRY_HEALER1, @ENTRY_HEALER2);
DELETE FROM creature_template WHERE entry IN (@ENTRY_NUBMAGE, @ENTRY_DRESK, @ENTRY_HEALER1, @ENTRY_HEALER2);

-- Allocate GUIDs dynamically (so the file works on any DB state)
SET @GUID_BASE := (SELECT IFNULL(MAX(guid), 0) + 1 FROM creature);
SET @GUID_NUBMAGE := @GUID_BASE;
SET @GUID_DAISH   := @GUID_BASE + 1;
SET @GUID_HEALER1 := @GUID_BASE + 2;
SET @GUID_HEALER2 := @GUID_BASE + 3;

-- --------------------------------------------------------------------
-- Templates
-- unit_class: 8 = mage/caster, 2 = paladin-ish, 1 = warrior-ish
-- We keep defaults modest; tune later (damage/health) if you want them tougher.
-- --------------------------------------------------------------------
INSERT INTO creature_template (entry, name, subname, minlevel, maxlevel, exp, faction, npcflag, speed_walk, speed_run, scale, `rank`, dmgschool, unit_class, unit_flags, unit_flags2, dynamicflags, type, type_flags, AIName, MovementType, HoverHeight, HealthModifier, ManaModifier, ArmorModifier, ExperienceModifier, RacialLeader, RegenHealth, mechanic_immune_mask, spell_school_immune_mask, flags_extra)
VALUES
  (@ENTRY_NUBMAGE, 'Nubmage', 'Portal Service', 80, 80, 2, @FACTION_HORDE_CITY, 1, 1, 1.14286, 1, 0, 0, 8, 0, 0, 0, 7, 0, 'SmartAI', 0, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0),
  (@ENTRY_DAISH,   'Daish',   'Wintergrasp Champion', 60, 60, 1, @FACTION_ALLIANCE, 0, 1, 1.14286, 1, 1, 0, 2, 0, 0, 0, 7, 0, 'SmartAI', 2, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0),
  (@ENTRY_HEALER1, 'Daish\'s Healer', NULL, 60, 60, 1, @FACTION_ALLIANCE, 0, 1, 1.14286, 1, 0, 0, 8, 0, 0, 0, 7, 0, 'SmartAI', 0, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0),
  (@ENTRY_HEALER2, 'Daish\'s Healer', NULL, 60, 60, 1, @FACTION_ALLIANCE, 0, 1, 1.14286, 1, 0, 0, 8, 0, 0, 0, 7, 0, 'SmartAI', 0, 1, 1, 1, 1, 1, 0, 1, 0, 0, 0);

INSERT INTO creature_template_model (CreatureID, Idx, CreatureDisplayID, DisplayScale, Probability)
VALUES
  (@ENTRY_NUBMAGE, 0, @DISPLAY_UNDERCITY_MAGE, 1, 1),   -- Male undead
  (@ENTRY_DAISH,   0, @DISPLAY_STORMWIND_GUARD, 1, 1),  -- Male human
  (@ENTRY_HEALER1, 0, @DISPLAY_FEMALE_PRIEST, 1, 1),    -- Female human
  (@ENTRY_HEALER2, 0, @DISPLAY_FEMALE_PRIEST, 1, 1);    -- Female human

-- --------------------------------------------------------------------
-- Equipment (weapons)
--   - The Untamed Blade: item 19334
--   - Soulseeker: item 22799
-- --------------------------------------------------------------------
DELETE FROM creature_equip_template WHERE CreatureID IN (@ENTRY_NUBMAGE, @ENTRY_DAISH);
INSERT INTO creature_equip_template (CreatureID, ID, ItemID1, ItemID2, ItemID3)
VALUES
  (@ENTRY_DAISH,   1, 19334, 0, 0),
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
  (@GUID_DAISH,   @ENTRY_DAISH,   0, 25, 25, 1, 1, 1, -7692.5690, -1087.0372, 217.71353, 1.1909260, 300, 0, 0, 0, 0, 2),
  (@GUID_HEALER1, @ENTRY_HEALER1, 0, 25, 25, 1, 1, 0, -7692.5690, -1087.0372, 217.71353, 1.1909260, 300, 0, 0, 0, 0, 0),
  (@GUID_HEALER2, @ENTRY_HEALER2, 0, 25, 25, 1, 1, 0, -7692.5690, -1087.0372, 217.71353, 1.1909260, 300, 0, 0, 0, 0, 0);

-- --------------------------------------------------------------------
-- Formation: healers follow Daish
-- dist/angle are in yards/radians-ish (angle is used by core as facing offset)
-- groupAI flags: 1 = members assist leader, 2 = leader assist members, 3 = both
-- IMPORTANT: Leader must be added as a member with dist=0 for formations to work!
-- --------------------------------------------------------------------
INSERT INTO creature_formations (leaderGUID, memberGUID, dist, angle, groupAI, point_1, point_2)
VALUES
  (@GUID_DAISH, @GUID_DAISH,   0,   0,   3, 0, 0),  -- Leader entry (required!)
  (@GUID_DAISH, @GUID_HEALER1, 3.0, 2.2, 3, 0, 0),  -- Left flank
  (@GUID_DAISH, @GUID_HEALER2, 3.0, 4.1, 3, 0, 0);  -- Right flank

-- --------------------------------------------------------------------
-- Waypoints: Daish patrol loop (Blackrock Mountain points you provided)
-- --------------------------------------------------------------------
INSERT INTO waypoint_data (id, point, position_x, position_y, position_z, orientation, delay, move_type, action, action_chance, wpguid)
VALUES
  (@GUID_DAISH, 1,  -7692.5690,  -1087.0372, 217.71353, NULL, 1, 1, 0, 100, 0),
  (@GUID_DAISH, 2,  -7662.7380,  -1041.3998, 225.61966, NULL, 1, 1, 0, 100, 0),
  (@GUID_DAISH, 3,  -7609.1570,  -1017.90125,240.53793, NULL, 1, 1, 0, 100, 0),
  (@GUID_DAISH, 4,  -7546.7495,  -1027.6194, 255.44817, NULL, 1, 1, 0, 100, 0),
  (@GUID_DAISH, 5,  -7498.7217,  -1081.6483, 264.92593, NULL, 1, 1, 0, 100, 0),
  (@GUID_DAISH, 6,  -7490.6772,  -1143.7604, 264.80300, NULL, 1, 1, 0, 100, 0),
  (@GUID_DAISH, 7,  -7521.4220,  -1186.5950, 256.97855, NULL, 1, 1, 0, 100, 0),
  (@GUID_DAISH, 8,  -7568.8290,  -1217.5265, 244.40808, NULL, 1, 1, 0, 100, 0),
  (@GUID_DAISH, 9,  -7617.3340,  -1217.7589, 232.17680, NULL, 1, 1, 0, 100, 0),
  (@GUID_DAISH, 10, -7668.8916,  -1183.1165, 218.79893, NULL, 1, 1, 0, 100, 0),
  (@GUID_DAISH, 11, -7696.5960,  -1128.0096, 215.45802, NULL, 1, 1, 0, 100, 0),
  (@GUID_DAISH, 12, -7693.8057,  -1089.9984, 217.53280, NULL, 1, 1, 0, 100, 0);

-- --------------------------------------------------------------------
-- Daish: yells while patrolling
-- --------------------------------------------------------------------
DELETE FROM creature_text WHERE CreatureID = @ENTRY_DAISH;
INSERT INTO creature_text (CreatureID, GroupID, ID, Text, Type, Language, Probability, Emote, Duration, Sound, BroadcastTextId, TextRange, comment)
VALUES
  (@ENTRY_DAISH, 0, 0, 'Daish! Daish! Daish!', 14, 0, 100, 0, 0, 0, 0, 0, 'Daish loop yell');

DELETE FROM smart_scripts WHERE entryorguid = @ENTRY_DAISH AND source_type = 0;
INSERT INTO smart_scripts (entryorguid, source_type, id, link, event_type, event_phase_mask, event_chance, event_flags,
  event_param1, event_param2, event_param3, event_param4, event_param5, event_param6,
  action_type, action_param1, action_param2, action_param3, action_param4, action_param5, action_param6,
  target_type, target_param1, target_param2, target_param3, target_param4, target_x, target_y, target_z, target_o, comment)
VALUES
  -- Out of combat: yell periodically while patrolling.
  (@ENTRY_DAISH, 0, 0, 0, 1, 0, 100, 0,
   15000, 30000, 45000, 90000, 0, 0,
   1, 0, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish - OOC - Yell line 0'),

  -- Combat: Cast Seal of Command on aggro (20165)
  (@ENTRY_DAISH, 0, 1, 0, 4, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   11, 20165, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish - On Aggro - Cast Seal of Command'),

  -- Combat: Cast Judgement of Command periodically (20271)
  (@ENTRY_DAISH, 0, 2, 0, 0, 0, 100, 0,
   3000, 5000, 8000, 12000, 0, 0,
   11, 20271, 0, 0, 0, 0, 0,
   2, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish - IC - Cast Judgement'),

  -- Combat: Consecration (27173 - Rank 6, level 60)
  (@ENTRY_DAISH, 0, 3, 0, 0, 0, 100, 0,
   5000, 8000, 12000, 15000, 0, 0,
   11, 27173, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish - IC - Cast Consecration'),

  -- Combat: Hammer of Justice stun (10308 - Rank 4)
  (@ENTRY_DAISH, 0, 4, 0, 0, 0, 100, 0,
   10000, 15000, 25000, 30000, 0, 0,
   11, 10308, 0, 0, 0, 0, 0,
   2, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish - IC - Cast Hammer of Justice'),

  -- Combat: Divine Shield when low HP (642)
  (@ENTRY_DAISH, 0, 5, 0, 2, 0, 100, 1,
   0, 20, 0, 0, 0, 0,
   11, 642, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish - HP 20% - Cast Divine Shield (once)');

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
  -- Friendly missing aura: Power Word: Shield (event_flags=0 so it works in open world)
  (@ENTRY_HEALER1, 0, 0, 0, 16, 0, 100, 0,
   17139, 40, 8000, 12000, 0, 0,
   11, 17139, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish Healer 1 - Friendly Missing Aura - Cast Power Word: Shield'),
  (@ENTRY_HEALER2, 0, 0, 0, 16, 0, 100, 0,
   17139, 40, 8000, 12000, 0, 0,
   11, 17139, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish Healer 2 - Friendly Missing Aura - Cast Power Word: Shield'),

  -- Friendly missing health: Flash Heal at 70% HP threshold
  (@ENTRY_HEALER1, 0, 1, 0, 14, 0, 100, 0,
   8000, 70, 2500, 4000, 0, 0,
   11, 17843, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish Healer 1 - Friendly Missing Health - Cast Flash Heal'),
  (@ENTRY_HEALER2, 0, 1, 0, 14, 0, 100, 0,
   8000, 70, 2500, 4000, 0, 0,
   11, 17843, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Daish Healer 2 - Friendly Missing Health - Cast Flash Heal');

-- --------------------------------------------------------------------
-- Nubmage: portal spam + occasional trash talk (simple timed chatter)
-- --------------------------------------------------------------------
DELETE FROM creature_text WHERE CreatureID = @ENTRY_NUBMAGE;
INSERT INTO creature_text (CreatureID, GroupID, ID, Text, Type, Language, Probability, Emote, Duration, Sound, BroadcastTextId, TextRange, comment)
VALUES
  -- GroupID 0: Portal advertisements (yell)
  (@ENTRY_NUBMAGE, 0, 0, 'Nubmage portal service! Best portal service for you!', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage ad'),
  (@ENTRY_NUBMAGE, 0, 1, 'Nubmage portal service! Step right up! Ten gold only!', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage ad'),
  (@ENTRY_NUBMAGE, 0, 2, 'Nubmage takes you anywhere! Orgrimmar! Thunder Bluff! Undercity! Silvermoon!', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage ad'),
  -- GroupID 1: Idle trash talk (yell)
  (@ENTRY_NUBMAGE, 1, 0, 'Nubmage sees you. Nubmage is not impressed.', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash'),
  (@ENTRY_NUBMAGE, 1, 1, 'Nubmage offers portals, and you offer... nothing.', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash'),
  (@ENTRY_NUBMAGE, 1, 2, 'Nubmage waits. Nubmage grows bored. Nubmage judges you.', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash'),
  (@ENTRY_NUBMAGE, 1, 3, 'Nubmage could port you far away. Nubmage chooses not to.', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash'),
  (@ENTRY_NUBMAGE, 1, 4, 'Nubmage hears your footsteps. Nubmage hears your indecision.', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash'),
  (@ENTRY_NUBMAGE, 1, 5, 'Nubmage is a master of space and time. You are a master of wasting it.', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash'),
  (@ENTRY_NUBMAGE, 1, 6, 'Nubmage has many portals. You have many excuses.', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage trash'),
  -- GroupID 2: Not enough gold (yell)
  (@ENTRY_NUBMAGE, 2, 0, 'Nubmage does not accept poverty as payment!', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage no gold'),
  (@ENTRY_NUBMAGE, 2, 1, 'Nubmage laughs at your empty pockets! Come back with gold!', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage no gold'),
  (@ENTRY_NUBMAGE, 2, 2, 'You want portal? Nubmage wants GOLD! Ten gold! You have... nothing!', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage no gold'),
  (@ENTRY_NUBMAGE, 2, 3, 'Nubmage is not a charity! Go farm some gold, peasant!', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage no gold'),
  (@ENTRY_NUBMAGE, 2, 4, 'Nubmage smells broke. Is that you? Yes, that is you.', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage no gold'),
  -- GroupID 3: Successful portal (yell)
  (@ENTRY_NUBMAGE, 3, 0, 'Another satisfied customer! Nubmage is the best!', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage success'),
  (@ENTRY_NUBMAGE, 3, 1, 'Nubmage thanks you for your gold! Safe travels!', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage success'),
  (@ENTRY_NUBMAGE, 3, 2, 'Whoooosh! Nubmage sends you away! Bye bye!', 14, 0, 100, 0, 0, 0, 0, 0, 'Nubmage success');

DELETE FROM smart_scripts WHERE entryorguid = @ENTRY_NUBMAGE AND source_type = 0;
INSERT INTO smart_scripts (entryorguid, source_type, id, link, event_type, event_phase_mask, event_chance, event_flags,
  event_param1, event_param2, event_param3, event_param4, event_param5, event_param6,
  action_type, action_param1, action_param2, action_param3, action_param4, action_param5, action_param6,
  target_type, target_param1, target_param2, target_param3, target_param4, target_x, target_y, target_z, target_o, comment)
VALUES
  -- OOC: Portal sales pitch (often).
  (@ENTRY_NUBMAGE, 0, 0, 0, 1, 0, 100, 0,
   15000, 30000, 60000, 120000, 0, 0,
   1, 0, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - OOC - Yell ad lines'),
  -- OOC: Occasional third-person trash talk.
  (@ENTRY_NUBMAGE, 0, 1, 0, 1, 0, 100, 0,
   60000, 90000, 180000, 360000, 0, 0,
   1, 1, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - OOC - Yell trash lines'),

  -- GOSSIP: Orgrimmar (MenuID 0, OptionID 0) - Player has gold
  (@ENTRY_NUBMAGE, 0, 10, 11, 62, 0, 100, 0,
   @ENTRY_NUBMAGE, 0, 0, 0, 0, 0,
   62, 0, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip Orgrimmar - Close Gossip'),
  (@ENTRY_NUBMAGE, 0, 11, 12, 61, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   1, 3, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip Orgrimmar - Yell success'),
  (@ENTRY_NUBMAGE, 0, 12, 0, 61, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   62, 1, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 1805.70, 342.60, -60.00, 0, 'Nubmage - Gossip Orgrimmar - Teleport'),

  -- GOSSIP: Thunder Bluff (MenuID 0, OptionID 1) - Player has gold
  (@ENTRY_NUBMAGE, 0, 20, 21, 62, 0, 100, 0,
   @ENTRY_NUBMAGE, 1, 0, 0, 0, 0,
   62, 0, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip Thunder Bluff - Close Gossip'),
  (@ENTRY_NUBMAGE, 0, 21, 22, 61, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   1, 3, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip Thunder Bluff - Yell success'),
  (@ENTRY_NUBMAGE, 0, 22, 0, 61, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   62, 1, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, -1030.00, -251.00, 159.00, 0, 'Nubmage - Gossip Thunder Bluff - Teleport'),

  -- GOSSIP: Undercity (MenuID 0, OptionID 2) - Player has gold
  (@ENTRY_NUBMAGE, 0, 30, 31, 62, 0, 100, 0,
   @ENTRY_NUBMAGE, 2, 0, 0, 0, 0,
   62, 0, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip Undercity - Close Gossip'),
  (@ENTRY_NUBMAGE, 0, 31, 32, 61, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   1, 3, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip Undercity - Yell success'),
  (@ENTRY_NUBMAGE, 0, 32, 0, 61, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   62, 0, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 1586.00, 239.00, -52.00, 0, 'Nubmage - Gossip Undercity - Teleport'),

  -- GOSSIP: Silvermoon (MenuID 0, OptionID 3) - Player has gold
  (@ENTRY_NUBMAGE, 0, 40, 41, 62, 0, 100, 0,
   @ENTRY_NUBMAGE, 3, 0, 0, 0, 0,
   62, 0, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip Silvermoon - Close Gossip'),
  (@ENTRY_NUBMAGE, 0, 41, 42, 61, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   1, 3, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip Silvermoon - Yell success'),
  (@ENTRY_NUBMAGE, 0, 42, 0, 61, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   62, 530, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 9738.00, -7454.00, 13.60, 0, 'Nubmage - Gossip Silvermoon - Teleport'),

  -- GOSSIP: Not enough gold responses (MenuID 0, OptionID 10-13)
  (@ENTRY_NUBMAGE, 0, 50, 51, 62, 0, 100, 0,
   @ENTRY_NUBMAGE, 10, 0, 0, 0, 0,
   62, 0, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip No Gold Org - Close Gossip'),
  (@ENTRY_NUBMAGE, 0, 51, 0, 61, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   1, 2, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip No Gold Org - Yell sass'),
  (@ENTRY_NUBMAGE, 0, 52, 53, 62, 0, 100, 0,
   @ENTRY_NUBMAGE, 11, 0, 0, 0, 0,
   62, 0, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip No Gold TB - Close Gossip'),
  (@ENTRY_NUBMAGE, 0, 53, 0, 61, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   1, 2, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip No Gold TB - Yell sass'),
  (@ENTRY_NUBMAGE, 0, 54, 55, 62, 0, 100, 0,
   @ENTRY_NUBMAGE, 12, 0, 0, 0, 0,
   62, 0, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip No Gold UC - Close Gossip'),
  (@ENTRY_NUBMAGE, 0, 55, 0, 61, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   1, 2, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip No Gold UC - Yell sass'),
  (@ENTRY_NUBMAGE, 0, 56, 57, 62, 0, 100, 0,
   @ENTRY_NUBMAGE, 13, 0, 0, 0, 0,
   62, 0, 0, 0, 0, 0, 0,
   7, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip No Gold SM - Close Gossip'),
  (@ENTRY_NUBMAGE, 0, 57, 0, 61, 0, 100, 0,
   0, 0, 0, 0, 0, 0,
   1, 2, 0, 0, 0, 0, 0,
   1, 0, 0, 0, 0, 0, 0, 0, 0, 'Nubmage - Gossip No Gold SM - Yell sass');

-- --------------------------------------------------------------------
-- Nubmage Gossip Menu: Portal destinations (10g each)
-- Uses conditions to show different options based on player gold
-- --------------------------------------------------------------------
SET @GOSSIP_MENU_NUBMAGE := @ENTRY_NUBMAGE;  -- Use entry as menu ID for simplicity

DELETE FROM gossip_menu WHERE MenuID = @GOSSIP_MENU_NUBMAGE;
INSERT INTO gossip_menu (MenuID, TextID)
VALUES (@GOSSIP_MENU_NUBMAGE, 'Yes what is it? Nubmage has many portals to make and very little time!');  -- TextID 1 = generic "Greetings" (or create custom npc_text)

-- Link creature to gossip menu
DELETE FROM creature_template_gossip WHERE CreatureID = @ENTRY_NUBMAGE;
INSERT INTO creature_template_gossip (CreatureID, MenuID)
VALUES (@ENTRY_NUBMAGE, @GOSSIP_MENU_NUBMAGE);

-- Gossip options: Cities (shown when player HAS 10g)
DELETE FROM gossip_menu_option WHERE MenuID = @GOSSIP_MENU_NUBMAGE;
INSERT INTO gossip_menu_option (MenuID, OptionID, OptionIcon, OptionText, OptionBroadcastTextID, OptionType, OptionNpcFlag, ActionMenuID, ActionPoiID, BoxCoded, BoxMoney, BoxText)
VALUES
  -- Options 0-3: Player has enough gold (10g = 100000 copper)
  (@GOSSIP_MENU_NUBMAGE, 1,  6, 'Portal to Thunder Bluff [10g]', 0, 1, 1, 0, 0, 0, 100000, 'Nubmage demands 10 gold for this portal. Pay up!'),
  (@GOSSIP_MENU_NUBMAGE, 2,  6, 'Portal to Undercity [10g]',     0, 1, 1, 0, 0, 0, 100000, 'Nubmage demands 10 gold for this portal. Pay up!'),
  (@GOSSIP_MENU_NUBMAGE, 3,  6, 'Portal to Silvermoon [10g]',    0, 1, 1, 0, 0, 0, 100000, 'Nubmage demands 10 gold for this portal. Pay up!'),
  -- Options 10-13: Player does NOT have enough gold (shows grayed-out style)
  (@GOSSIP_MENU_NUBMAGE, 11, 0, 'Portal to Thunder Bluff [10g] - Not enough gold!', 0, 1, 1, 0, 0, 0, 0, NULL),
  (@GOSSIP_MENU_NUBMAGE, 12, 0, 'Portal to Undercity [10g] - Not enough gold!',     0, 1, 1, 0, 0, 0, 0, NULL),
  (@GOSSIP_MENU_NUBMAGE, 13, 0, 'Portal to Silvermoon [10g] - Not enough gold!',    0, 1, 1, 0, 0, 0, 0, NULL);

-- Conditions: Show "has gold" options only if player has >= 10g
-- Condition type 7 = CONDITION_GOLD (requires player to have X copper)
DELETE FROM conditions WHERE SourceTypeOrReferenceId = 15 AND SourceGroup = @GOSSIP_MENU_NUBMAGE;
INSERT INTO conditions (SourceTypeOrReferenceId, SourceGroup, SourceEntry, SourceId, ElseGroup, ConditionTypeOrReference, ConditionTarget, ConditionValue1, ConditionValue2, ConditionValue3, NegativeCondition, ErrorType, ErrorTextId, ScriptName, Comment)
VALUES
  -- Show options 0-3 only if player has >= 100000 copper (10g)
  (15, @GOSSIP_MENU_NUBMAGE, 1,  0, 0, 7, 0, 100000, 0, 0, 0, 0, 0, '', 'Nubmage - Thunder Bluff option requires 10g'),
  (15, @GOSSIP_MENU_NUBMAGE, 2,  0, 0, 7, 0, 100000, 0, 0, 0, 0, 0, '', 'Nubmage - Undercity option requires 10g'),
  (15, @GOSSIP_MENU_NUBMAGE, 3,  0, 0, 7, 0, 100000, 0, 0, 0, 0, 0, '', 'Nubmage - Silvermoon option requires 10g'),
  -- Show options 10-13 only if player has < 100000 copper (negated condition)
  (15, @GOSSIP_MENU_NUBMAGE, 11, 0, 0, 7, 0, 100000, 0, 0, 1, 0, 0, '', 'Nubmage - Thunder Bluff no-gold option'),
  (15, @GOSSIP_MENU_NUBMAGE, 12, 0, 0, 7, 0, 100000, 0, 0, 1, 0, 0, '', 'Nubmage - Undercity no-gold option'),
  (15, @GOSSIP_MENU_NUBMAGE, 13, 0, 0, 7, 0, 100000, 0, 0, 1, 0, 0, '', 'Nubmage - Silvermoon no-gold option');

-- Done.
