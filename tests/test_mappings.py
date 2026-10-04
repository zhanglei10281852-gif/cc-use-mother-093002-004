import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from skill_recognition.errors import ValidationError
from skill_recognition.mappings import (
    MappingBook,
    MappingDecision,
    MappingStatus,
    RecognitionMapping,
    validate_mapping_against_versions,
)

SRC = ("CERT", 1)
TGT = ("STD", 1)
UNITS = frozenset({"U1", "U2", "U3"})
COVERED_ALL = UNITS


def make_mapping(mapping_id="M1", **over) -> RecognitionMapping:
    params = dict(
        mapping_id=mapping_id,
        source_cert_key=SRC,
        target_standard_key=TGT,
        target_grade_label="中级",
        decision=MappingDecision.PARTIAL_EQUIVALENT,
        covered_units=frozenset({"U1"}),
        valid_from="2019-05-01",
        source_case_id="CASE-1",
    )
    params.update(over)
    return RecognitionMapping(**params)


class MappingValidationTests(unittest.TestCase):
    def test_equivalent_requires_full_coverage(self):
        m = make_mapping(
            decision=MappingDecision.EQUIVALENT,
            covered_units=frozenset({"U1", "U2"}),
        )
        with self.assertRaises(ValidationError):
            validate_mapping_against_versions(m, UNITS, UNITS)

    def test_equivalent_full_coverage_passes(self):
        m = make_mapping(decision=MappingDecision.EQUIVALENT, covered_units=UNITS)
        validate_mapping_against_versions(m, UNITS, UNITS)

    def test_partial_cannot_cover_all_or_none(self):
        for covered in (frozenset(), UNITS):
            with self.assertRaises(ValidationError):
                validate_mapping_against_versions(
                    make_mapping(covered_units=covered), UNITS, UNITS
                )

    def test_covered_unit_missing_from_source_cert_rejected(self):
        with self.assertRaises(ValidationError):
            validate_mapping_against_versions(
                make_mapping(covered_units=frozenset({"U1", "U2"})),
                frozenset({"U1"}), UNITS,
            )

    def test_assessment_units_must_be_listed_and_non_overlapping(self):
        with self.assertRaises(ValidationError):
            validate_mapping_against_versions(
                make_mapping(decision=MappingDecision.ADDITIONAL_ASSESSMENT,
                             covered_units=frozenset({"U1"}),
                             assessment_units=frozenset()),
                UNITS, UNITS,
            )
        with self.assertRaises(ValidationError):
            validate_mapping_against_versions(
                make_mapping(decision=MappingDecision.ADDITIONAL_ASSESSMENT,
                             covered_units=frozenset({"U1"}),
                             assessment_units=frozenset({"U1"})),
                UNITS, UNITS,
            )

    def test_not_recognized_declares_no_units(self):
        with self.assertRaises(ValidationError):
            validate_mapping_against_versions(
                make_mapping(decision=MappingDecision.NOT_RECOGNIZED,
                             covered_units=frozenset({"U1"})),
                UNITS, UNITS,
            )

    def test_unknown_target_unit_rejected(self):
        m = make_mapping(covered_units=frozenset({"U9"}))
        with self.assertRaises(ValidationError):
            validate_mapping_against_versions(m, UNITS, UNITS)


class MappingBookTests(unittest.TestCase):
    def test_terminate_keeps_history_for_past_dates(self):
        book = MappingBook()
        book.add(make_mapping())
        book.terminate("M1", "2022-06-30", superseded_by_new="M2")
        kept = book.get("M1")
        self.assertEqual(kept.status, MappingStatus.SUPERSEDED)
        self.assertTrue(kept.is_effective_on("2022-06-29"))
        self.assertFalse(kept.is_effective_on("2022-06-30"))
        self.assertEqual(kept.superseded_by, "M2")

    def test_terminate_is_idempotency_guarded(self):
        book = MappingBook()
        book.add(make_mapping())
        book.terminate("M1", "2022-06-30")
        with self.assertRaises(ValidationError):
            book.terminate("M1", "2022-07-01")

    def test_duplicate_mapping_id_rejected(self):
        book = MappingBook()
        book.add(make_mapping())
        with self.assertRaises(ValidationError):
            book.add(make_mapping())


if __name__ == "__main__":
    unittest.main()
