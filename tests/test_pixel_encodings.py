"""Pixel-only encoder geometry, color preservation and serialization regressions."""
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from pixel_encodings import PALETTE, encode_alternatives


class PixelEncodingTests(unittest.TestCase):
    def encode(self, image, columns, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.png'
            image.save(path)
            return encode_alternatives(path, columns=columns, rows=rows)

    def test_color_rectangles_preserve_coordinates(self):
        image = Image.new('RGB', (16, 12), PALETTE['S'][1])
        draw = ImageDraw.Draw(image)
        draw.rectangle((4, 4, 7, 7), fill=PALETTE['R'][1])
        draw.rectangle((8, 8, 15, 11), fill=PALETTE['G'][1])
        result = self.encode(image, 4, 3)
        self.assertEqual(result['color_grid']['grid'], 'SSSS\nSRSS\nSSGG')
        self.assertEqual(result['color_runs']['bands'], [
            [0, 1, []], [1, 2, [[1, 2, 'R']]], [2, 3, [[2, 4, 'G']]]])

    def test_runs_reconstruct_grid_exactly_and_compress_repeated_rows(self):
        image = Image.new('RGB', (16, 16), PALETTE['K'][1])
        ImageDraw.Draw(image).rectangle((4, 4, 11, 11), fill=PALETTE['W'][1])
        result = self.encode(image, 4, 4)
        runs = result['color_runs']
        decoded = [[runs['default_symbol']] * runs['columns'] for _ in range(runs['rows'])]
        for y0, y1, row_runs in runs['bands']:
            for x0, x1, symbol in row_runs:
                for y in range(y0, y1):
                    decoded[y][x0:x1] = [symbol] * (x1 - x0)
        self.assertEqual('\n'.join(''.join(row) for row in decoded), result['color_grid']['grid'])
        self.assertIn([1, 3, [[1, 3, 'W']]], runs['bands'])

    def test_every_exact_palette_color_round_trips(self):
        image = Image.new('RGB', (len(PALETTE), 1))
        image.putdata([rgb for _, rgb in PALETTE.values()])
        result = self.encode(image, len(PALETTE), 1)
        self.assertEqual(result['color_grid']['grid'], ''.join(PALETTE))

    def test_uneven_source_partition_and_ties(self):
        image = Image.new('RGB', (5, 3), PALETTE['S'][1])
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1, 2), fill=PALETTE['R'][1])
        self.assertEqual(self.encode(image, 2, 1)['color_grid']['grid'], 'RS')
        image = Image.new('RGB', (2, 1))
        image.putdata([PALETTE['W'][1], PALETTE['K'][1]])
        self.assertEqual(self.encode(image, 1, 1)['color_grid']['grid'], 'K')

    def test_determinism_except_timing_and_no_source_identifiers(self):
        image = Image.new('RGB', (8, 8), PALETTE['N'][1])
        first, second = self.encode(image, 2, 2), self.encode(image, 2, 2)
        for name in first:
            self.assertGreaterEqual(first[name].pop('conversion_ms'), 0)
            second[name].pop('conversion_ms')
            self.assertEqual(first[name], second[name])
        serialized = json.dumps(first)
        for forbidden in ('fixture.png', 'sha256', 'image_path'):
            self.assertNotIn(forbidden, serialized)

    def test_invalid_grid_rejected(self):
        image = Image.new('RGB', (4, 4))
        for dimensions in [(0, 2), (2, -1), (5, 2)]:
            with self.assertRaises(ValueError):
                self.encode(image, *dimensions)


if __name__ == '__main__':
    unittest.main()
