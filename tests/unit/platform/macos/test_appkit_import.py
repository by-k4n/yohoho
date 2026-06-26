def test_appkit_module_imports_without_native():
    # Importing the seam module must NOT import pyobjc at module load (safe anywhere).
    import yohoho.platform.macos._appkit as ak
    assert hasattr(ak, "input_monitoring_state")
    assert hasattr(ak, "accessibility_trusted")
    assert hasattr(ak, "frontmost_bundle_id")
    assert hasattr(ak, "pasteboard_get")
    assert hasattr(ak, "pasteboard_set")
    assert hasattr(ak, "input_monitoring_request")
    assert hasattr(ak, "pasteboard_has_nontext")
    assert hasattr(ak, "open_url")
