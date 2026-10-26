"""Tests for the deterministic nutrition parser, validator and scorer.

The panel texts here are real: `OCR_*` are verbatim RapidOCR output from
packaging photographs, and `AMAZON_CARD` is the text of Amazon's rendered
nutrition widget. They are what the parser actually has to cope with -- two
panel columns interleaved, labels glued to their values, `0` read as `O`.
"""
from __future__ import annotations

from scraper.extract import nutrients as N

# RapidOCR output for the nutrition panel on an Amazon peanut-butter photo.
OCR_AMAZON_PANEL = (
    "Nutrition Amount/serving % DV Amount/serving % DV Facts Total Fat 15g 19% "
    "Total Carb. 8g 3% About 14 servings Sat. Fat 3g 15% Fiber 2g 7% Trans Fat Og "
    "Total Sugars 3g per container Serv. size Cholest. 0mg 0% Incl.2g Added Sugars 4% "
    "2 Tbsp (32g) Protein 7g Sodium 140mg 6% 7% Calories 180 Vit. D 0% "
    "Calcium 2% Iron 2% Potas. 4% per serving"
)

# RapidOCR output for a Costco Kirkland Signature jar: labels run straight into
# their values, and the %DV column lands between the two halves of the panel.
OCR_COSTCO_PANEL = (
    "Nutrition Facts About 50 servings per container Serving size 2 Tbsp (32g) "
    "Amount per serving Calories 180 % Daily Value Total Fat16g 21% "
    "Saturated Fat 2.5g 13% Trans Fat Og Cholesterol Omg %0 Sodium70mg 3% "
    "Total Carbohydrate7g 3% Dietary Fiber3g 11% Total Sugars 1g "
    "Includes Og Added Sugars 0% Protein 8g Vit D 0mcg 0% Calcium 15mg 2% "
    "lron 0.7mg4% Potas.212mg4%"
)

# Amazon's nutrition card, cells joined by pipes.
AMAZON_CARD = (
    "Nutrition facts | 14.0 servings per container | Serving size | 2 tbsp (32g) | "
    "Amount per serving | Calories | 180 | % Daily Value* | 19% | Total Fat | 15g | "
    "13% | Saturated Fat | 2.5g | Trans Fat | 0g | 0% | Cholesterol | 0mg | 6% | "
    "Sodium | 130mg | 3% | Total Carbohydrate | 7g | 7% | Dietary Fiber | 2g | "
    "Sugars | 3g | Added Sugars | 2g | Protein | 7g"
)

PLAIN_PANEL = """Nutrition Facts
8 servings per container
Serving size 1 cup (240ml)
Calories 150
Total Fat 8g 10%
  Saturated Fat 5g 25%
  Trans Fat 0g
Cholesterol 35mg 12%
Sodium 125mg 5%
Total Carbohydrate 12g 4%
  Dietary Fiber 0g 0%
  Total Sugars 12g
    Includes 0g Added Sugars 0%
Protein 8g 16%
Vitamin D 2.5mcg 15%
Calcium 300mg 25%
"""


class TestParsePanelText:
    def test_reads_interleaved_ocr_columns(self):
        got = N.parse_panel_text(OCR_AMAZON_PANEL)
        assert got["serving_size"] == "2 Tbsp (32g)"
        assert got["calories"] == 180
        assert got["fat_g"] == 15
        assert got["saturated_fat_g"] == 3
        assert got["sodium_mg"] == 140
        assert got["carbs_g"] == 8
        assert got["fiber_g"] == 2
        assert got["protein_g"] == 7
        assert got["added_sugars_g"] == 2

    def test_reads_labels_glued_to_values(self):
        got = N.parse_panel_text(OCR_COSTCO_PANEL)
        assert got["fat_g"] == 16, "'Total Fat16g' must parse without a space"
        assert got["sodium_mg"] == 70
        assert got["carbs_g"] == 7
        assert got["fiber_g"] == 3
        assert got["calories"] == 180, "a %DV column must not truncate 180 to 18"

    def test_reads_o_as_zero(self):
        got = N.parse_panel_text(OCR_COSTCO_PANEL)
        assert got["trans_fat_g"] == 0, "OCR reads the printed 0 of '0g' as 'O'"
        assert got["cholesterol_mg"] == 0
        assert got["added_sugars_g"] == 0

    def test_reads_pipe_separated_cells(self):
        got = N.parse_panel_text(AMAZON_CARD)
        assert got["serving_size"] == "2 tbsp (32g)"
        assert got["servings_per_container"] == 14
        assert got["calories"] == 180
        assert got["sodium_mg"] == 130
        assert got["protein_g"] == 7

    def test_reads_a_plain_panel(self):
        got = N.parse_panel_text(PLAIN_PANEL)
        assert got["serving_size"] == "1 cup (240ml)"
        assert got["calcium_mg"] == 300
        assert got["vitamin_d_mcg"] == 2.5
        assert N.score(got).value == 1.0

    def test_percent_daily_value_is_never_an_amount(self):
        # Vitamin D and calcium appear only as percentages in this panel.
        got = N.parse_panel_text(OCR_AMAZON_PANEL)
        assert "calcium_mg" not in got
        assert got.get("vitamin_d_mcg") in (None, 0)

    def test_saturated_fat_does_not_leak_into_total_fat(self):
        got = N.parse_panel_text("Saturated Fat 2.5g 13% Trans Fat 0g Protein 8g")
        assert "fat_g" not in got
        assert got["saturated_fat_g"] == 2.5

    def test_added_sugars_does_not_leak_into_total_sugars(self):
        got = N.parse_panel_text("Includes 2g Added Sugars 4% Protein 7g")
        assert got["added_sugars_g"] == 2
        assert "sugars_g" not in got

    def test_calories_from_fat_is_not_calories(self):
        got = N.parse_panel_text(
            "Calories 230 Calories from Fat 90 Total Fat 10g "
            "Total Carbohydrate 30g Protein 3g"
        )
        assert got["calories"] == 230

    def test_two_thousand_calorie_footnote_is_ignored(self):
        got = N.parse_panel_text(
            "The % Daily Value tells you how much a nutrient contributes. "
            "2,000 calories a day is used for general nutrition advice."
        )
        assert got == {}

    def test_empty_input(self):
        assert N.parse_panel_text("") == {}
        assert N.parse_panel_text("Front of pack. Organic. 16 oz jar.") == {}


class TestNormalizeMapping:
    def test_canonicalizes_labels_and_units(self):
        got = N.normalize_mapping({
            "Total Fat": "15 g",
            "sodium": {"value": 140, "unit": "mg"},
            "Protein": 7,
            "Dietary Fiber": "2g",
        })
        assert got == {"fat_g": 15, "sodium_mg": 140, "protein_g": 7, "fiber_g": 2}

    def test_converts_between_mass_units(self):
        got = N.normalize_mapping({"sodium": {"value": 1.5, "unit": "g"}})
        assert got["sodium_mg"] == 1500

    def test_keeps_serving_size_as_text(self):
        for label in ("serving_size", "Serving Size", "servingSize"):
            got = N.normalize_mapping({label: "2 tbsp (32g)"})
            assert got["serving_size"] == "2 tbsp (32g)", label

    def test_unknown_nutrients_are_kept_under_a_slug(self):
        got = N.normalize_mapping({"Caffeine": "80mg"})
        assert got["caffeine"] == 80


class TestScoring:
    def test_a_consistent_complete_panel_scores_full_marks(self):
        score = N.score(N.parse_panel_text(PLAIN_PANEL))
        assert score.completeness == 1.0
        assert score.calorie_error < 0.05
        assert score.value == 1.0

    def test_macros_that_contradict_calories_are_capped(self):
        # 7.5g fat + 3.5g carb + 4g protein is ~98 kcal, not 180.
        bad = {"calories": 180, "fat_g": 7.5, "carbs_g": 3.5, "protein_g": 4,
               "sodium_mg": 65, "serving_size": "2 tbsp"}
        score = N.score(bad)
        assert score.completeness == 1.0, "the record looks complete"
        assert score.calorie_error > N.CALORIE_ERROR_LIMIT
        assert score.value <= 0.5, "but must not be accepted on completeness alone"

    def test_a_partial_record_scores_low(self):
        assert N.score({"calories": 180}).value < 0.5

    def test_empty_record_scores_zero(self):
        assert N.score({}).value == 0.0

    def test_percent_misread_as_an_amount_is_implausible(self):
        bad = {"calories": 180, "fat_g": 15, "carbs_g": 7, "protein_g": 7,
               "sodium_mg": 99999}
        assert "sodium_mg" in N.implausible_fields(bad)
        assert not N.score(bad).plausible

    def test_a_part_cannot_exceed_its_whole(self):
        bad = {"fat_g": 2, "saturated_fat_g": 9}
        assert "saturated_fat_g" in N.implausible_fields(bad)

    def test_calorie_error_needs_all_three_macros(self):
        assert N.calorie_error({"calories": 180, "fat_g": 15}) is None


class TestPanelDetection:
    def test_a_panel_scores_high(self):
        assert N.looks_like_panel(OCR_AMAZON_PANEL) >= 0.7
        assert N.looks_like_panel(OCR_COSTCO_PANEL) >= 0.7

    def test_front_of_pack_text_scores_zero(self):
        front = ("KIRKLAND organic PEANUT Made with U.S. Valencia Peanuts BUTTER "
                 "CREAMY REFRIGERATE AFTER OPENING")
        assert N.looks_like_panel(front) < 0.3

    def test_empty_text_scores_zero(self):
        assert N.looks_like_panel("") == 0.0


class TestMerge:
    def test_existing_values_win_by_default(self):
        got = N.merge({"calories": 180}, {"calories": 190, "protein_g": 7})
        assert got == {"calories": 180, "protein_g": 7}

    def test_overwrite_replaces(self):
        got = N.merge({"calories": 180}, {"calories": 190}, overwrite=True)
        assert got["calories"] == 190

    def test_blank_incoming_values_are_skipped(self):
        got = N.merge({"calories": 180}, {"calories": None, "protein_g": ""})
        assert got == {"calories": 180}
