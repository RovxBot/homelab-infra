USE acore_world;

-- Stormwind Transmog NPC (Ethereal Warpweaver)
-- Source template: legacy transmog creature_template entry 190011, retained
-- by mod-solo-collections for its compatible NPC workflow.
-- Location (as provided):
-- Map: 0 (Eastern Kingdoms), Zone/Area: 1519, Phase: 4294967295
-- X: -8854.853 Y: 795.0616 Z: 96.33459 O: 1.3205462

SET @ENTRY_TRANSMOG := 190011;
SET @GUID_TRANSMOG := (SELECT IFNULL(MAX(guid), 0) + 1 FROM creature);

-- Re-runnable: remove existing spawns for this entry
DELETE FROM creature WHERE id1 = @ENTRY_TRANSMOG;

INSERT INTO creature (
  guid,
  id1,
  map,
  zoneId,
  areaId,
  spawnMask,
  phaseMask,
  equipment_id,
  position_x,
  position_y,
  position_z,
  orientation,
  spawntimesecs,
  wander_distance,
  currentwaypoint,
  curhealth,
  curmana,
  MovementType
) VALUES (
  @GUID_TRANSMOG,
  @ENTRY_TRANSMOG,
  0,
  1519,
  1519,
  1,
  4294967295,
  0,
  -8854.853,
  795.0616,
  96.33459,
  1.3205462,
  300,
  0,
  0,
  0,
  0,
  0
);
