-- SmartAI fix for caster mobs to move towards player if out of melee range
-- Replace <ENTRY> with the creature entry ID (e.g., 16694 for Meven Korgal)

-- First, ensure the creature uses SmartAI
UPDATE creature_template SET AIName = 'SmartAI' WHERE entry = <ENTRY>;

-- Add the condition for distance > 5 yards to victim
INSERT INTO conditions (SourceTypeOrReferenceId, SourceGroup, SourceEntry, SourceId, ElseGroup, ConditionTypeOrReference, ConditionTarget, ConditionValue1, ConditionValue2, ConditionValue3, NegativeCondition, ErrorType, ErrorTextId, ScriptName, Comment) VALUES
(22, <ENTRY>, 0, 0, 0, 28, 0, 1, 5, 1, 0, 0, 0, '', 'Distance to victim > 5 yards');

-- Get the condition ID (assuming it's the last inserted, or use a fixed ID)
SET @condition_id = LAST_INSERT_ID();

-- Add the SmartAI script: Every 10 seconds, if condition met, move chase victim
INSERT INTO smart_scripts (entryorguid, source_type, id, link, event_type, event_param1, event_param2, event_param3, event_param4, action_type, action_param1, action_param2, action_param3, action_param4, action_param5, action_param6, target_type, target_param1, target_param2, target_param3, target_param4, target_x, target_y, target_z, target_o, comment, condition_id)
VALUES
(<ENTRY>, 0, 0, 0, 0, 10000, 10000, 0, 0, 39, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 'Every 10 seconds - Move chase victim if out of range', @condition_id);

-- Note: If you want to apply this to multiple mobs, repeat the above for each <ENTRY>
-- To find caster mobs, you can query: SELECT entry, name FROM creature_template WHERE AIName != 'SmartAI' AND entry IN (SELECT entry FROM creature_template_spell);