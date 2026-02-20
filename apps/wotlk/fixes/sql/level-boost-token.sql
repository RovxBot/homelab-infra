-- This item will drop from bosses in Classic, TBC and WotLK raids, and can be used to boost a character to level 60.

USE acore_world;

-- Configure the boost token item ID.
SET @BOOST_TOKEN_ITEM_ID :=90004;

-- Exclude known classic,tbc and wotlk dungeons (these values match instance_encounters.lastEncounterDungeon in your DB).
EXCLUDE_CLASSIC_DUNGEON_IDs="33,34,36,43,47,48,70,90,109,129,209,229,230,329,349,389,429,1001,1004,1007" -- Please query ID for dungeons in classic
EXCLUDE_TBC_DUNGEON_IDs="269,540,542,543,545,546,547,552,553,554,555,556,557,558,560,585" -- Please query ID for dungeons in TBC
EXCLUDE_WOTLK_DUNGEON_IDs="574,575,579,578,595,599,600,601,602,604,608,619,632,650,658,668" -- Please query ID for dungeons in WotLK

-- Add the item into the Database, and update it if it already exists.
INSERT IGNORE INTO item_template 
  (entry, class, subclass, name, displayid, Quality, bonding, stackable, maxcount, BuyPrice, SellPrice, description)
   
VALUES

  (@BOOST_TOKEN_ITEM_ID, 10, 0, 'Sands of Infinite Wisdom', 29735, 5, 1, 1000, 2, 0, 0, 'The power emanating from the sands is almost overwhelming.'),


UPDATE item_template
  SET bonding = 1, SellPrice = 0, BuyPrice = 0, stackable = 1000
  WHERE entry = @BOOST_TOKEN_ITEM_ID;


-- Getting the item to drop from raid bosses in Classic, TBC and WotLK raids.

INSERT INTO creature_loot_template 
  (Entry, Item, Reference, Chance, QuestRequired, LootMode, GroupId, MinCount, MaxCount, Comment)
    
SELECT DISTINCT 
  ie.creditEntry AS boss_entry,
  @BOOST_TOKEN_ITEM_ID,
  0,
  0.01,
  0,
  1,
  0,
  1,
  1,
  'Sands of Infinite Wisdom'
-- Please check this enitre section to make sure that it is dropping correctly and excluded from dungeons
FROM instance_id ie
JOIN creature_template ct ON ct.entry = ie.creditEntry
WHERE ie.lastEncounterDungeon != 0
  AND ie.creditType = 0
  AND ct.exp = 0
  AND ie.lastEncounterDungeon NOT IN (${EXCLUDE_CLASSIC_DUNGEON_IDs})
  AND ie.lastEncounterDungeon NOT IN (${EXCLUDE_TBC_DUNGEON_IDs})
  AND ie.lastEncounterDungeon NOT IN (${EXCLUDE_WOTLK_DUNGEON_IDs});
ON DUPLICATE KEY UPDATE
  Chance = VALUES(Chance),
  QuestRequired = VALUES(QuestRequired),
  LootMode = VALUES(LootMode),
  GroupId = VALUES(GroupId),
  MinCount = VALUES(MinCount),
  MaxCount = VALUES(MaxCount),
  Comment = VALUES(Comment);
  
  -- Sanity check: ensure the item exists.
SELECT 'boost_token_item' AS k, COUNT(*) AS v 
FROM item_template 
WHERE entry = @BOOST_TOKEN_ITEM_ID
