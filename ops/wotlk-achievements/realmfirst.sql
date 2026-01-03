-- Configure "Realm First!" achievements in AzerothCore.
--
-- Notes:
-- - You cannot create brand new achievements without client DBC changes.
-- - This file only adjusts rewards for existing achievements and revokes them from bot accounts.

USE acore_world;

-- --- Reward titles (existing CharTitles.dbc IDs) -----------------------------
-- These are the WotLK "realm first raid" achievements that already have
-- corresponding title strings in CharTitles.dbc.
--
-- Title IDs were extracted from CharTitles.dbc:
-- - 139: "Obsidian Slayer %s"
-- - 120: "%s the Magic Seeker"
-- - 122: "%s, Conqueror of Naxxramas"
-- - 158: "%s, Death's Demise"
-- - 159: "%s the Celestial Defender"
-- - 170: "Grand Crusader %s"
-- - 174: "%s, Bane of the Fallen King"

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
-- mod-playerbots marks bot accounts in acore_playerbots.playerbots_account_type.
-- We treat account_type=1 as "bot" accounts (random bots).
--
-- This removes the achievements (so they aren't considered "claimed by bots").
-- If you want to ALSO remove these titles from bot "knownTitles", that's a
-- separate and riskier change (bitmask/longtext); we only clear chosenTitle.

-- Realm-first achievement IDs extracted from Achievement.dbc (title contains "Realm First"):
-- 456,457,458,459,460,461,462,463,464,465,466,467,959,1400,1402,1404,1405,1406,1407,1408,1409,1410,1411,1412,1413,1414,1415,1416,1417,1418,1419,1420,1421,1422,1423,1424,1425,1426,1427,1463,3117,3259,4078,4576

DELETE FROM acore_characters.character_achievement
WHERE achievement IN (
  456,457,458,459,460,461,462,463,464,465,466,467,959,
  1400,1402,1404,1405,1406,1407,1408,1409,1410,1411,1412,1413,
  1414,1415,1416,1417,1418,1419,1420,1421,1422,1423,1424,1425,1426,1427,
  1463,3117,3259,4078,4576
)
AND guid IN (
  SELECT guid FROM (
    SELECT c.guid
    FROM acore_characters.characters c
    JOIN acore_playerbots.playerbots_account_type p
      ON p.account_id = c.account AND p.account_type = 1
  ) bot_guids
);

UPDATE acore_characters.characters c
JOIN acore_playerbots.playerbots_account_type p
  ON p.account_id = c.account AND p.account_type = 1
SET c.chosenTitle = 0
WHERE c.chosenTitle IN (120, 122, 139, 158, 159, 170, 174);
