import copy
import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageDraw
from image_ascii import convert_image, attach_ascii


class ImageAsciiTests(unittest.TestCase):
    def test_uniform_background_is_blank_without_invented_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'image.png'
            Image.new('RGB', (64,32), (120,140,240)).save(path)
            result = convert_image(path, 16, 8)
            self.assertEqual(result['ascii'].split('\n'), [' '*16]*8)

    def test_foreground_position_and_grid_dimensions_survive_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'image.png'
            image = Image.new('RGB', (64,32), 'black')
            ImageDraw.Draw(image).rectangle((32,16,47,23), fill='white')
            image.save(path)
            result = convert_image(path,16,8)
            lines = result['ascii'].split('\n')
            self.assertEqual(lines[4], ' '*8+'@'*4+' '*4)
            self.assertEqual(lines[3], ' '*16)
            self.assertEqual(convert_image(path,16,8)['ascii'], result['ascii'])
            self.assertEqual(result['pixels_per_cell'], [4,4])

    def test_scope_prevents_leaking_experiment_to_another_level(self):
        payload = {'state': {'current': {'level':'5-4'}}, 'questions': {'maneuver': {'criteria': {'run':'same'}}}}
        original = copy.deepcopy(payload)
        self.assertIsNone(attach_ascii(payload, 'does-not-exist.png', '5-3'))
        self.assertEqual(payload, original)

    def test_ascii_is_additive_and_does_not_change_available_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'image.png'; Image.new('RGB',(256,240),'blue').save(path)
            payload = {'state': {'current': {'level':'5-3'}}, 'questions': {'maneuver': {'criteria': {'run':'same'}}}}
            original = copy.deepcopy(payload)
            attach_ascii(payload,path,'5-3',tmp)
            self.assertIn('screen_ascii',payload['state'])
            del payload['state']['screen_ascii']
            self.assertEqual(payload,original)


if __name__ == '__main__':
    unittest.main()
