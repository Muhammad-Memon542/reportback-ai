"""Map 'Assigned to' strings (official titles or division names) to canonical divisions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

DEFAULT_YAML = Path(__file__).with_name("divisions.yaml")


def normalize(text: str) -> str:
    s = text.lower().replace("&", " and ").replace("’", "'").replace("‘", "'")
    s = re.sub(r"[–—-]", " ", s)  # hyphens and en/em dashes ('Long-Term', 'Manager - Community')
    s = re.sub(r"[^a-z0-9' ]+", " ", s)  # commas, periods, parentheses...
    return " ".join(s.split())


@dataclass(frozen=True)
class Division:
    name: str
    aliases: tuple[str, ...]
    successors: tuple[str, ...] = ()


@dataclass
class DivisionMapper:
    divisions: list[Division]
    ignore: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        names = {d.name for d in self.divisions}
        for d in self.divisions:
            missing = set(d.successors) - names
            if missing:
                raise ValueError(f"{d.name}: unknown successors {sorted(missing)}")
        pairs: list[tuple[str, str | None]] = [
            (normalize(a), d.name) for d in self.divisions for a in d.aliases
        ]
        pairs += [(normalize(a), None) for a in self.ignore]
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        self._patterns = [
            (re.compile(rf"(?<![a-z0-9']){re.escape(alias)}(?![a-z0-9])"), name) for alias, name in pairs
        ]
        self._by_name = {d.name: d for d in self.divisions}

    @classmethod
    def load(cls, path: Path = DEFAULT_YAML) -> DivisionMapper:
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        divs = [
            Division(
                name=str(d["name"]),
                aliases=tuple(str(a) for a in d["aliases"]),
                successors=tuple(str(s) for s in d.get("successors", [])),
            )
            for d in data["divisions"]
        ]
        return cls(divs, [str(i) for i in data.get("ignore", [])])

    def match(self, text: str) -> list[str]:
        """Canonical divisions named in `text` (no successors), in order of first appearance."""
        s = f" {normalize(text)} "
        found: list[tuple[int, str]] = []
        for pat, name in self._patterns:
            while (m := pat.search(s)) is not None:
                if name is not None:
                    found.append((m.start(), name))
                s = s[: m.start()] + " " * (m.end() - m.start()) + s[m.end() :]
        out: list[str] = []
        for _, name in sorted(found):
            if name not in out:
                out.append(name)
        return out

    def map(self, assignees: list[str]) -> list[str]:
        """Divisions for a list of assignees, including successors of renamed divisions."""
        out: list[str] = []
        for a in assignees:
            for name in self.match(a):
                for n in (name, *self._by_name[name].successors):
                    if n not in out:
                        out.append(n)
        return out


def split_assignees(raw: object) -> list[str]:
    """'Transportation Services - Toronto Police' -> two assignees. Titles keep their commas."""
    if raw is None:
        return []
    parts = re.split(r"\n+|\s+-\s+|;", str(raw))
    out = []
    for p in parts:
        p = " ".join(p.split()).strip(" ,.")
        if p and p not in out:
            out.append(p)
    return out


@lru_cache(maxsize=1)
def default_mapper() -> DivisionMapper:
    return DivisionMapper.load()
