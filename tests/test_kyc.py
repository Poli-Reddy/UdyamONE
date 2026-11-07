"""KYC answer-key tests.  These run the real OCR pipeline, so they are slower."""
import os

import pytest

import kyc

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(HERE, "kyc_samples")
EXPECTED = {
    "K01": "REJECTED",       # cancelled GSTIN + missing Udyam number
    "K02": "VERIFIED",       # tilted photo, auto-straightened
    "K03": "VERIFIED",       # pink watermark, GSTIN recovered
    "K04": "ACTION_REQUIRED",  # blurry GST certificate
    "K05": "ACTION_REQUIRED",  # missing Udyam certificate
    "K06": "REJECTED",       # altered GSTIN
    "K07": "REJECTED",       # legal name does not match registry
    "K08": "VERIFIED",
    "K09": "VERIFIED",
    "K10": "VERIFIED",
    "K11": "ACTION_REQUIRED",  # address proof older than 90 days
    "K12": "VERIFIED",
}


@pytest.mark.skipif(kyc.backend() == "none", reason="no OCR backend installed")
@pytest.mark.parametrize("applicant,verdict", EXPECTED.items())
def test_answer_key(applicant, verdict):
    _, got, findings = kyc.check(SAMPLES, applicant)
    assert got == verdict, (applicant, got, findings)


@pytest.mark.skipif(kyc.backend() == "none", reason="no OCR backend installed")
def test_gst_free_lane_uses_udyam_not_gst():
    _, verdict, findings = kyc.check(SAMPLES, "K03", lane="gst_free")
    assert verdict == "VERIFIED"
    assert not any(f["code"] == "missing_doc" for f in findings)


@pytest.mark.skipif(kyc.backend() == "none", reason="no OCR backend installed")
def test_gst_free_lane_still_requires_udyam():
    _, verdict, findings = kyc.check(SAMPLES, "K05", lane="gst_free")
    assert verdict == "ACTION_REQUIRED"
    assert any(f["code"] in ("missing_doc", "udyam_number") for f in findings)


def test_mask_does_not_reveal_full_identifier():
    assert kyc.mask("27XUIXG0767D1ZP") == "27*********D1ZP"
