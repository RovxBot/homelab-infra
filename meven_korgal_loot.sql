-- SQL to make Meven Korgal (entry 16694) drop Scarlet Crusade Documents (item 28756) 100% for players on quest The Scarlet Crusade (quest 10583)

-- First, insert the condition
INSERT INTO conditions (SourceTypeOrReferenceId, SourceGroup, SourceEntry, SourceId, ElseGroup, ConditionTypeOrReference, ConditionTarget, ConditionValue1, ConditionValue2, ConditionValue3, NegativeCondition, ErrorType, ErrorTextId, ScriptName, Comment) VALUES
(1, 16694, 28756, 0, 0, 9, 0, 10583, 0, 0, 0, 0, 0, '', 'Drop Scarlet Crusade Documents only if player has quest The Scarlet Crusade');

-- Get the condition ID (assuming it's the last inserted)
SET @condition_id = LAST_INSERT_ID();

-- Insert the loot entry with the condition
INSERT INTO creature_loot_template (Entry, Item, Reference, Chance, QuestRequired, LootMode, GroupId, MinCount, MaxCount, Comment, condition_id) VALUES
(16694, 28756, 0, 100, 0, 1, 0, 1, 1, 'Meven Korgal - Scarlet Crusade Documents', @condition_id);