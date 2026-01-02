-- This file is mounted into the wotlk realm-first cleanup job.
--
-- Keep this in sync with ops/wotlk-achievements/realmfirst.sql if you edit it.

-- --- Reward titles (existing CharTitles.dbc IDs) -----------------------------
INSERT INTO acore_world.achievement_reward (ID, TitleA, TitleH)
VALUES
  (456,  139, 139), -- Realm First! Obsidian Slayer (OS+3D)
  (1400, 120, 120), -- Realm First! Magic Seeker (Malygos)
  (1402, 122, 122), -- Realm First! Conqueror of Naxxramas
  (3117, 158, 158), -- Realm First! Death's Demise (Ulduar)
  (3259, 159, 159), -- Realm First! Celestial Defender (Ulduar)
  (4078, 170, 170), -- Realm First! Grand Crusader (ToGC)
  (4576, 174, 174)  -- Realm First! Fall of the Lich King (ICC)
ON DUPLICATE KEY UPDATE
  TitleA = VALUES(TitleA),
  TitleH = VALUES(TitleH);

-- --- Revoke realm-first achievements from bot characters ---------------------
DELETE ca
FROM acore_characters.character_achievement ca
JOIN acore_characters.characters c ON c.guid = ca.guid
JOIN acore_playerbots.playerbots_account_type p
  ON p.account_id = c.account AND p.account_type = 1
WHERE ca.achievement IN (
  456,457,458,459,460,461,462,463,464,465,466,467,959,
  1400,1402,1404,1405,1406,1407,1408,1409,1410,1411,1412,1413,
  1414,1415,1416,1417,1418,1419,1420,1421,1422,1423,1424,1425,1426,1427,
  1463,3117,3259,4078,4576
);

UPDATE acore_characters.characters c
JOIN acore_playerbots.playerbots_account_type p
  ON p.account_id = c.account AND p.account_type = 1
SET c.chosenTitle = 0
WHERE c.chosenTitle IN (120, 122, 139, 158, 159, 170, 174);

