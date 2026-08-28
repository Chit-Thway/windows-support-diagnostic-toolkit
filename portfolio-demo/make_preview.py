"""Build a lightweight animated portfolio preview from captured demo frames."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent
TARGET_SIZE = (960, 540)


def load_frame(path: Path) -> Image.Image:
    with Image.open(path) as source:
        return source.convert("RGB").resize(TARGET_SIZE, Image.Resampling.LANCZOS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--frames",
        type=Path,
        default=ROOT / "frames",
        help="Directory containing ordered PNG frames.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "toolkit-preview.gif",
        help="Destination GIF path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_frames = [load_frame(path) for path in sorted(args.frames.glob("*.png"))]
    if not source_frames:
        raise SystemExit(f"No PNG frames found in {args.frames}.")

    frames: list[Image.Image] = []
    durations: list[int] = []

    for index, current in enumerate(source_frames):
        frames.append(current)
        durations.append(1500 if index in {0, len(source_frames) - 1} else 1200)

        if index == len(source_frames) - 1:
            continue

        following = source_frames[index + 1]
        for alpha in (0.25, 0.5, 0.75):
            frames.append(Image.blend(current, following, alpha))
            durations.append(100)

    palette_frames = [
        frame.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
        for frame in frames
    ]
    palette_frames[0].save(
        args.output,
        save_all=True,
        append_images=palette_frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )


if __name__ == "__main__":
    main()
