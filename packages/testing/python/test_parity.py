import subprocess
import sys
import unittest
from pathlib import Path


class FixtureParityTests(unittest.TestCase):
    def test_checked_in_fixtures_match_python_behavior(self):
        script = Path(__file__).with_name("export_contract_fixtures.py")
        subprocess.run([sys.executable, str(script), "--check"], check=True)


if __name__ == "__main__":
    unittest.main()
