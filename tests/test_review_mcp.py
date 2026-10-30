import pytest

from agent_control.review_mcp import ReviewReader


def test_reader_limits_paths_and_has_no_execution_or_write_tool(tmp_path):
    root = tmp_path / "review"
    root.mkdir()
    (root / "app.py").write_text("first\nsecond\nthird\n")
    secret = tmp_path / "private.txt"
    secret.write_text("synthetic secret")
    (root / "escape.py").symlink_to(secret)
    reader = ReviewReader({"roots": {"repo": str(root)}, "facts": {}})
    assert (
        reader.call(
            "read_file", {"repository": "repo", "path": "app.py", "start_line": 2, "line_count": 1}
        )
        == "2: second"
    )
    for path in [
        "../private.txt",
        "escape.py",
        ".local/secrets.json",
        ".git/config",
        ".env",
        "key.pem",
    ]:
        with pytest.raises(ValueError):
            reader.call("read_file", {"repository": "repo", "path": path})
    with pytest.raises(ValueError):
        reader.call("read_file", {"repository": "other", "path": "app.py"})
    assert all(t["annotations"]["readOnlyHint"] for t in reader.tools())
    assert {t["name"] for t in reader.tools()} == {
        "review_context",
        "list_files",
        "read_file",
        "git_diff",
        "git_show",
    }
