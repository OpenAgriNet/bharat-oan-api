"""Guard for PM-KISAN routing and localized PMFBY/IMD instructions in rendered prompts.

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


def render(language, **context):
    return Environment(loader=FileSystemLoader(ROOT / "assets/prompts")).get_template(f"agrinet_{language}.md").render(**context)


class PromptLanguageContract(unittest.TestCase):
    def test_pmkisan_release_date_is_not_personal_status(self):
        for language in ["en", *SCRIPT_RANGES]:
            with self.subTest(language=language):
                text = render(language, today_date="28 October 2026")
                blocks = [block for block in text.split("\n\n")
                          if block.startswith("**PM-KISAN 24th installment release date:**")]
                self.assertEqual(len(blocks), 1)
                block = blocks[0]
                self.assertIn("Do not call search_schemes, load_capability or any status/OTP tool", block)
                self.assertIn("The 24th installment of PM kisan will release on 30th October 2026.", text)
                self.assertIn("The 24th installment of PM kisan was released on 30th October 2026.", text)
                self.assertIn("पीएम-किसान योजना की 24वीं किस्त दिनांक 30 अक्टूबर 2026 को जारी की जाएगी।", text)
                self.assertIn("पीएम-किसान योजना की 24वीं किस्त दिनांक 30 अक्टूबर 2026 को जारी की गई।", text)
                self.assertIn("When will the next PM-KISAN installment be released?", block)
                self.assertIn("No registration number or OTP is needed", block)
                self.assertIn("30 October 2026", block)
                self.assertIn("31 October 2026", block)
                self.assertIn("28 October 2026", block)
                self.assertNotIn("or when the next instalment will come", text)
                self.assertNotIn("23rd", text)
                self.assertIn('"PM Kisan installment status"', text)
                self.assertIn("initiate_pm_kisan_status_check", text)
                self.assertIn("check_pm_kisan_status_with_otp", text)

    def test_release_routing_is_visible_before_loading_search(self):
        for language in ["en", *SCRIPT_RANGES]:
            with self.subTest(language=language):
                text = render(language, capability_enabled=lambda names: False)
                block = next(block for block in text.split("\n\n")
                             if "**Scheme information vs. status request:**" in block)
                self.assertIn("When will the next PM-KISAN installment be released?", block)
                self.assertIn("when will pm kisan installement arrivces", block)
                self.assertIn("A previous status-check offer is not consent", block)
                self.assertIn("Do not continue a pending request for identifiers", block)
                self.assertIn("Answer directly from the configured release announcement in this prompt", block)
                self.assertIn("do not search or load a capability", block)
                self.assertLess(block.index("**General PM-KISAN release date**"),
                                block.index("**Direct status request**"))
                self.assertIn("do not ask for registration number, phone number or OTP", block)

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
                for name in ["place", "forward_geocode", "search_documents", "search_pests_diseases"]:
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
