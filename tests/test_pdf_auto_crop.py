"""Automatic PDF crops must not block an otherwise valid paper import."""

import unittest
from pathlib import Path
from unittest.mock import patch

import pdf_tools


class AutomaticCropTests(unittest.TestCase):
    def test_tiny_detected_formula_gets_renderable_page_bounds(self):
        detected = [0.07752, 0.277164, 0.081494, 0.283125]
        with patch.object(pdf_tools, '_crop_page', return_value='equation.jpg') as crop:
            self.assertEqual(pdf_tools._crop_extracted_region(
                Path('unused'), 1, detected, 'equation.jpg'), 'equation.jpg')
        bounds = crop.call_args.args[2]
        self.assertGreaterEqual(bounds[2] - bounds[0], .008 - 1e-12)
        self.assertGreaterEqual(bounds[3] - bounds[1], .008 - 1e-12)
        self.assertTrue(all(0 <= value <= 1 for value in bounds))

    def test_outside_page_box_is_skipped_without_rendering(self):
        with patch.object(pdf_tools, '_crop_page') as crop:
            self.assertIsNone(pdf_tools._crop_extracted_region(
                Path('unused'), 1, [1, .3, 1, .5], 'equation.jpg'))
        crop.assert_not_called()

    def test_manual_crop_still_rejects_tiny_regions(self):
        with self.assertRaisesRegex(ValueError, '裁剪区域过小'):
            pdf_tools._crop_page(Path('unused'), 1,
                                 [0.07752, 0.277164, 0.081494, 0.283125],
                                 'cover.jpg')


if __name__ == '__main__':
    unittest.main()
