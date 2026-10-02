import json
import os

import pytest

from double_diamond.store import Store
from double_diamond.tools import MAX_FILE_BYTES, LocalTools


# ---- store ------------------------------------------------------------------


def test_preference_becomes_standing_after_two_consistent_answers(tmp_path):
    s = Store(tmp_path)
    s.record_answer("database", "postgres")
    assert s.standing("database") is None
    s.record_answer("database", "Postgres")  # case-insensitive agreement
    assert s.standing("database") == "Postgres"


def test_a_contradicting_answer_breaks_the_standing_preference(tmp_path):
    s = Store(tmp_path)
    for a in ("postgres", "postgres", "sqlite"):
        s.record_answer("database", a)
    assert s.standing("database") is None


def test_blank_answers_and_keys_are_ignored(tmp_path):
    s = Store(tmp_path)
    s.record_answer("", "x")
    s.record_answer("k", "   ")
    assert s.standing_summary() == {}


def test_forget(tmp_path):
    s = Store(tmp_path)
    s.record_answer("k", "v")
    s.record_answer("k", "v")
    assert s.forget("k") and s.standing("k") is None
    assert not s.forget("k")


def test_corrupt_preferences_file_is_tolerated(tmp_path):
    s = Store(tmp_path)
    s.prefs_path.parent.mkdir(parents=True, exist_ok=True)
    s.prefs_path.write_text("{not json", encoding="utf-8")
    assert s.standing("anything") is None
    s.record_answer("k", "v")  # recovers by rewriting
    assert json.loads(s.prefs_path.read_text(encoding="utf-8")) == {"k": ["v"]}


def test_state_dir_comes_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DD_STATE_DIR", str(tmp_path / "x"))
    assert Store().root == tmp_path / "x"


def test_stats(tmp_path):
    s = Store(tmp_path)
    s.log("run", run_id="1", skipped=True)
    s.log("run", run_id="2", skipped=False)
    s.log("question", run_id="2", outcome="accepted_default", auto=False)
    s.log("question", run_id="2", outcome="you_decide", auto=False)
    s.log("question", run_id="2", outcome="overridden", auto=False)
    s.log("question", run_id="2", outcome="accepted_default", auto=True)  # auto answers are not counted
    s.log("flip", run_id="2", id="x")
    st = s.stats()
    assert st["runs"] == 2 and st["skipped_by_triage"] == 1
    assert st["questions_asked"] == 3 and st["go_rate"] == pytest.approx(0.667, abs=1e-3)
    assert st["override_rate"] == pytest.approx(0.5)  # (1 override + 1 flip) / (3 questions + 1 flip)
    assert st["flips_after_plan"] == 1 and st["questions_per_pipeline_run"] == 3.0


def test_runs_roundtrip_and_missing_run(tmp_path):
    s = Store(tmp_path)
    s.save_run("r1", {"a": 1})
    assert s.load_run("r1") == {"a": 1} and s.list_runs() == ["r1"]
    with pytest.raises(KeyError):
        s.load_run("nope")


# ---- local tools --------------------------------------------------------------


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path / "root"
    (root / "src").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret-in-git", encoding="utf-8")
    (root / "README.md").write_text("hello world\nsecond line TODO", encoding="utf-8")
    (root / "src" / "a.py").write_text("x = 1  # TODO fix\n", encoding="utf-8")
    (root / "bin.dat").write_bytes(b"\x00\x01\x02binary")
    (tmp_path / "outside.txt").write_text("TOP SECRET", encoding="utf-8")
    return LocalTools(root)


def test_read_and_list(sandbox):
    assert "hello world" in sandbox.read_file("README.md")
    listing = sandbox.list_dir(".")
    assert "src/" in listing and "README.md" in listing and ".git" not in listing


@pytest.mark.parametrize("path", ["../outside.txt", "src/../../outside.txt", "/etc/passwd", "C:\\Windows\\win.ini"])
def test_escape_attempts_are_refused(sandbox, path):
    text, is_error = sandbox.run("read_file", {"path": path})
    assert is_error and "TOP SECRET" not in text


def test_symlink_escape_is_refused(sandbox, tmp_path):
    link = sandbox.root / "link.txt"
    try:
        os.symlink(tmp_path / "outside.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform or account")
    text, is_error = sandbox.run("read_file", {"path": "link.txt"})
    assert is_error and "TOP SECRET" not in text


def test_binary_and_large_files(sandbox):
    assert sandbox.run("read_file", {"path": "bin.dat"}) == ("binary file skipped", True)
    (sandbox.root / "big.txt").write_text("a" * (MAX_FILE_BYTES + 500), encoding="utf-8")
    out = sandbox.read_file("big.txt")
    assert out.endswith("[truncated]") and len(out) < MAX_FILE_BYTES + 50


def test_search_finds_matches_and_skips_git_and_binaries(sandbox):
    out = sandbox.search("TODO", ".")
    assert "README.md:2" in out and "src/a.py:1" in out
    assert "secret" not in sandbox.search("secret", ".")
    assert sandbox.search("nomatch_zzz", ".") == "[no matches]"


def test_tool_errors_never_raise(sandbox):
    assert sandbox.run("search", {"pattern": "(", "path": "."})[1]
    assert sandbox.run("read_file", {})[1]
    assert sandbox.run("delete_everything", {"path": "."})[1]
    assert sandbox.run("list_dir", {"path": "README.md"})[1]


def test_missing_root_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        LocalTools(tmp_path / "nope")


def test_tool_definitions_are_read_only_and_strict_objects(sandbox):
    names = {d["name"] for d in sandbox.definitions()}
    assert names == {"read_file", "list_dir", "search"}
    for d in sandbox.definitions():
        assert d["input_schema"]["additionalProperties"] is False
