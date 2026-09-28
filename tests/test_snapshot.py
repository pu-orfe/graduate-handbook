import hashlib
from bs4 import BeautifulSoup
from handbook_generator.snapshot import build_snapshot, snapshot_differs, write_snapshot

BODY = """
<div class="tex2jax_process">
  <p>Welcome   to the
     Department.</p>
  <h2>Ph.D. Program Requirements</h2>
  <h3>Core courses</h3>
  <ul>
    <li>ORF 522 Optimization<ul><li>Nested detail</li></ul></li>
  </ul>
  <section class="cke-callout">
    <div class="cke-callout-content"><p>Must be approved 2 weeks prior.</p></div>
  </section>
  <p>   </p>
</div>
"""


def _data(tmp_path, chair_bytes=b"chair"):
    chair = tmp_path / "chair.jpeg"
    chair.write_bytes(chair_bytes)
    return {
        "chair_name": "Test Chair",
        "dgs_name": "Test DGS",
        "chair_img_path": str(chair),
        "dgs_img_path": None,
    }


def _body():
    return BeautifulSoup(BODY, "html.parser")


def test_snapshot_lines(tmp_path):
    lines = build_snapshot(_data(tmp_path), _body(), "2026").splitlines()

    assert "year: 2026" in lines
    assert "chair: Test Chair" in lines
    assert f"chair_image_sha256: {hashlib.sha256(b'chair').hexdigest()}" in lines
    assert "dgs_image_sha256: missing" in lines
    # Whitespace collapses so reflowed HTML does not register as a change.
    assert "Welcome to the Department." in lines
    assert "## Ph.D. Program Requirements" in lines
    assert "### Core courses" in lines
    assert "- ORF 522 Optimization" in lines
    assert "- Nested detail" in lines
    assert "> Must be approved 2 weeks prior." in lines
    # Blank blocks are dropped rather than written as empty lines.
    body_lines = lines[lines.index("dgs_image_sha256: missing") + 2:]
    assert all(line.strip() for line in body_lines)


def test_snapshot_is_deterministic(tmp_path):
    assert build_snapshot(_data(tmp_path), _body(), "2026") == build_snapshot(_data(tmp_path), _body(), "2026")


def test_snapshot_reflects_every_input(tmp_path):
    base = build_snapshot(_data(tmp_path), _body(), "2026")

    assert build_snapshot(_data(tmp_path), _body(), "2027") != base
    assert build_snapshot(_data(tmp_path, chair_bytes=b"new photo"), _body(), "2026") != base
    renamed = dict(_data(tmp_path), dgs_name="Someone Else")
    assert build_snapshot(renamed, _body(), "2026") != base
    edited = BeautifulSoup(BODY.replace("2 weeks", "3 weeks"), "html.parser")
    assert build_snapshot(_data(tmp_path), edited, "2026") != base


def test_snapshot_differs_and_write(tmp_path):
    path = tmp_path / "nested" / "handbook.md"

    assert snapshot_differs(str(path), "a\n") is True
    write_snapshot(str(path), "a\n")
    assert path.read_text() == "a\n"
    assert snapshot_differs(str(path), "a\n") is False
    assert snapshot_differs(str(path), "b\n") is True
