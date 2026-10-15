import re
import unittest
from pathlib import Path


PROMPT_DIR = Path(__file__).resolve().parents[1] / "assets" / "prompts"


class PmkisanPromptValidationTests(unittest.TestCase):
    def test_registration_examples_and_tool_handoff_in_every_language(self):
        prompts = sorted(PROMPT_DIR.glob("agrinet_*.md"))
        self.assertTrue(prompts)
        for path in prompts:
            with self.subTest(language=path.stem):
                prompt = path.read_text(encoding="utf-8")
                rule = next(
                    line for line in prompt.splitlines()
                    if line.startswith("**PM-KISAN Registration Number Validation:**")
                )
                self.assertIn("trim surrounding whitespace", rule)
                pattern = rule.split("`")[1]
                for example in ("TN110700729", "TN110698429"):
                    self.assertIsNotNone(re.fullmatch(pattern, example))
                    self.assertNotIn(example, prompt)
                    self.assertIsNotNone(re.fullmatch(pattern, f" {example.lower()} ".strip().upper()))
                self.assertIn("`TN000000000`", rule)
                self.assertIn("Never show a real registration number as an example", rule)
                for invalid in ("TN11069842", "TN1106984290", "TN11069842X", "12110698429"):
                    self.assertIsNone(re.fullmatch(pattern, invalid))
                self.assertIn("11 characters, not 11 digits", rule)
                self.assertIn("do not reject it or ask for the number again", rule)
                self.assertIn("call `initiate_pm_kisan_status_check(reg_no)`", rule)
                self.assertIn("do not claim the portal rejected it", rule)
                self.assertIn("Only report a missing record if the status tool", rule)


if __name__ == "__main__":
    unittest.main()
