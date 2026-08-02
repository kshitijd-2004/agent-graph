# test_main.py
import unittest
from main import process_data
class TestMain(unittest.TestCase):
    def test_process(self):
        self.assertEqual(process_data([1, 2]), [2, 4])
