from html.parser import HTMLParser

import pytest

from agent_control.markdown import instructions_html


class Elements(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.elements = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_instructions_render_structured_markdown():
    source = """# Development

## Workflow

1. **Deploy** the feature.
2. Run `pytest`.

- Verify [the task](https://example.com/task).

> Preserve existing work.

| Step | Result |
| --- | --- |
| Test | Passed |

```text
<untrusted example>
```
"""
    rendered = instructions_html(source)
    tags = [tag for tag, _ in Elements(rendered).elements]
    assert {"h1", "h2", "ol", "ul", "li", "strong", "code", "blockquote", "table"} <= set(tags)
    assert '<a href="https://example.com/task">the task</a>' in rendered
    assert "&lt;untrusted example&gt;" in rendered


@pytest.mark.parametrize(
    "source",
    [
        '<script>alert("unsafe")</script>',
        '<img src=x onerror="alert(1)">',
        '<svg onload="alert(1)"></svg>',
        '<iframe src="https://example.com"></iframe>',
        "[run](javascript:alert(1))",
        "[run](jav&#x61;script:alert(1))",
        "[run](vbscript:msgbox(1))",
        "[file](file:///etc/passwd)",
        "[run](data:text/html;base64,PHNjcmlwdD4=)",
        "![tracking](https://example.com/track.png)",
        "![inline](data:image/png;base64,aGVsbG8=)",
    ],
)
def test_instructions_cannot_create_active_html_or_images(source):
    for tag, attrs in Elements(instructions_html(source)).elements:
        assert tag not in {"script", "img", "svg", "iframe", "object", "embed", "style"}
        assert not any(name.startswith("on") for name in attrs)
        assert (
            not attrs.get("href", "")
            .lower()
            .startswith(("javascript:", "vbscript:", "file:", "data:"))
        )


def test_display_keeps_original_markdown_and_profile_fields(world):
    app, client, headers, *_ = world
    original = app.state.store.agent("development").model_dump()
    source = "# Role\n\n- **Verify** the feature.\n\nAgent Control reporting: synthetic footer"
    updated = {**original, "instructions": source}
    response = client.put("/api/agents/development", json=updated, headers=headers)
    assert response.status_code == 200
    state = client.get("/api/state", headers=headers).json()
    profile = next(a for a in state["agents"] if a["id"] == "development")
    assert profile == updated
    assert app.state.store.agent("development").instructions == source
    assert "<h1>Role</h1>" in state["agent_instruction_html"]["development"]
    assert "<strong>Verify</strong>" in state["agent_instruction_html"]["development"]
    assert "synthetic footer" not in state["agent_instruction_html"]["development"]


def test_empty_instructions_have_a_readable_placeholder():
    assert "No instructions configured." in instructions_html("")
