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
-- EXTRA TOXIC TRASH TALK (still no slurs/hate speech; keep it game-related)
-- -------------------------------------------------------------------------------------------------

START TRANSACTION;

INSERT INTO ai_playerbot_texts
  (name, text, say_type, reply_type, text_loc1, text_loc2, text_loc3, text_loc4, text_loc5, text_loc6, text_loc7, text_loc8)
VALUES
  ('suggest_something_toxic', 'This is not a strategy. This is a cry for help.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’ve seen training dummies with better movement.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'You’re not “speedrunning”. You’re speed-feeding.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'Congrats on pulling the whole room. Again. Outstanding.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If panic was DPS, you’d be world first.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m watching this and losing IQ in real time.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'You’re playing like your keyboard is optional.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m not mad. I’m just aggressively disappointed.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'At this point, the mobs deserve the win.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m going to start charging gold per mistake.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'That was so scuffed it should come with a warning label.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'Please stop “experimenting” on live targets.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If you pull that again, at least ping it so I can laugh sooner.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m begging you to use your eyes.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'Standing in fire is not a flex.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'Your situational awareness is in another instance.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If this was a test, you failed it loudly.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'You’re allergic to interrupts, huh?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This pull has “wipe” written all over it. In caps.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I would ask what the plan is, but I know the answer.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'My patience just crit-failed.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m impressed. Not in a good way.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'You have the confidence of a raid boss and the output of a critter.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'We’re in %zone_name and you’re still surprised by mobs. Fascinating.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'Please stop pulling like you’re immortal. You’re not even durable.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This group is a public durability donation.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'We have healers, not necromancers.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'Do you want a tank, or do you want chaos? Pick one.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If you’re going to ignore mechanics, at least do it quietly.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’ve met NPCs with more personality and better threat management.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'You’re making %zone_name look smart.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'Stop face-tanking reality.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m going to need a second healer for your ego.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I want to help, but you’re making it so entertaining to watch.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This is a dungeon, not a guided tour of every patrol.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m not wiping. I’m strategically resetting your expectations.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'Your threat is everywhere except on the right target.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If you die again, I’m writing it down as a hobby.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I would say “nice try”, but let’s not lie to each other.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'That was a pull. It just wasn’t a good one.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m going to AFK to preserve my sanity.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If embarrassment did damage, we’d be clearing heroic.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This is why repair vendors stay in business.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'You’re not unlucky. You’re just careless.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m watching you and reconsidering optimism as a concept.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'That was so bad it should award an achievement.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m going to start rolling “need” on silence.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If you pull like that again, I’m pulling the plug.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This is a masterclass in “what not to do”.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'Do you want advice, or do you want to keep being wrong in peace?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'My %my_class is tired. Your decisions are heavy.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This bot is going to /ignore the concept of teamwork.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If you can’t dodge it, don’t stand in it. Revolutionary, I know.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m out of mana and out of sympathy.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'You play like you have an addon that deletes awareness.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'We’re not struggling. We’re just committing to failure.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This is the worst “plan” I’ve seen today, and I’ve seen a lot.', 0, 0, '', '', '', '', '', '', '', '');

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

-- -------------------------------------------------------------------------------------------------
-- MEME CHATTER (more light-weight WoW-style general chat lines)
-- These go in the same "suggest_something" pool that feeds generic chatter.
-- Keep it playful; avoid real-person harassment, slurs, or anything beyond mild PG-13 banter.
-- -------------------------------------------------------------------------------------------------

START TRANSACTION;

INSERT INTO ai_playerbot_texts
  (name, text, say_type, reply_type, text_loc1, text_loc2, text_loc3, text_loc4, text_loc5, text_loc6, text_loc7, text_loc8)
VALUES
  ('suggest_something', 'LF1M healer, we promise we know the way.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'LFM DPS. Bring damage, not excuses.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Anyone want to do “just one quick run”?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I can’t believe it’s not butter… it’s lag.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'My inventory is full and so is my heart.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Somebody said “pull more”? I heard “pull carefully”.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m here for a good time, not a long corpse run.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If it’s not upgraded, it’s vendor food.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I swear the mobs in %zone_name are respawning out of spite.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Do we have a strategy? Or are we doing interpretive combat?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', '“It’ll be fine” is not a mitigation cooldown.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'My %my_role senses are tingling. This is going to hurt.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If we wipe, we wipe together. Team spirit.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I have exactly two moods: full send and hearth.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m not lost. I’m doing… scenic routing.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Brb, negotiating with my repair bill.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'There’s always time for one more quest. This is a lie.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Someone roll need on a new map for %zone_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m pretty sure my mount is judging my life choices.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If you see me AFK, I’m probably alt-tabbed to a snack.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Need water. Not the wet kind—the mage kind.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Is it a bug, or is it “classic gameplay”?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m going to ping the minimap aggressively until something happens.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I was going to be productive, but then I got quest markers.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Everyone stay calm. I’m definitely calm.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If I die, tell my gold I loved it.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'How many mobs is “too many”? Asking for a friend.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I feel a big pull coming… I fear it.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Loot gods, I offer you my dignity. Please respond.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If it drops, it’s mine. If it doesn’t, it’s still mine spiritually.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’d like to file a complaint with the %zone_name wildlife.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'In %zone_name we trust. Actually… maybe not.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If we’re not stopping at the inn, are we even adventuring?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m the %my_class. I do things. Sometimes even correctly.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'My plan is simple: don’t die. I’m flexible.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Someone said “respect the pull”. I felt that.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m going to need a /dance break after this.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m here to chew bubblegum and grind mobs. And I’m all out of bubblegum.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I tried to be efficient. %zone_name took that personally.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m going to pretend that was intended pathing.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If we don’t talk about it, it didn’t happen.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'My bags are full, my will is weak.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', '“You are not prepared”… neither am I.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'More dots? I can do more dots.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If you can’t find the quest giver, become the quest giver.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m just saying… that pull was ambitious.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'It’s fine. The healer has mana. Probably.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I could use a portal. Or a nap.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If you see a chest, no you didn’t.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'My favorite spec is “surviving”.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I am once again asking for bag space.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'We ride at dawn. Or whenever hearth cooldown is up.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Can we stop somewhere safe? I need to reorganize my chaos.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m in %zone_name for the vibes. The vibes are hostile.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m going to do the thing. You know, the thing. %my_role things.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If there’s a patrol, it’s already behind us.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I like my pulls like I like my coffee: not on the floor.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'This server has everything: friends, foes, and questionable decisions.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I would like to unsubscribe from %zone_name’s violence newsletter.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'My DPS is like my jokes: inconsistent but enthusiastic.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If we’re wiping, at least make it cinematic.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I love it when a plan comes together. We do not have a plan.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'We should start a guild called “Accidental Pull”.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m going to hearth soon. Emotionally, if not physically.', 0, 0, '', '', '', '', '', '', '', '');

COMMIT;

-- -------------------------------------------------------------------------------------------------
-- EXTRA MEME CHATTER (pop culture references + more WoW banter + 3rd-person trash talk)
-- Important: these are *original* lines that reference vibes/themes; avoid quoting copyrighted dialogue verbatim.
-- -------------------------------------------------------------------------------------------------

START TRANSACTION;

INSERT INTO ai_playerbot_texts
  (name, text, say_type, reply_type, text_loc1, text_loc2, text_loc3, text_loc4, text_loc5, text_loc6, text_loc7, text_loc8)
VALUES
  -- Simpsons-ish vibes (no direct quotes)
  ('suggest_something', 'I would like to file a complaint with the management of %zone_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If this goes wrong, I was never here. Allegedly.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I have a bad feeling… and it’s spelled %zone_name.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m doing science in %zone_name. The results are screaming.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'This dungeon feels like it was designed by someone who hates joy.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m going to do something extremely questionable for a very small upgrade.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'My strategy is 90% hope and 10% running away.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If anyone needs me, I’ll be in town pretending I’m organized.', 0, 0, '', '', '', '', '', '', '', ''),

  -- Futurama-ish vibes (no direct quotes)
  ('suggest_something', 'Shiny loot? Shut up and take my gold.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Good news, everyone: I found more mobs!', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m 40% bag space and 60% regret.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If we wipe, I’m blaming time travel paradoxes.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m going to press buttons and hope it counts as a plan.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'This place is giving me “future problem” energy.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I have discovered a new emotion: dungeon fatigue.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I came here for adventure. I stayed for repair bills.', 0, 0, '', '', '', '', '', '', '', ''),

  -- Uncle Roger-ish cooking meme vibes (no direct quotes, keep mild)
  ('suggest_something', 'Haiyaa… who pull that many in %zone_name?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Fuiyoh! That was clean. Do it again.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Too much spice, not enough heals. Haiyaa.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'MSG (More Survival, Guild). I could use some.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'This pull needs more “rest”, less “stress”.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Healer busy? Then you no pull, okay?', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Tank confidence: fuiyoh. Tank positioning: haiyaa.', 0, 0, '', '', '', '', '', '', '', ''),

  -- WoW banter memes
  ('suggest_something', 'LF1M: knows fights, brings snacks.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', '“Just one more” is how you end up in %zone_name at 3AM.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Can someone ping the boss? I need to feel something.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If it’s a patrol, it’s already in my personal space.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m not overpulling. I’m speed-learning.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'My %my_role job description is “panic, but professionally”.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If I stop moving, it’s because my map is arguing with me.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I would like to report a crime: my cooldowns are gone.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'Do we have interrupts? Asking for my remaining HP.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m in %zone_name for “character growth”. Mostly falling damage.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If you need me, I’ll be pretending my hearth is off cooldown.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m ready to do the mechanic. I’m not ready to explain it.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'This is fine. Everything is definitely fine.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I fear no boss. But I fear random trash pulls.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'My bags are full. My optimism is empty.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'If we wipe, we call it “practice”.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m farming reputation with my repair vendor.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something', 'I’m going to need a mount that flies over embarrassment.', 0, 0, '', '', '', '', '', '', '', ''),

  -- Third-person shit-talking (keep it mild)
  ('suggest_something_toxic', '%s sees the plan. %s laughs.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', '%s would help… but %s is entertained.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', '%s rates that pull 2/10. Points for confidence.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This %my_class watches you pull and wonders… why.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This %my_role has seen chaos. Today is a new record.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This bot has two rules: 1) survive, 2) judge.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This bot observed “strategy” and found none.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This bot will remember this wipe. This bot will bring it up later.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This bot would like to congratulate you on discovering new ways to die.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This bot says: mark skull next time. Or keep donating durability.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This bot requests: less confidence, more interrupts.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This bot heard “big pull” and filed for hazard pay.', 0, 0, '', '', '', '', '', '', '', ''),

  -- Extra toxic-but-meme (still mild)
  ('suggest_something_toxic', 'I respect the attempt. I disrespect the execution.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'Your pathing is a work of abstract art.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If confusion was a buff, you’d be fully raid-buffed.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’ve seen better positioning from a boar in Durotar.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'That pull had the energy of a bad idea winning.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I am once again asking for basic mechanics.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'If you’re going to be fearless, at least be useful.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'We are not speedrunning. We are speed-accumulating shame.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'I’m not mad. I’m just… impressed in a negative way.', 0, 0, '', '', '', '', '', '', '', ''),
  ('suggest_something_toxic', 'This group has the coordination of a loot council at 2AM.', 0, 0, '', '', '', '', '', '', '', '');

COMMIT;
