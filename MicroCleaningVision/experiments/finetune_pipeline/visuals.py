"""PIL-only visual inspection artifacts with colors supplied by the class config."""
from __future__ import annotations

from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageOps

from .augment import augment_pair


def colorize(mask, schema):
    values = np.asarray(mask)
    output = np.zeros((*values.shape, 3), np.uint8)
    allowed = {item["index"] for item in schema.all_classes}
    if set(np.unique(values)) - allowed:
        raise ValueError("mask contains indices outside class configuration")
    for item in schema.all_classes:
        output[values == item["index"]] = item["color"]
    return Image.fromarray(output)


def overlay(image, mask, schema, alpha=0.4):
    if not 0 <= alpha <= 1:
        raise ValueError("alpha must be in 0..1")
    if image.size != (np.asarray(mask).shape[1], np.asarray(mask).shape[0]):
        raise ValueError("image/mask dimensions differ")
    return Image.blend(image.convert("RGB"), colorize(mask, schema), alpha)


def save_augmentation_grid(dataset, path, count=3):
    """Each sample: RGB row and synchronized mask-overlay row; first column original."""
    if count < 1 or len(dataset) == 0:
        raise ValueError("augmentation grid requires samples and at least one variant")
    tile_w, tile_h, label_h = 320, 240, 26
    row_h = tile_h * 2 + label_h
    legend_h = 36
    grid = Image.new("RGB", ((count + 1) * tile_w, len(dataset) * row_h + legend_h), (24, 24, 24))
    draw = ImageDraw.Draw(grid)
    for i in range(len(dataset)):
        image, mask, metadata = dataset.raw_item(i)
        pairs = [(image, mask)] + [augment_pair(image, mask, dataset.augment_config,
                   dataset.seed + i * 1009 + j, dataset.schema.ignore_index) for j in range(count)]
        for j, (rgb, semantic) in enumerate(pairs):
            x, y = j * tile_w, i * row_h
            draw.text((x + 6, y + 5), f"{metadata['filename']} | {'original' if j == 0 else 'aug ' + str(j)}", fill="white")
            for k, view in enumerate((rgb, overlay(rgb, semantic, dataset.schema))):
                framed = ImageOps.pad(view, (tile_w, tile_h), method=Image.Resampling.BILINEAR, color=(24, 24, 24))
                grid.paste(framed, (x, y + label_h + k * tile_h))
    x, y = 6, grid.height - legend_h + 10
    for item in dataset.schema.all_classes:
        draw.rectangle((x, y, x + 15, y + 15), fill=tuple(item["color"]))
        draw.text((x + 20, y), f"{item['name']} ({item['index']})", fill="white")
        x += 230
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    grid.save(output)
    return output
