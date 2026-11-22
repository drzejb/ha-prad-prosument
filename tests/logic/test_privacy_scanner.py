"""Tests for the privacy scanner. Suspicious strings are assembled at runtime so the scanner
does not flag this file itself."""
import importlib.util
import re
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_private.py"
_spec = importlib.util.spec_from_file_location("check_private", _SCRIPT)
check_private = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_private)
scan_text = check_private.scan_text


def test_detects_private_ip():
    ip = ".".join(["192", "168", "1", "10"])
    assert scan_text("host: " + ip, []) != []
    assert scan_text("x " + ".".join(["10", "0", "0", "5"]), []) != []


def test_detects_coordinates():
    assert scan_text("latitude: " + "50." + "123456", []) != []
    assert scan_text("lat=" + "50." + "12345" + ", lon=" + "17." + "12345", []) != []


def test_detects_non_noreply_email():
    mail = "jan.kowalski" + "@" + "gmail.com"
    assert scan_text("mail: " + mail, []) != []
    ok = "123+user" + "@" + "users.noreply.github.com"
    assert scan_text("x " + ok, []) == []


def test_detects_ppe_like_number():
    ppe = "590" + "3" * 15
    assert scan_text("PPE " + ppe, []) != []


def test_extra_patterns_from_file():
    assert scan_text("Ulica Testowa 1", [re.compile("Testowa", re.I)]) != []


def test_clean_text_passes():
    assert scan_text("energy_peak = Decimal('0.62700')\npobor = 195", []) == []
