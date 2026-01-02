-- Extra RP/flavor lines for mod-playerbots
-- DB: acore_playerbots
--
-- Apply:
--   kubectl -n wotlk exec -i wotlk-mariadb-0 -- sh -lc 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" acore_playerbots' < ops/wotlk-playerbots/rp-texts.sql

USE acore_playerbots;

START TRANSACTION;

-- Add more variety to commonly-used chat buckets.
-- NOTE: We leave localized columns empty (English only) for now.
INSERT INTO ai_playerbot_texts
  (name, text, say_type, reply_type, text_loc1, text_loc2, text_loc3, text_loc4, text_loc5, text_loc6, text_loc7, text_loc8)
VALUES
  -- ------------------------------------------------------------
  -- HELLO / GOODBYE
  -- ------------------------------------------------------------
  ('hello', 'Well met.', 0, 0, '', '', '', '', '', '', '', ''),
  ('hello', 'Greetings, traveler.', 0, 0, '', '', '', '', '', '', '', ''),
  ('hello', 'How goes it?', 0, 0, '', '', '', '', '', '', '', ''),
  ('hello', 'Hello! Need a hand?', 0, 0, '', '', '', '', '', '', '', ''),
  ('hello', 'Ah, company. Good.', 0, 0, '', '', '', '', '', '', '', ''),
  ('hello', 'By the Light, hello.', 0, 0, '', '', '', '', '', '', '', ''),
  ('hello', 'Lok’tar! What’s the plan?', 0, 0, '', '', '', '', '', '', '', ''),
  ('hello', 'Peace, friend.', 0, 0, '', '', '', '', '', '', '', ''),
  ('hello', 'Hey! Ready when you are.', 0, 0, '', '', '', '', '', '', '', ''),
  ('hello', 'Alright then… let’s do this.', 0, 0, '', '', '', '', '', '', '', ''),

  ('goodbye', 'Safe travels.', 0, 0, '', '', '', '', '', '', '', ''),
  ('goodbye', 'May your path be clear.', 0, 0, '', '', '', '', '', '', '', ''),
  ('goodbye', 'Until next time.', 0, 0, '', '', '', '', '', '', '', ''),
  ('goodbye', 'Catch you at the inn.', 0, 0, '', '', '', '', '', '', '', ''),
  ('goodbye', 'I’ll be around.', 0, 0, '', '', '', '', '', '', '', ''),
  ('goodbye', 'Good hunting.', 0, 0, '', '', '', '', '', '', '', ''),
  ('goodbye', 'Don’t get yourself killed.', 0, 0, '', '', '', '', '', '', '', ''),
  ('goodbye', 'I’ll see you in the city.', 0, 0, '', '', '', '', '', '', '', ''),

  -- ------------------------------------------------------------
  -- LOW RESOURCE / STATUS
  -- ------------------------------------------------------------
  ('low mana', 'Give me a moment… my mana is running thin.', 0, 0, '', '', '', '', '', '', '', ''),
  ('low mana', 'Hold up. I need to drink.', 0, 0, '', '', '', '', '', '', '', ''),
  ('low mana', 'Out of mana. Someone cover me!', 0, 0, '', '', '', '', '', '', '', ''),
  ('low mana', 'No mana, no miracles. One sec.', 0, 0, '', '', '', '', '', '', '', ''),
  ('low mana', 'Mana break. The Lich King can wait.', 0, 0, '', '', '', '', '', '', '', ''),

  ('low health', 'I’m hurt… I could use a heal.', 0, 0, '', '', '', '', '', '', '', ''),
  ('low health', 'Bleeding here. A little help?', 0, 0, '', '', '', '', '', '', '', ''),
  ('low health', 'Not my best day. Heal me when you can.', 0, 0, '', '', '', '', '', '', '', ''),
  ('low health', 'I’m taking heavy hits!', 0, 0, '', '', '', '', '', '', '', ''),
  ('critical health', 'I am NOT dying here!', 0, 0, '', '', '', '', '', '', '', ''),
  ('critical health', 'This is bad! I need a heal NOW!', 0, 0, '', '', '', '', '', '', '', ''),
  ('critical health', 'I can’t take another hit!', 0, 0, '', '', '', '', '', '', '', ''),

  -- ------------------------------------------------------------
  -- AOE / LOOT / TAUNT (high-frequency RP)
  -- ------------------------------------------------------------
  ('aoe', 'That’s a lot of enemies… I don’t like this.', 0, 0, '', '', '', '', '', '', '', ''),
  ('aoe', 'If you pull the room, you tank the room.', 0, 0, '', '', '', '', '', '', '', ''),
  ('aoe', 'Stack them up—make it clean.', 0, 0, '', '', '', '', '', '', '', ''),
  ('aoe', 'Big pull… please say you meant to do that.', 0, 0, '', '', '', '', '', '', '', ''),
  ('aoe', 'I’m going to need a bigger mana bar.', 0, 0, '', '', '', '', '', '', '', ''),
  ('aoe', 'Alright. Time to make a mess.', 0, 0, '', '', '', '', '', '', '', ''),
  ('aoe', 'If I die, tell the innkeeper I tried.', 0, 0, '', '', '', '', '', '', '', ''),
  ('aoe', 'Please keep them off me.', 0, 0, '', '', '', '', '', '', '', ''),
  ('aoe', 'I see red… and not in a good way.', 0, 0, '', '', '', '', '', '', '', ''),
  ('aoe', 'This is why we bring healers.', 0, 0, '', '', '', '', '', '', '', ''),
  ('aoe', 'Someone mark targets…? No? Great.', 0, 0, '', '', '', '', '', '', '', ''),
  ('aoe', 'If this goes wrong, I’m blaming the hunter.', 0, 0, '', '', '', '', '', '', '', ''),

  ('loot', 'Hands off—this one’s mine.', 0, 0, '', '', '', '', '', '', '', ''),
  ('loot', 'Let’s see what the Scourge was carrying…', 0, 0, '', '', '', '', '', '', '', ''),
  ('loot', 'If it glitters, it’s coming with me.', 0, 0, '', '', '', '', '', '', '', ''),
  ('loot', 'Please be upgrades. Please be upgrades.', 0, 0, '', '', '', '', '', '', '', ''),
  ('loot', 'Looting fast—watch my back.', 0, 0, '', '', '', '', '', '', '', ''),
  ('loot', 'I swear I’m just checking for gold.', 0, 0, '', '', '', '', '', '', '', ''),
  ('loot', 'Bag space is a myth.', 0, 0, '', '', '', '', '', '', '', ''),
  ('loot', 'Another handful of coins. Nice.', 0, 0, '', '', '', '', '', '', '', ''),
  ('loot', 'If it’s gray, it stays.', 0, 0, '', '', '', '', '', '', '', ''),
  ('loot', 'Someone bring a vendor…', 0, 0, '', '', '', '', '', '', '', ''),

  ('taunt', 'Come then, <target>!', 0, 0, '', '', '', '', '', '', '', ''),
  ('taunt', 'You picked the wrong fight, <target>.', 0, 0, '', '', '', '', '', '', '', ''),
  ('taunt', 'I’ve fought ghouls with better manners, <target>.', 0, 0, '', '', '', '', '', '', '', ''),
  ('taunt', 'Your courage is impressive. Your aim is not, <target>.', 0, 0, '', '', '', '', '', '', '', ''),
  ('taunt', 'Is that all you’ve got, <target>?', 0, 0, '', '', '', '', '', '', '', ''),
  ('taunt', 'Face me, <target>—or run.', 0, 0, '', '', '', '', '', '', '', ''),
  ('taunt', 'I hope you brought friends, <target>.', 0, 0, '', '', '', '', '', '', '', ''),
  ('taunt', 'I’ll send you back to the graveyard, <target>.', 0, 0, '', '', '', '', '', '', '', ''),
  ('taunt', 'You look lost, <target>. Let me help.', 0, 0, '', '', '', '', '', '', '', ''),
  ('taunt', 'I’ve got time for this, <target>. Do you?', 0, 0, '', '', '', '', '', '', '', ''),
  ('taunt', 'Tell your leader you tried, <target>.', 0, 0, '', '', '', '', '', '', '', ''),
  ('taunt', 'I’ve survived worse than you, <target>.', 0, 0, '', '', '', '', '', '', '', ''),

  -- ------------------------------------------------------------
  -- LEVEL UP (adds flavor to the broadcast buckets)
  -- ------------------------------------------------------------
  ('broadcast_levelup_generic', 'Level %my_level! I’m getting the hang of this.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_levelup_generic', 'Ding! %my_level. Next stop: better gear.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_levelup_generic', 'Level %my_level—still breathing, still fighting.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_levelup_generic', 'Just hit %my_level. Onward!', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_levelup_generic', '%my_level! The road to Northrend continues.', 0, 0, '', '', '', '', '', '', '', ''),

  ('broadcast_levelup_max_level', 'Level %my_level! Dalaran is calling.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_levelup_max_level', 'Finally %my_level. Time to chase epics.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_levelup_max_level', 'Max level %my_level—now the real grind begins.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_levelup_max_level', '%my_level achieved. Point me at a dungeon.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_levelup_max_level', 'Level %my_level! I’m ready for the Frozen Throne.', 0, 0, '', '', '', '', '', '', '', '');

COMMIT;

-- -------------------------------------------------------------------------------------------------
-- BROADCASTS (server-wide flavor lines triggered by playerbots broadcasts system)
-- These are NOT the same as SayStrategy lines; they go through the broadcast logic.
-- -------------------------------------------------------------------------------------------------

START TRANSACTION;

INSERT INTO ai_playerbot_texts
  (name, text, say_type, reply_type, text_loc1, text_loc2, text_loc3, text_loc4, text_loc5, text_loc6, text_loc7, text_loc8)
VALUES
  -- Looting (poor/normal/uncommon/rare+). Uses %item_link.
  ('broadcast_looting_item_poor', 'Ugh… %item_link. Straight to the vendor.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_poor', 'Another %item_link. My bags are crying.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_poor', 'If anyone needs %item_link… no? Didn’t think so.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_poor', 'I looted %item_link. It smells like regret.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_poor', '%item_link. The true treasure is disappointment.', 0, 0, '', '', '', '', '', '', '', ''),

  ('broadcast_looting_item_normal', 'Picked up %item_link.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_normal', 'Loot: %item_link.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_normal', 'Found %item_link. Better than nothing.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_normal', 'Yoink—%item_link.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_normal', 'Got %item_link. Tossing it in the bag.', 0, 0, '', '', '', '', '', '', '', ''),

  ('broadcast_looting_item_uncommon', 'Nice—%item_link!', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_uncommon', 'Green drop: %item_link.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_uncommon', 'I’ll take that %item_link.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_uncommon', 'Found %item_link. Not bad at all.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_uncommon', '%item_link… that might actually be useful.', 0, 0, '', '', '', '', '', '', '', ''),

  ('broadcast_looting_item_rare', 'Blue! %item_link!', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_rare', 'Now we’re talking—%item_link.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_rare', 'Looted %item_link. Finally, something shiny.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_rare', 'I see %item_link. I smile.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_rare', 'Found %item_link. That’s a keeper.', 0, 0, '', '', '', '', '', '', '', ''),

  ('broadcast_looting_item_epic', 'Purple drop: %item_link. Let’s goooo!', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_epic', '%item_link!! That’s the good stuff.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_legendary', '…Is that %item_link?! I’m never taking it off.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_looting_item_artifact', '%item_link… I don’t even know what to say.', 0, 0, '', '', '', '', '', '', '', ''),

  -- Quest accepted/updates. Uses %quest_link, %quest_obj_* placeholders, %zone_name, %item_link.
  ('broadcast_quest_accepted_generic', 'Picked up %quest_link in %zone_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_accepted_generic', 'New quest: %quest_link.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_accepted_generic', 'Taking %quest_link. Should be quick… right?', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_accepted_generic', 'I’ll handle %quest_link. Meet you at the turn-in.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_accepted_generic', '%quest_link accepted. Time to get moving.', 0, 0, '', '', '', '', '', '', '', ''),

  ('broadcast_quest_update_add_kill_objective_progress', '%quest_obj_full_formatted for %quest_link.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_update_add_kill_objective_progress', 'Still working on %quest_link: %quest_obj_full_formatted.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_update_add_kill_objective_progress', '%quest_link progress—%quest_obj_full_formatted.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_update_add_kill_objective_completed', 'Objective done for %quest_link: %quest_obj_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_update_add_kill_objective_completed', '%quest_obj_name complete for %quest_link.', 0, 0, '', '', '', '', '', '', '', ''),

  ('broadcast_quest_update_add_item_objective_progress', 'Collected %quest_obj_available/%quest_obj_required %item_link for %quest_link.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_update_add_item_objective_progress', '%quest_link: got %quest_obj_available/%quest_obj_required %item_link.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_update_add_item_objective_progress', 'Still need %quest_obj_missing %item_link for %quest_link.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_update_add_item_objective_completed', 'Finished collecting %item_link for %quest_link.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_update_add_item_objective_completed', '%quest_link item objective complete: %item_link.', 0, 0, '', '', '', '', '', '', '', ''),

  ('broadcast_quest_update_failed_timer', 'Timed out on %quest_link… that one got away.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_update_failed_timer', '%quest_link failed—ran out of time.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_update_complete', '%quest_link complete. Heading back.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_update_complete', 'All objectives done for %quest_link!', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_turned_in', 'Turned in %quest_link in %zone_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_turned_in', '%quest_link handed in. On to the next one.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_quest_turned_in', 'Quest done: %quest_link.', 0, 0, '', '', '', '', '', '', '', ''),

  -- Kills. Uses %victim_name and %zone_name.
  ('broadcast_killed_normal', 'Down goes %victim_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_normal', 'Another one bites the dust: %victim_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_normal', '%victim_name defeated in %zone_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_elite', 'Elite %victim_name down. That hit hard.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_elite', 'That elite %victim_name won’t bother anyone again.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_rare', 'Rare %victim_name down—check for loot!', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_rare', 'Found and killed %victim_name in %zone_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_rareelite', 'Rare elite %victim_name defeated. Nice.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_rareelite', '%victim_name (rare elite) is down in %zone_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_worldboss', 'WORLD BOSS %victim_name down!!', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_worldboss', '%victim_name has fallen. That was legendary.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_player', 'PvP: %victim_name down in %zone_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_player', 'Sent %victim_name back to the graveyard.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_unknown', 'Something died. I’m sure it deserved it.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_killed_pet', 'Sorry, %victim_name… wrong place, wrong time.', 0, 0, '', '', '', '', '', '', '', ''),

  -- Guild messages.
  ('broadcast_guild_recruitment', 'Guild recruitment: any brave souls looking for adventure?', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_guild_recruitment', 'If you want a guild that actually does things, we’re right here.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_guild_promotion', 'Grats on the promotion! Don’t let it go to your head.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_guild_promotion', 'Promotion time—well earned!', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_guild_demotion', 'Demoted… but you can earn it back.', 0, 0, '', '', '', '', '', '', '', ''),
  ('broadcast_guild_demotion', 'Oof. Demotion stings. Learn and move on.', 0, 0, '', '', '', '', '', '', '', '');

COMMIT;

-- -------------------------------------------------------------------------------------------------
-- GENERIC CHATTER (the "suggest_something" pool used by RandomBotTalk/broadcast suggestions)
-- Placeholders commonly used by existing lines: %zone_name %my_role %my_race %my_class %my_level
-- -------------------------------------------------------------------------------------------------

START TRANSACTION;

INSERT INTO ai_playerbot_texts
  (name, text, say_type, reply_type, text_loc1, text_loc2, text_loc3, text_loc4, text_loc5, text_loc6, text_loc7, text_loc8)
VALUES
  ('suggest_something', 'Anyone else questing in %zone_name?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I could go for a dungeon run soon.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If you need a %my_role, I’m around.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'What’s everyone working on today?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Feels like a good day to farm some mats.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I keep running in circles in %zone_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Is %zone_name always this dangerous?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I swear I just saw something move over there…', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Where do you all keep finding bags? I need more space.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I should probably go train skills again.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I miss the city. %zone_name is a bit much.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If I vanish for a minute, I’m just repairing.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I wonder what the Auction House looks like right now.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Anyone need a hand with elites in %zone_name?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I feel like %zone_name has a story. Most of it is pain.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’ve got a bad feeling about this place.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I could use a drink. Preferably not water.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m level %my_level and still learning. Be gentle.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Somewhere out there is loot with my name on it.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'We should group up in %zone_name sometime.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m just here for the adventure.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Does anyone else get jumpy in %zone_name at night?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I heard %zone_name has good loot… I heard wrong.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'My %my_class legs are tired. Too much running.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I wonder what’s happening in Dalaran right now.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I could use a hearthstone cooldown refresh.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If you see me wandering, I’m probably “exploring”.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m convinced %zone_name is cursed.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m starting to respect the wildlife in %zone_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I could really use a mailbox right now.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'At this point I’m just collecting stories… and bruises.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I have a plan. Step one: survive %zone_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m enjoying %zone_name. I think. Maybe.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If anyone asks, I was never here.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I should stop staring at the map and start moving.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m pretty sure %zone_name is bigger every time I visit.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'What’s the best inn in %zone_name?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'My bags are full again. How does that keep happening?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If you need me, I’ll be nearby… probably.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I wonder if the gryphon masters ever sleep.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Sometimes I think my %my_role job is just “panic politely”.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m not lost. I’m scouting.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m going to pretend that last pull was intentional.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'There’s a strange peace to %zone_name… between fights.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If you hear screaming, it’s probably fine.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I should have brought more food.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I wonder what level %my_level will feel like. Oh wait.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Some days you win, some days %zone_name wins.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'One more quest, then I’m hearthstoning. Probably.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Is it just me, or is the air different in %zone_name?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m trying to stay focused… but the scenery in %zone_name is wild.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If you find a spare mount, let me know.', 0, 0, '', '', '', '', '', '', '', '');

COMMIT;

-- -------------------------------------------------------------------------------------------------
-- VERY RUDE CHATTER (the "suggest_something_toxic" pool)
-- Keep it "WoW banter" rude (salt/insults), but avoid slurs/hate speech.
-- Placeholders commonly used: %zone_name %my_role %my_class %my_level
-- -------------------------------------------------------------------------------------------------

START TRANSACTION;

INSERT INTO ai_playerbot_texts
  (name, text, say_type, reply_type, text_loc1, text_loc2, text_loc3, text_loc4, text_loc5, text_loc6, text_loc7, text_loc8)
VALUES
  ('suggest_something_toxic', 'If you’re going to pull like that, at least commit to it.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'That was a “plan”? I’ve seen better plans in a murloc camp.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'We’re in %zone_name, not a petting zoo. Pay attention.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m not saying you’re lost, but %zone_name is laughing at you.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If we wipe, I’m logging it as “educational”.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I swear some of you are roleplaying as target dummies.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'Please stop testing your durability on my time.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'You’re standing in the bad. Again. Impressively consistent.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m a %my_role, not a miracle worker.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If we had a copper for every mistake, we could buy %zone_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'Mark targets? No? Cool. Let’s just freestyle death.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If you body-pull one more pack, I’m letting it be “your moment”.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’ve met mobs with better situational awareness.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m starting to think your keyboard has only W and spacebar.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'We can do this the easy way… or whatever way you’re doing.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'You’re level %my_level and still pulling like it’s your first day.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I love surprises. Said no tank ever.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If you want to tank, say so. Otherwise stop auditioning.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'My patience has a cooldown and it’s almost up.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This group has the coordination of a drunk kodo.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If “confidence” did damage, you’d be top DPS.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'We’re not speedrunning. We’re just slow-wiping.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'Imagine using interrupts. Wild concept, I know.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If you’re going to be reckless, at least be effective.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’ve seen better teamwork from the Scourge.', 0, 0, '', '', '', '', '', '', '', '');

COMMIT;
