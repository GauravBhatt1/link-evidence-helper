import unittest
from network_safety import UnsafeUrl, validate_public_url

class NetworkSafetyTests(unittest.TestCase):
    def test_rejects_local_targets(self):
        for url in ("http://127.0.0.1/x", "http://localhost/x", "http://169.254.169.254/latest"):
            with self.assertRaises(UnsafeUrl): validate_public_url(url)

if __name__ == "__main__": unittest.main()
