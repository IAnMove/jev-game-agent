"""Deterministic pixels-to-ASCII conversion. No RAM, object detector or level map."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

from PIL import Image, ImageDraw, ImageFont, ImageOps

PALETTE = ' .:-=+*#%@'


def convert_image(path, columns=64, rows=30, mode='background_contrast'):
    if not 8 <= columns <= 160 or not 4 <= rows <= 120:
        raise ValueError('Grid must be 8..160 columns and 4..120 rows')
    if mode not in ('luminance', 'background_contrast'):
        raise ValueError('Unknown conversion mode')
    started = time.perf_counter()
    path = Path(path)
    with Image.open(path) as source:
        rgb = ImageOps.exif_transpose(source).convert('RGB')
    pixels = rgb.get_flattened_data() if hasattr(rgb, 'get_flattened_data') else list(rgb.getdata())
    background = Counter(pixels).most_common(1)[0][0]
    if mode == 'background_contrast':
        # Most frequent exact RGB is an image statistic, not a semantic sky label.
        strength = Image.new('L', rgb.size)
        strength.putdata([max(abs(p[i]-background[i]) for i in range(3)) for p in pixels])
    else:
        strength = rgb.convert('L')
    small = strength.resize((columns, rows), Image.Resampling.BOX)
    data = small.get_flattened_data() if hasattr(small, 'get_flattened_data') else list(small.getdata())
    symbols = [PALETTE[min(len(PALETTE)-1, value*len(PALETTE)//256)] for value in data]
    lines = [''.join(symbols[y*columns:(y+1)*columns]) for y in range(rows)]
    return {'ascii': '\n'.join(lines), 'columns': columns, 'rows': rows,
        'source_size': list(rgb.size), 'pixels_per_cell': [rgb.width/columns, rgb.height/rows],
        'mode': mode, 'palette_low_to_high': PALETTE, 'background_rgb': list(background),
        'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'conversion_ms': round((time.perf_counter()-started)*1000, 3)}


def attach_ascii(payload, image_path, allowed_level, artifact_dir=None):
    """Add only the image representation, keeping existing options/state intact."""
    if not allowed_level or payload['state']['current']['level'] != allowed_level:
        return None
    result = convert_image(image_path)
    payload['state']['screen_ascii'] = {k: v for k, v in result.items()
                                       if k not in ('conversion_ms', 'source_sha256')}
    payload['state']['screen_ascii']['interpretation'] = (
        'This grid is generated from screenshot PIXELS, not RAM or object labels. '
        'Each cell covers the stated pixel rectangle, rows top-to-bottom, columns left-to-right. '
        'Space means little difference from the most common image colour; denser glyphs mean more colour contrast. '
        'Glyphs do NOT identify Mario, enemies, solid ground or empty space. Background decorations can also be visible. '
        'Use it as additional spatial context alongside RAM motion and the SAME simulated candidate outcomes. '
        'A single screenshot does not encode platform velocity or prove that a landing is safe.')
    if artifact_dir:
        folder = Path(artifact_dir)
        (folder/'screen_ascii.txt').write_text(result['ascii'], encoding='ascii')
        (folder/'screen_ascii.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def preview(image_path, result, output):
    """Render a review artifact; it is not sent as an image to Jev."""
    font = ImageFont.truetype('C:/Windows/Fonts/consola.ttf', 14)
    label = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 18)
    cw, ch = 9, 16
    w, h = result['columns']*cw, result['rows']*ch
    canvas = Image.new('RGB', (2*w+48, h+76), '#11151c')
    with Image.open(image_path) as original:
        original = original.convert('RGB')
        scale = min(w/original.width, h/original.height)
        original = original.resize((round(original.width*scale), round(original.height*scale)), Image.Resampling.NEAREST)
        canvas.paste(original, (16+(w-original.width)//2, 52))
    draw = ImageDraw.Draw(canvas)
    draw.text((16, 15), 'Original screenshot', font=label, fill='white')
    draw.text((w+32, 15), f"ASCII from pixels only ({result['columns']} x {result['rows']})", font=label, fill='white')
    for y, line in enumerate(result['ascii'].split('\n')):
        for x, char in enumerate(line):
            draw.text((w+32+x*cw, 52+y*ch), char, font=font, fill='#dbe8f7')
    canvas.save(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('image', type=Path)
    parser.add_argument('--out', type=Path, required=True, help='Output stem (.txt/.json/.png)')
    parser.add_argument('--columns', type=int, default=64)
    parser.add_argument('--rows', type=int, default=30)
    parser.add_argument('--mode', choices=('luminance','background_contrast'), default='background_contrast')
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result = convert_image(args.image, args.columns, args.rows, args.mode)
    args.out.with_suffix('.txt').write_text(result['ascii'], encoding='ascii')
    args.out.with_suffix('.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    preview(args.image, result, args.out.with_suffix('.png'))
    print(json.dumps({k: v for k, v in result.items() if k != 'ascii'}))


if __name__ == '__main__':
    main()
