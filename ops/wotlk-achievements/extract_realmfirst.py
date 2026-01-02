#!/usr/bin/env python3
from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Wdbc:
    record_count: int
    field_count: int
    record_size: int
    string_size: int
    records: bytes
    strings: bytes

    @staticmethod
    def load(path: Path) -> "Wdbc":
        data = path.read_bytes()
        if data[:4] != b"WDBC":
            raise ValueError(f"{path} is not a WDBC file")
        record_count, field_count, record_size, string_size = struct.unpack_from("<4I", data, 4)
        header_size = 20
        records_size = record_count * record_size
        records = data[header_size : header_size + records_size]
        strings = data[header_size + records_size : header_size + records_size + string_size]
        return Wdbc(
            record_count=record_count,
            field_count=field_count,
            record_size=record_size,
            string_size=string_size,
            records=records,
            strings=strings,
        )

    def get_string(self, offset: int) -> str | None:
        if offset < 0 or offset >= self.string_size:
            return None
        end = self.strings.find(b"\x00", offset)
        if end == -1:
            return None
        return self.strings[offset:end].decode("utf-8", errors="replace")

    def iter_records(self):
        for i in range(self.record_count):
            rec = self.records[i * self.record_size : (i + 1) * self.record_size]
            fields = struct.unpack_from("<" + ("I" * self.field_count), rec, 0)
            yield fields


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract realm-first achievements from Achievement.dbc")
    ap.add_argument("--achievement-dbc", type=Path, required=True, help="Path to Achievement.dbc (WDBC)")
    ap.add_argument("--contains", default="Realm First", help="Substring to search for (default: 'Realm First')")
    ap.add_argument("--format", choices=["tsv", "sql-in-list"], default="tsv")
    args = ap.parse_args()

    dbc = Wdbc.load(args.achievement_dbc)

    hits: list[tuple[int, str]] = []
    for fields in dbc.iter_records():
        achievement_id = int(fields[0])
        found: str | None = None
        for v in fields:
            s = dbc.get_string(int(v))
            if s and args.contains in s:
                found = s
                break
        if found:
            hits.append((achievement_id, found))

    hits.sort(key=lambda x: x[0])

    if args.format == "tsv":
        for achievement_id, title in hits:
            print(f"{achievement_id}\t{title}")
    else:
        ids = ", ".join(str(i) for i, _ in hits)
        print(f"({ids})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

