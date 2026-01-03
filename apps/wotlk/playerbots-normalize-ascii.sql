-- This file is mounted into the wotlk playerbots normalize job.
--
-- Keep this in sync with ops/wotlk-playerbots/normalize-ascii.sql if you edit it.

USE acore_playerbots;

START TRANSACTION;

-- Replace common Unicode punctuation that often shows as mojibake in the WoW client.
UPDATE ai_playerbot_texts
SET text =
  REPLACE(
    REPLACE(
      REPLACE(
        REPLACE(
          REPLACE(
            REPLACE(
              REPLACE(text,
                '’', ''''
              ),
              '‘', ''''
            ),
            '“', '\"'
          ),
          '”', '\"'
        ),
        '…', '...'
      ),
      '—', '-'
    ),
    '–', '-'
  )
WHERE
  name LIKE 'broadcast_%'
  OR name IN (
    'hello', 'goodbye',
    'low mana', 'low health', 'critical health',
    'aoe', 'loot', 'taunt',
    'suggest_something', 'suggest_something_toxic',
    'suggest_trade', 'suggest_sell', 'suggest_quest', 'suggest_instance', 'suggest_faction'
  );

COMMIT;

