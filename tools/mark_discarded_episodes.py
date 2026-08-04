#!/usr/bin/env python3
"""Mark LeRobot/SONIC dataset episodes as discarded.

This is a small convenience wrapper around the official post-processing flow:
``process_dataset.py`` removes episodes listed in ``meta/info.json`` under
``discarded_episode_indices``.  This script edits that field safely after manual
video review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


def parse_episode_list(text: str) -> list[int]:
    """Parse comma/space separated episode ids and simple ranges.

    Examples:
      "1,3,8"
      "1 3 8"
      "1-4,8,10"
    """
    result: set[int] = set()
    for raw_part in text.replace(",", " ").split():
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            if end < start:
                raise ValueError(f"Invalid descending range: {part}")
            result.update(range(start, end + 1))
        else:
            result.add(int(part))
    return sorted(result)


def load_episode_indices(dataset_path: Path) -> set[int]:
    episodes_path = dataset_path / "meta" / "episodes.jsonl"
    if not episodes_path.exists():
        raise FileNotFoundError(f"Missing {episodes_path}")
    indices: set[int] = set()
    with episodes_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            indices.add(int(item["episode_index"]))
    return indices


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mark or unmark discarded episodes in a SONIC/LeRobot dataset."
    )
    parser.add_argument(
        "dataset_path",
        type=Path,
        help="Dataset directory, e.g. outputs/2026-07-31-22-07-03",
    )
    parser.add_argument(
        "--bad",
        default="",
        help="Episodes to mark as discarded, e.g. '1,3,8' or '1-4,8'.",
    )
    parser.add_argument(
        "--good",
        default="",
        help="Episodes to remove from discarded list, e.g. '3,8'.",
    )
    parser.add_argument(
        "--set",
        dest="replace",
        action="store_true",
        help="Replace discarded list with --bad instead of adding to existing list.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear discarded_episode_indices.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Only show current discarded_episode_indices.",
    )
    args = parser.parse_args()

    dataset_path = args.dataset_path
    info_path = dataset_path / "meta" / "info.json"
    if not info_path.exists():
        raise FileNotFoundError(f"Missing {info_path}")

    info = json.loads(info_path.read_text(encoding="utf-8"))
    current = set(int(x) for x in info.get("discarded_episode_indices", []))

    if args.show:
        print(f"{dataset_path}: discarded_episode_indices = {sorted(current)}")
        return 0

    valid_indices = load_episode_indices(dataset_path)
    bad = set(parse_episode_list(args.bad)) if args.bad else set()
    good = set(parse_episode_list(args.good)) if args.good else set()
    requested = bad | good
    invalid = sorted(requested - valid_indices)
    if invalid:
        raise ValueError(
            f"Episode id(s) not present in {dataset_path}: {invalid}. "
            f"Valid range: {min(valid_indices)}..{max(valid_indices)}"
        )

    if args.clear:
        updated: set[int] = set()
    elif args.replace:
        updated = set(bad)
    else:
        updated = set(current)
        updated.update(bad)
        updated.difference_update(good)

    backup_path = info_path.with_suffix(".json.bak")
    if not backup_path.exists():
        shutil.copy2(info_path, backup_path)

    info["discarded_episode_indices"] = sorted(updated)
    info_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    print(f"{dataset_path}:")
    print(f"  previous: {sorted(current)}")
    print(f"  updated:  {sorted(updated)}")
    print(f"  backup:   {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
