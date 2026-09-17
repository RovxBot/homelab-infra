USE acore_world;

-- Orgrimmar Transmog NPC (Warpweaver)
-- Source template: legacy transmog creature_template entry 190010, retained
-- by mod-solo-collections for its compatible NPC workflow.
-- Location (as provided):
-- Map: 1 (Kalimdor), Zone/Area: 1637, Phase: 4294967295
-- X: 1921.1827 Y: -4427.4233 Z: 24.90695 O: 4.5310626

SET @ENTRY_TRANSMOG := 190010;
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
  1,
  1637,
  1637,
  1,
  4294967295,
  0,
  1921.1827,
  -4427.4233,
  24.90695,
  4.5310626,
  300,
  0,
  0,
  0,
  0,
  0
);
