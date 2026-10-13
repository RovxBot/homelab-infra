#!/usr/bin/env python3
"""Restore cooking recipe reagents from the pinned AzerothCore v20.0 data bundle."""

import argparse
import hashlib
import json
from pathlib import Path
import struct


REFERENCE_HASHES = {
    "Spell.dbc": "d5cce1a83550dcfa9eb2f0251dbb11fd24c272534b2b1a9b230924a44d817ab3",
    "SkillLineAbility.dbc": "4154b833d6a26b9b9ce53851d56cb594f0813c0936a72ec89f933cc69abe42c3",
}
REFERENCE_URL = "https://github.com/wowgaming/client-data/releases/download/v20.0/Data.zip"
COOKING_SKILL = 185
CREATE_ITEM_EFFECT = 24
REAGENT_START = 52
REAGENT_END = 68


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Dbc:
    def __init__(self, path, fields, expected_hash=None):
        self.data = Path(path).read_bytes()
        if expected_hash and digest(self.data) != expected_hash:
            raise ValueError(f"SHA256 mismatch for {path}")
        if len(self.data) < 20:
            raise ValueError(f"Truncated DBC header: {path}")
        magic, count, actual_fields, size, strings = struct.unpack_from("<4s4I", self.data)
        if magic != b"WDBC" or actual_fields != fields or size != fields * 4:
            raise ValueError(f"Unexpected DBC layout: {path}")
        self.string_offset = 20 + count * size
        if len(self.data) != self.string_offset + strings:
            raise ValueError(f"DBC size does not match header: {path}")
        self.rows = {}
        self.offsets = {}
        for index in range(count):
            offset = 20 + index * size
            row = struct.unpack_from(f"<{fields}I", self.data, offset)
            if row[0] in self.rows:
                raise ValueError(f"Duplicate DBC ID {row[0]}: {path}")
            self.rows[row[0]] = row
            self.offsets[row[0]] = offset

    def spell_name(self, spell_id):
        position = self.string_offset + self.rows[spell_id][136]
        if not self.string_offset <= position < len(self.data):
            raise ValueError(f"Invalid name offset for spell {spell_id}")
        end = self.data.index(b"\0", position)
        return self.data[position:end].decode("utf-8")


def reagent_list(row):
    return [
        {"item": item, "count": count}
        for item, count in zip(row[52:60], row[60:68])
        if item and count
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Current server Spell.dbc")
    parser.add_argument("--reference-dir", type=Path, required=True, help="Extracted reference DBC directory")
    parser.add_argument("--expected-input-sha256", help="Abort if the source has changed")
    parser.add_argument("--output", type=Path, help="New file; omitted for a dry run")
    parser.add_argument("--report", type=Path, help="Write the reagent changes and file hashes as JSON")
    args = parser.parse_args()
    if args.output and args.output.resolve() == args.input.resolve():
        parser.error("Use a separate output file; deploy it after backing up the original")

    source = Dbc(args.input, 234, args.expected_input_sha256)
    reference = Dbc(args.reference_dir / "Spell.dbc", 234, REFERENCE_HASHES["Spell.dbc"])
    skills = Dbc(args.reference_dir / "SkillLineAbility.dbc", 14, REFERENCE_HASHES["SkillLineAbility.dbc"])
    cooking_ids = {row[2] for row in skills.rows.values() if row[1] == COOKING_SKILL}
    recipe_ids = {
        spell_id for spell_id in cooking_ids
        if CREATE_ITEM_EFFECT in reference.rows[spell_id][71:74]
    }

    patched = bytearray(source.data)
    changes = []
    for spell_id in sorted(recipe_ids):
        if spell_id not in source.rows:
            raise ValueError(f"Cooking spell {spell_id} is missing from the server DBC")
        old = source.rows[spell_id]
        new = reference.rows[spell_id]
        if old[REAGENT_START:REAGENT_END] == new[REAGENT_START:REAGENT_END]:
            continue
        start = source.offsets[spell_id] + REAGENT_START * 4
        struct.pack_into("<16I", patched, start, *new[REAGENT_START:REAGENT_END])
        changes.append({
            "spell_id": spell_id,
            "name": reference.spell_name(spell_id),
            "before": reagent_list(old),
            "after": reagent_list(new),
        })

    # Validate every recipe and preserve all other data, including with python -O.
    for spell_id, old in source.rows.items():
        offset = source.offsets[spell_id]
        new = struct.unpack_from("<234I", patched, offset)
        if spell_id in recipe_ids:
            if new[52:68] != reference.rows[spell_id][52:68]:
                raise ValueError(f"Reagent verification failed for spell {spell_id}")
            if new[:52] != old[:52] or new[68:] != old[68:]:
                raise ValueError(f"Unexpected changes outside spell {spell_id}'s reagents")
        elif new != old:
            raise ValueError(f"Unexpected change to non-recipe spell {spell_id}")
    if patched[:20] != source.data[:20] or patched[source.string_offset:] != source.data[source.string_offset:]:
        raise ValueError("Unexpected change to the DBC header or strings")

    report = {
        "reference_url": REFERENCE_URL,
        "reference_sha256": REFERENCE_HASHES,
        "input_sha256": digest(source.data),
        "output_sha256": digest(patched),
        "checked_recipes": len(recipe_ids),
        "changed_recipes": len(changes),
        "changes": changes,
    }
    if args.output:
        with args.output.open("xb") as output:
            output.write(patched)
    if args.report:
        with args.report.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
    print(f"Checked {len(recipe_ids)} cooking recipes; corrected {len(changes)} reagent lists.")
    print(f"Input SHA256:  {report['input_sha256']}")
    print(f"Output SHA256: {report['output_sha256']}")
    if not args.output:
        print("Dry run: no DBC file written.")


if __name__ == "__main__":
    main()
