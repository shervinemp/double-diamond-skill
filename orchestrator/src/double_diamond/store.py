"""Persistent state: standing preferences, an event log for telemetry, and run records.

Layout under the state directory (default ``~/.double-diamond``, or ``DD_STATE_DIR``)::

    preferences.json   topic_key -> history of explicit answers
    events.jsonl       one JSON event per line
    runs/<run_id>.json full record of each run (brief, audit trail, plan)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

HISTORY_LIMIT = 20


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class Store:
    def __init__(self, root: str | Path | None = None):
        chosen = root or os.environ.get("DD_STATE_DIR") or (Path.home() / ".double-diamond")
        self.root = Path(chosen)
        self.prefs_path = self.root / "preferences.json"
        self.events_path = self.root / "events.jsonl"
        self.runs_dir = self.root / "runs"

    # -- standing preferences ----------------------------------------------

    def _prefs(self) -> dict[str, list[str]]:
        try:
            data = json.loads(self.prefs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {k: [str(x) for x in v] for k, v in data.items() if isinstance(v, list)}

    def record_answer(self, topic_key: str, answer: str) -> None:
        """Remember an explicit answer. Accepting a default is not an answer."""
        if not topic_key or not answer.strip():
            return
        prefs = self._prefs()
        history = prefs.setdefault(topic_key, [])
        history.append(answer.strip())
        prefs[topic_key] = history[-HISTORY_LIMIT:]
        _atomic_write(self.prefs_path, json.dumps(prefs, indent=2, sort_keys=True))

    def standing(self, topic_key: str) -> str | None:
        """A preference is standing once the last two explicit answers agree."""
        history = self._prefs().get(topic_key, [])
        if len(history) >= 2 and history[-1].lower() == history[-2].lower():
            return history[-1]
        return None

    def forget(self, topic_key: str) -> bool:
        prefs = self._prefs()
        if topic_key not in prefs:
            return False
        del prefs[topic_key]
        _atomic_write(self.prefs_path, json.dumps(prefs, indent=2, sort_keys=True))
        return True

    def standing_summary(self) -> dict[str, str]:
        return {k: v for k in self._prefs() if (v := self.standing(k)) is not None}

    # -- events and stats ---------------------------------------------------

    def log(self, kind: str, **fields: Any) -> None:
        event = {"ts": round(time.time(), 3), "kind": kind, **fields}
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    def events(self) -> list[dict[str, Any]]:
        try:
            lines = self.events_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def stats(self) -> dict[str, Any]:
        """The tuning signals: how often triage skips, how many questions were
        asked, how often "go" was the answer, and how often defaults were overridden."""
        events = self.events()
        runs = [e for e in events if e["kind"] == "run"]
        questions = [e for e in events if e["kind"] == "question" and not e.get("auto")]
        flips = [e for e in events if e["kind"] == "flip"]
        accepted = [q for q in questions if q["outcome"] in ("accepted_default", "you_decide")]
        overridden = [q for q in questions if q["outcome"] == "overridden"]

        def rate(n: int, d: int) -> float | None:
            return round(n / d, 3) if d else None

        pipeline_runs = [r for r in runs if not r.get("skipped")]
        return {
            "runs": len(runs),
            "skipped_by_triage": len([r for r in runs if r.get("skipped")]),
            "questions_asked": len(questions),
            "questions_per_pipeline_run": rate(len(questions), len(pipeline_runs)),
            "go_rate": rate(len(accepted), len(questions)),
            "override_rate": rate(len(overridden) + len(flips), len(questions) + len(flips)),
            "flips_after_plan": len(flips),
            "standing_preferences": len(self.standing_summary()),
        }

    # -- runs ---------------------------------------------------------------

    def save_run(self, run_id: str, state: dict[str, Any]) -> Path:
        path = self.runs_dir / f"{run_id}.json"
        _atomic_write(path, json.dumps(state, indent=2, default=str))
        return path

    def load_run(self, run_id: str) -> dict[str, Any]:
        path = self.runs_dir / f"{run_id}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except OSError as e:
            raise KeyError(f"no such run: {run_id}") from e

    def list_runs(self) -> list[str]:
        if not self.runs_dir.is_dir():
            return []
        return sorted(p.stem for p in self.runs_dir.glob("*.json"))
