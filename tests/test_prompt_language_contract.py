"""Guard for localized PMFBY and IMD agromet instructions in rendered language prompts.

This checks script and identifiers, not translation quality or model behavior.
"""
from pathlib import Path
import re
import unittest
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_RANGES = {
    "hi": (0x900, 0x97F), "mai": (0x900, 0x97F), "mr": (0x900, 0x97F),
    "as": (0x980, 0x9FF), "bn": (0x980, 0x9FF), "gu": (0xA80, 0xAFF),
    "kn": (0xC80, 0xCFF), "ml": (0xD00, 0xD7F), "or": (0xB00, 0xB7F),
    "pa": (0xA00, 0xA7F), "ta": (0xB80, 0xBFF), "te": (0xC00, 0xC7F),
}


def render(language):
    return Environment(loader=FileSystemLoader(ROOT / "assets/prompts")).get_template(f"agrinet_{language}.md").render()


class PromptLanguageContract(unittest.TestCase):
    def test_pmfby_safeguards_keep_language_and_tool_arguments(self):
        for language in ["en", *SCRIPT_RANGES]:
            with self.subTest(language=language):
                text = render(language)
                self.assertNotIn("### PMFBY grievance verification rules", text)
                headings = list(re.finditer(r"^### .*PMFBY.*$", text, re.M))
                self.assertTrue(headings, "Missing localized PMFBY safeguards")
                start = headings[-1].start()
                end = text.index("\n## ", start)
                block = text[start:end]
                for call in [
                    "initiate_pmfby_grievance_otp(phone_number)",
                    "check_pmfby_grievance_otp(otp, phone_number)",
                    "pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)",
                ]:
                    self.assertIn(f"`{call}`", block)
                self.assertIn("10", block)
                self.assertIn("6", block)
                if language == "en":
                    continue
                prose = re.sub(r"`[^`]+`", "", block)
                prose = re.sub(r"(?<![A-Za-z])(?:PMFBY|OTP)(?![A-Za-z])", "", prose)
                self.assertIsNone(re.search(r"[A-Za-z]{2,}", prose), "English prose in localized safeguards")
                lo, hi = SCRIPT_RANGES[language]
                for line in block.splitlines():
                    if line.startswith("- "):
                        self.assertTrue(any(lo <= ord(char) <= hi for char in line), "Instruction is missing the language's script")

    def test_imd_agromet_instructions_keep_language_and_tool_names(self):
        for language in ["en", *SCRIPT_RANGES]:
            with self.subTest(language=language):
                rows = [line for line in render(language).splitlines()
                        if line.startswith("|") and "`imd_agromet_advisory`" in line]
                self.assertEqual(len(rows), 1, "Expected exactly one IMD agromet tool row")
                section = (ROOT / "assets/prompts/capabilities/agrinet/weather/agromet"
                           / f"agromet_{language}.md").read_text()
                self.assertIn(section.strip(), render(language), "IMD agromet section is not included")
                for name in ["forward_geocode", "search_documents", "search_pests_diseases"]:
                    self.assertIn(f"`{name}`", rows[0])
                self.assertIn("`crop`", section)
                if language == "en":
                    continue
                lo, hi = SCRIPT_RANGES[language]
                for text in [rows[0], section]:
                    prose = re.sub(r"`[^`]+`", "", text)
                    prose = re.sub(r"(?<![A-Za-z])IMD(?![A-Za-z])", "", prose)
                    self.assertIsNone(re.search(r"[A-Za-z]{2,}", prose), "English prose in localized IMD instructions")
                for cell in rows[0].strip("|").split("|"):
                    self.assertTrue(any(lo <= ord(char) <= hi for char in cell), "Cell is missing the language's script")
                for line in section.splitlines():
                    if line.startswith("- "):
                        self.assertTrue(any(lo <= ord(char) <= hi for char in line), "Instruction is missing the language's script")

if __name__ == "__main__":
    unittest.main()
