"""Two screenshot-only encodings; symbols describe colors, never game objects.

Both alternatives contain exactly the same 64x60 quantized cells. Comparing
them therefore measures serialization effects without changing visual evidence.
The run representation can save tokens on flat backgrounds but requires more
coordinate reasoning. Neither representation infers solidity or motion.
"""
from collections import Counter
from pathlib import Path
import time

from PIL import Image, ImageOps


# Fixed generic color prototypes, not a sprite/level-specific classifier.
PALETTE = {
    'K': ('black', (0, 0, 0)),
    'H': ('gray', (128, 128, 128)),
    'W': ('white', (255, 255, 255)),
    'D': ('dark blue', (0, 0, 136)),
    'B': ('blue', (0, 64, 224)),
    'S': ('light blue', (92, 148, 252)),
    'C': ('cyan', (0, 184, 216)),
    'G': ('green', (0, 168, 0)),
    'L': ('light green', (184, 248, 24)),
    'Y': ('yellow', (248, 216, 120)),
    'O': ('orange', (248, 120, 0)),
    'R': ('red', (248, 56, 0)),
    'N': ('brown', (172, 80, 0)),
    'T': ('tan', (252, 160, 68)),
    'P': ('pink', (248, 120, 248)),
    'V': ('purple', (184, 0, 184)),
}
_ORDER = {symbol: index for index, symbol in enumerate(PALETTE)}


def _quantize(rgb):
    return min(PALETTE, key=lambda symbol: sum(
        (rgb[channel] - PALETTE[symbol][1][channel]) ** 2
        for channel in range(3)))


def _color_grid(image, columns, rows):
    if columns > image.width or rows > image.height:
        raise ValueError('Grid dimensions cannot exceed source dimensions')
    pixels = list(image.get_flattened_data() if hasattr(image, 'get_flattened_data')
                  else image.getdata())
    colors = {rgb: _quantize(rgb) for rgb in set(pixels)}
    symbols = [colors[rgb] for rgb in pixels]
    lines = []
    for y in range(rows):
        top, bottom = y * image.height // rows, (y + 1) * image.height // rows
        line = []
        for x in range(columns):
            left, right = x * image.width // columns, (x + 1) * image.width // columns
            counts = Counter(symbols[py * image.width + px]
                             for py in range(top, bottom) for px in range(left, right))
            # Fixed palette order breaks ties; independent of source iteration order.
            line.append(min(counts, key=lambda symbol: (-counts[symbol], _ORDER[symbol])))
        lines.append(''.join(line))
    return lines


def _runs(lines, default):
    """Return y-inclusive/exclusive row bands and x-inclusive/exclusive runs."""
    bands = []
    for y, line in enumerate(lines):
        runs = []
        x = 0
        while x < len(line):
            end = x + 1
            while end < len(line) and line[end] == line[x]:
                end += 1
            if line[x] != default:
                runs.append([x, end, line[x]])
            x = end
        if bands and bands[-1][2] == runs:
            bands[-1][1] = y + 1
        else:
            bands.append([y, y + 1, runs])
    return bands


def encode_alternatives(image_path, columns=64, rows=60):
    """Return self-contained JSON payloads ``color_grid`` and ``color_runs``.

    Timing is measurement metadata, not evidence for perception. No filename,
    hash, RAM, sprite templates, OCR, or semantic labels enter either payload.
    """
    if not isinstance(columns, int) or not isinstance(rows, int) or columns < 1 or rows < 1:
        raise ValueError('Grid dimensions must be positive integers')
    started = time.perf_counter()
    with Image.open(Path(image_path)) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
    lines = _color_grid(image, columns, rows)
    counts = Counter(''.join(lines))
    default = min(counts, key=lambda symbol: (-counts[symbol], _ORDER[symbol]))
    common = {
        'columns': columns, 'rows': rows, 'source_size': list(image.size),
        'coordinate_system': {
            'origin': 'top-left', 'x_direction': 'right', 'y_direction': 'down',
            'index_base': 0,
            'cell_pixel_bounds': '[floor(x*width/columns), floor((x+1)*width/columns)) '
                                 'and [floor(y*height/rows), floor((y+1)*height/rows))',
        },
        'legend': {symbol: {'color': color, 'rgb': list(rgb)}
                   for symbol, (color, rgb) in PALETTE.items()},
        'interpretation': 'Each cell is the most frequent quantized pixel color in its rectangle. '
                          'RGB pixels use nearest squared Euclidean color distance; ties use legend order. '
                          'Symbols mean colors only, not objects, solidity, danger or background. '
                          'Thin details may disappear. One still image does not reveal motion.',
    }
    grid = {**common, 'encoding': 'color_symbol_grid', 'grid': '\n'.join(lines)}
    grid['conversion_ms'] = round((time.perf_counter() - started) * 1000, 3)
    runs = {**common, 'encoding': 'color_row_runs', 'default_symbol': default,
            'format': 'Start with every cell equal to default_symbol. Each band is '
                      '[y_start,y_end,runs]; each run is [x_start,x_end,symbol]. '
                      'All ends are exclusive. Paint each run on every row in its band. '
                      'Missing runs retain default_symbol, which is only a color.',
            'bands': _runs(lines, default)}
    runs['conversion_ms'] = round((time.perf_counter() - started) * 1000, 3)
    return {'color_grid': grid, 'color_runs': runs}
