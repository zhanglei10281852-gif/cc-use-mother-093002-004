import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from skill_recognition.certificates import (
    CertificateCatalog,
    CertificateVersion,
    Credential,
    CredentialRegistry,
    Mastery,
    RenewalRule,
    UnitClaim,
)
from skill_recognition.errors import ValidationError


def cert(rev=1, **over):
    params = dict(
        cert_family_id="CERT-X", revision=rev, title="X院校车工证",
        issuer="X院校", grade_label="中级", standard_key=("TURNER", 1),
        claims=frozenset({UnitClaim("U1", Mastery.FULL)}),
        issue_date="2018-06-01",
        renewal_rule=RenewalRule.SUPPLEMENT_EVIDENCE,
        default_validity_years=5,
    )
    params.update(over)
    return CertificateVersion(**params)


class CertificateVersionTests(unittest.TestCase):
    def test_renewal_rule_is_attached_per_version(self):
        self.assertIs(cert().renewal_rule, RenewalRule.SUPPLEMENT_EVIDENCE)
        re_review = cert(revision=2, renewal_rule=RenewalRule.FULL_RE_REVIEW)
        self.assertIs(re_review.renewal_rule, RenewalRule.FULL_RE_REVIEW)
        # 同名等级但不同版本的补证规则可以不同，规则按版本登记
        self.assertNotEqual(cert().renewal_rule, re_review.renewal_rule)

    def test_catalog_rejects_duplicate_version(self):
        cat = CertificateCatalog()
        cat.publish(cert())
        with self.assertRaises(ValidationError):
            cat.publish(cert())

    def test_active_window(self):
        c = cert(retire_date="2023-01-01")
        self.assertTrue(c.is_active_on("2022-12-31"))
        self.assertFalse(c.is_active_on("2023-01-01"))
        self.assertFalse(c.is_active_on("2018-05-31"))

    def test_covered_units_from_claims(self):
        self.assertEqual(cert().covered_units, frozenset({"U1"}))

    def test_invalid_validity_years_rejected(self):
        with self.assertRaises(ValidationError):
            cert(default_validity_years=0)


class CredentialTests(unittest.TestCase):
    def test_validity_window_includes_expiry_day(self):
        cred = Credential("CR-1", "张某", ("CERT-X", 1), "S-1",
                          "2019-03-01", expiry_date="2024-03-01")
        self.assertTrue(cred.is_valid_on("2024-03-01"))
        self.assertFalse(cred.is_valid_on("2024-03-02"))
        self.assertFalse(cred.is_valid_on("2019-02-28"))

    def test_long_lived_credential(self):
        cred = Credential("CR-2", "李某", ("CERT-X", 1), "S-2", "2019-03-01")
        self.assertTrue(cred.is_valid_on("2099-01-01"))

    def test_expiry_before_issue_rejected(self):
        with self.assertRaises(ValidationError):
            Credential("CR-3", "王某", ("CERT-X", 1), "S-3",
                       "2019-03-01", expiry_date="2019-02-28")

    def test_registry_valid_for_on(self):
        reg = CredentialRegistry()
        reg.register(Credential("CR-1", "张某", ("CERT-X", 1), "S-1",
                                "2019-03-01", expiry_date="2024-03-01"))
        self.assertEqual(len(reg.valid_for_on("张某", "2023-01-01")), 1)
        self.assertEqual(reg.valid_for_on("张某", "2025-01-01"), [])


if __name__ == "__main__":
    unittest.main()
