"""The Generate EMR page's camera option (owner request 2026-09-29): take a photo or upload a file.
A captured photo is added to the same list of chosen scans and goes through the unchanged upload."""

from __future__ import annotations

from cdi_adapter.webapp.page import BODY, SCRIPT, upload_page


def test_the_page_offers_camera_and_upload():
    assert "id=cam " in BODY and "Take photo" in BODY
    assert "id=browse " in BODY and "Upload files" in BODY
    # phones without a live camera API use their own camera app through this input
    assert 'id=camfile type=file accept="image/*" capture=environment' in BODY
    assert "id=campanel" in BODY and "id=camvideo" in BODY and "playsinline" in BODY


def test_a_photo_joins_the_chosen_scans_through_the_existing_pick():
    assert "pick(chosen.concat([file]))" in SCRIPT
    assert "camera-${stamp()}.jpg" in SCRIPT and "'image/jpeg', 0.92" in SCRIPT
    assert "chosen.length >= 10" in SCRIPT  # the same 10-scan limit as the drop zone
    assert "blob instanceof File ? blob" in SCRIPT  # a phone camera-app file is kept as it is


def test_the_live_camera_is_released_and_falls_back():
    assert "getUserMedia" in SCRIPT and "facingMode: {ideal: 'environment'}" in SCRIPT
    assert "window.isSecureContext" in SCRIPT and "$('#camfile').click()" in SCRIPT
    assert "getTracks().forEach(t => t.stop())" in SCRIPT


def test_the_existing_upload_and_processing_are_unchanged():
    # the drop zone and the file chooser now ADD to the chosen pages; the upload call is as
    # before
    drop = "$('#drop').ondrop = e => { e.preventDefault(); addFiles([...e.dataTransfer.files]); };"
    assert drop in SCRIPT
    files = "$('#files').onchange = e => { addFiles([...e.target.files]); e.target.value = ''; };"
    assert files in SCRIPT
    assert "chosen.forEach(f => fd.append('files', f));" in SCRIPT
    assert "await fetch('/api/jobs', {method:'POST', body:fd});" in SCRIPT
    assert 'accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff"' in BODY
    assert "cdn" not in upload_page().lower()  # still no external scripts


def test_the_camera_is_opened_only_by_a_press_never_when_the_page_loads():
    """Owner request 2026-10-07: the camera is used only between a press of "Take photo" and
    the picture being taken."""
    assert SCRIPT.count("getUserMedia({") == 1  # one place opens it ...
    opener = SCRIPT[SCRIPT.index("async function camStart(){") : SCRIPT.index("$('#cam').onclick")]
    assert "getUserMedia({" in opener  # ... and it is inside the function a press calls
    assert "$('#cam').onclick = camStart;" in SCRIPT
    assert "$('#camretake').onclick = camStart;" in SCRIPT
    # nothing calls it on load: no bare call, no load / DOMContentLoaded hook
    assert SCRIPT.count("camStart") == 3  # its definition and the two buttons: no other call
    assert "DOMContentLoaded" not in SCRIPT and "window.onload" not in SCRIPT
    assert "enumerateDevices" not in SCRIPT and "permissions.query" not in SCRIPT


def test_the_camera_goes_off_as_soon_as_the_picture_is_taken():
    snap = SCRIPT[SCRIPT.index("$('#camsnap').onclick") : SCRIPT.index("$('#camretake').onclick")]
    assert "camStop();" in snap and snap.index("cam.blob = b") < snap.index("camStop();")
    assert "$('#camvideo').srcObject = null;" in SCRIPT  # the video lets go of the stream too


def test_the_camera_goes_off_when_the_scans_are_sent_or_the_page_is_left():
    send = SCRIPT[SCRIPT.index("$('#go').onclick = async () => {") :]
    assert send.index("camStop(); camShow(false);") < send.index("fetch('/api/jobs'")
    assert "window.addEventListener('pagehide', camStop);" in SCRIPT
    assert "if (document.hidden) camStop();" in SCRIPT
    assert "$('#camclose').onclick = () => { camStop(); camShow(false); };" in SCRIPT
    assert "$('#camuse').onclick = () => { camStop();" in SCRIPT
