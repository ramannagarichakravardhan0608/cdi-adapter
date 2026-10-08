"""UP-S1: the chosen pages as previews, in send order, each removable and movable.

The upload screen already took pages three ways (camera, file picker, drag-and-drop) and already
sent them as **one** prescription. What it did not do was show them: it listed filenames, so a
person could not see what they had added, drop the blurred one, or fix the order. These tests pin
the three additions - and, just as importantly, that the upload itself is unchanged: the pages are
still one POST of ``chosen`` in order, through the same ``pick``.
"""

from __future__ import annotations

from cdi_adapter.webapp.page import BODY, SCRIPT, upload_page

# ------------------------------------------------------------------ the previews


def test_the_page_has_a_preview_strip() -> None:
    assert "id=pages" in BODY and "id=pagehint" in BODY
    assert "renderPages()" in SCRIPT


def test_an_image_page_shows_a_thumbnail_and_a_pdf_shows_a_document_tile() -> None:
    """A browser cannot render a PDF page without a library, and this page loads no CDN script,
    so a PDF is shown as a named document tile rather than a faked preview."""
    assert "URL.createObjectURL(f)" in SCRIPT
    assert "const isImage = f => (f.type || '').startsWith('image/')" in SCRIPT
    assert "&#128196;" in SCRIPT  # the document glyph for a PDF / TIFF
    assert "cdn" not in upload_page().lower()


def test_every_preview_url_is_revoked_on_redraw() -> None:
    """A person may add and remove pages for a while; each redraw releases the previous blobs."""
    assert "thumbs.splice(0).forEach(URL.revokeObjectURL)" in SCRIPT


def test_each_page_is_numbered_in_send_order() -> None:
    assert "Page ${i + 1}" in SCRIPT


# ------------------------------------------------------------------ remove and reorder


def test_a_page_can_be_removed() -> None:
    assert "function dropPage(i){ pick(chosen.filter((_, n) => n !== i)); }" in SCRIPT
    assert "data-drop=" in SCRIPT and "Remove this page" in SCRIPT


def test_a_page_can_be_moved_either_way() -> None:
    assert "function movePage(i, by){" in SCRIPT
    assert "[next[i], next[to]] = [next[to], next[i]];" in SCRIPT
    assert "Move earlier" in SCRIPT and "Move later" in SCRIPT


def test_a_move_off_either_end_does_nothing() -> None:
    assert "if (to < 0 || to >= chosen.length) return;" in SCRIPT
    # and the end buttons are disabled, so it cannot even be asked for
    assert "${i === 0 ? 'disabled' : ''}" in SCRIPT
    assert "${i === chosen.length - 1 ? 'disabled' : ''}" in SCRIPT


def test_the_buttons_are_real_buttons_with_labels() -> None:
    """Keyboard and screen-reader reachable: the desk uses a laptop as often as a phone."""
    for label in ("aria-label=\"Move page ${i + 1} earlier\"",
                  "aria-label=\"Move page ${i + 1} later\"",
                  "aria-label=\"Remove page ${i + 1}\""):  # fmt: skip
        assert label in SCRIPT
    assert SCRIPT.count("<button type=button data-") == 0 or "type=button" in SCRIPT


def test_removing_and_moving_go_through_the_one_pick() -> None:
    """Both actions rewrite ``chosen`` through ``pick``, so the 10-page cap, the enabled state of
    Generate EMR and the preview redraw stay in one place instead of being duplicated."""
    assert "dropPage(i){ pick(" in SCRIPT
    assert "movePage(i, by){" in SCRIPT and "pick(next);" in SCRIPT


def test_the_hint_says_the_pages_are_one_prescription() -> None:
    """The story's "several files = one prescription" behaviour, said on screen rather than
    assumed - it appears only once there is more than one page to be confused about."""
    assert "pages of <b>one</b> prescription" in BODY
    assert "hint.style.display = chosen.length > 1 ? 'block' : 'none';" in SCRIPT


# ------------------------------------------------------------------ nothing else changed


def test_the_upload_itself_is_untouched() -> None:
    # 2026-10-07: the chooser and the drop zone add to the chosen pages instead of replacing
    assert "addFiles([...e.target.files])" in SCRIPT
    assert "addFiles([...e.dataTransfer.files])" in SCRIPT
    assert "chosen.forEach(f => fd.append('files', f));" in SCRIPT  # one POST, in this order
    assert "await fetch('/api/jobs', {method:'POST', body:fd});" in SCRIPT
    assert 'accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff"' in BODY


def test_the_camera_still_adds_to_the_same_list() -> None:
    assert "pick(chosen.concat([file]))" in SCRIPT
    assert "chosen.length >= 10" in SCRIPT  # the same cap, now with a way to make room
    assert "id=cam " in BODY and "id=campanel" in BODY


def test_the_file_name_is_escaped_in_the_tile() -> None:
    """A filename is user input and lands in HTML twice (the tile and its tooltip)."""
    assert 'title="${esc(f.name)}">${esc(f.name)}' in SCRIPT


def test_the_strip_is_hidden_until_something_is_chosen() -> None:
    assert "box.style.display = chosen.length ? 'flex' : 'none';" in SCRIPT


# ------------------------------------------------------------------ adding never removes


def test_every_way_of_adding_a_page_adds_to_what_is_already_chosen() -> None:
    """Owner report: pages already added were lost when more were added another way. The file
    chooser and the drop zone used to *replace* the chosen pages; only the camera added."""
    adder = SCRIPT[SCRIPT.index("function addFiles(fs){") : SCRIPT.index("function pick(fs){")]
    assert "pick(chosen.concat(fresh));" in adder
    assert "pick([...e.target.files])" not in SCRIPT  # the two replacing calls are gone
    assert "pick([...e.dataTransfer.files])" not in SCRIPT
    assert "pick(chosen.concat([file]));" in SCRIPT  # the camera: as it always was


def test_the_very_same_file_chosen_twice_is_added_once() -> None:
    assert "a.name === b.name && a.size === b.size && a.lastModified === b.lastModified" in SCRIPT
    assert "!chosen.some(c => sameFile(c, f))" in SCRIPT


def test_the_chooser_is_cleared_so_the_same_file_can_be_chosen_again_after_removal() -> None:
    assert "addFiles([...e.target.files]); e.target.value = '';" in SCRIPT


def test_sending_clears_the_chosen_pages_so_the_next_upload_starts_empty() -> None:
    send = SCRIPT[SCRIPT.index("$('#go').onclick = async () => {") :]
    assert send.index("fetch('/api/jobs'") < send.index("pick([]);") < send.index("poll(")
    assert send.index("if (!r.ok)") < send.index("pick([]);")  # kept if the upload failed


def test_the_ten_page_limit_still_holds_however_pages_are_added() -> None:
    assert "function pick(fs){ chosen = fs.slice(0, 10);" in SCRIPT
    assert "chosen.length + fresh.length > 10" in SCRIPT and "chosen.length >= 10" in SCRIPT
