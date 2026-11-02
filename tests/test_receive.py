import zipfile

from lifeos import intake
from lifeos.receive import ANSI, Request, acceptable, parse_request

SCREEN = """Ready to accept requests.
D [1] 示例手机 (192.168.0.10)
R 示例手机
  Files (1, 1.4 GB):
    HealthAutoExport_20261102.zip (1.4 GB)
  Accept? Y/N/P (P = accept and pair)"""


def test_parse_request_from_the_cli_screen():
    lines = ANSI.sub("", "\x1b[1m" + SCREEN + "\x1b[0m").splitlines()
    req = parse_request(lines)
    assert req == Request("示例手机", "1.4 GB", [("HealthAutoExport_20261102.zip", "1.4 GB")])
    done = lines + ["R 示例手机: Received 1 file (1.4 GB, took 8s)"]
    assert parse_request(done).sender == "示例手机"  # the result line is not a new request


def test_unattended_accepts_only_the_named_device_and_exports():
    req = Request("示例手机", "182 MB", [("导出.zip", "182 MB")])
    assert acceptable(req, "示例手机")
    assert not acceptable(req, None)
    assert not acceptable(req, "别人的手机")
    assert not acceptable(Request("示例手机", "1 MB", [("app.exe", "1 MB")]), "示例手机")


def test_detect_export_kinds(tmp_path):
    apple = tmp_path / "导出.zip"
    with zipfile.ZipFile(apple, "w") as z:
        z.writestr("apple_health_export/导出.xml", "<HealthData/>")
        z.writestr("apple_health_export/export_cda.xml", "<x/>")
    hae = tmp_path / "HealthAutoExport_1.zip"
    with zipfile.ZipFile(hae, "w") as z:
        z.writestr("HealthAutoExport-2019-10-26-2026-11-02.csv", "x")
    other = tmp_path / "photos.zip"
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("a.jpg", "x")
    (tmp_path / "push.json").write_text("{}")
    assert intake.detect(apple) == "apple-health"
    assert intake.detect(hae) == "health-auto-export-csv"
    assert intake.detect(tmp_path / "push.json") == "health-auto-export"
    assert intake.detect(other) is None
