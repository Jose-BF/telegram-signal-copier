"""Independent public-route regressions for frozen capture identity types."""

from datetime import timedelta

import pytest

from tests.test_collect_simulator_window import explicit_bridge_fixture
from tools import prepare_simulator_forward as forward
from tools import run_protection_controls as controls
from tools import run_simulator_forward as runner


@pytest.mark.parametrize(
    "binding_key,manifest_group,manifest_key",
    [
        ("expected_account_identity_sha256", "account", "account_identity_sha256"),
        ("expected_live_commit", "live_code", "commit"),
        ("wrapper_sha256", "live_code", "wrapper_sha256"),
    ],
)
@pytest.mark.parametrize("invalid", [None, "", False, 23, "not-a-hash"])
def test_public_capture_rejects_untyped_frozen_identity(
    tmp_path, monkeypatch, binding_key, manifest_group, manifest_key, invalid
):
    fixture = explicit_bridge_fixture(tmp_path, monkeypatch)
    profile = controls.read(tmp_path / "profile.json")
    profile["capture_contract"][binding_key] = invalid
    profile_path = tmp_path / "invalid-profile.json"
    controls.save(profile_path, profile)
    frozen = tmp_path / "invalid-freeze"
    output = tmp_path / "invalid-dataset"

    # Freeze the invalid value through the public API, never tamper with an
    # already-frozen protocol or bypass its fingerprint/source validation.
    with pytest.raises(ValueError):
        report = forward.prepare(
            profile_path, out=frozen, start_utc=fixture.start, end_utc=fixture.end
        )
        assert report["status"] == "frozen_waiting_for_cohort"
        protocol_path = frozen / "protocol.json"
        protocol = controls.read(protocol_path)
        manifest = controls.read(fixture.manifest_path)
        if invalid is None:
            manifest[manifest_group].pop(manifest_key)
        else:
            manifest[manifest_group][manifest_key] = invalid
        manifest["forward_protocol"] = {
            "sha256": controls.digest(protocol_path),
            "contract": protocol["contract"],
            "fingerprint": protocol["fingerprint"],
        }
        fixture.manifest_path.write_bytes(controls.encode(manifest))
        runner.produce(
            protocol_path,
            capture=fixture.capture,
            out=output,
            now=fixture.end + timedelta(seconds=10),
        )
    assert not output.exists()
