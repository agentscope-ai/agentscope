# -*- coding: utf-8 -*-
"""Two scoped image tools for a permission-aware AgentScope example."""

import json
from pathlib import Path
from uuid import uuid4

import numpy as np

from agentscope.message import TextBlock
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, ToolChunk, Toolkit


MAX_PIXELS = 20_000_000


def _grayscale(path: Path) -> np.ndarray:
    """Load one 8-bit image, orient it, and composite alpha on white."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(path) as opened:
            if getattr(opened, "n_frames", 1) != 1:
                raise ValueError("Multi-frame images are not supported")
            if opened.width * opened.height > MAX_PIXELS:
                raise ValueError("Image exceeds the 20 million pixel limit")
            if opened.mode.startswith("I;16") or opened.mode in {"I", "F"}:
                raise ValueError("16-bit and float images need conversion")
            image = ImageOps.exif_transpose(opened)
            if "A" in image.getbands() or "transparency" in image.info:
                rgba = image.convert("RGBA")
                white = Image.new("RGBA", rgba.size, (255,) * 4)
                image = Image.alpha_composite(white, rgba)
            return np.asarray(image.convert("L"), dtype=np.uint8).copy()
    except (FileNotFoundError, UnidentifiedImageError) as exc:
        raise ValueError(f"Cannot open image: {path.name}") from exc


def _region(
    gray: np.ndarray,
    x: int,
    y: int,
    width: int | None,
    height: int | None,
) -> tuple[np.ndarray, dict[str, int]]:
    """Select a region in orientation-corrected image coordinates."""
    image_height, image_width = gray.shape
    if width is None and height is None:
        if x != 0 or y != 0:
            raise ValueError("ROI x and y require width and height")
        width, height = image_width, image_height
    elif width is None or height is None:
        raise ValueError("ROI width and height must be supplied together")
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("ROI coordinates or size are invalid")
    if x + width > image_width or y + height > image_height:
        raise ValueError("ROI extends outside the image")
    return (
        gray[y : y + height, x : x + width],
        {"x": x, "y": y, "width": width, "height": height},
    )


def _otsu(gray: np.ndarray) -> tuple[np.ndarray, int | None]:
    """Return a bright-foreground mask and Otsu threshold."""
    histogram = np.bincount(gray.ravel(), minlength=256).astype(float)
    counts = np.cumsum(histogram)
    weighted = np.cumsum(histogram * np.arange(256))
    total = float(gray.size)
    denominator = counts * (total - counts)
    valid = denominator > 0
    if not np.any(valid):
        return np.zeros_like(gray), None
    score = np.zeros(256)
    score[valid] = (
        weighted[valid] * total - weighted[-1] * counts[valid]
    ) ** 2 / denominator[valid]
    threshold = int(np.argmax(score))
    return np.where(gray > threshold, 255, 0).astype(np.uint8), threshold


class ImageTools:
    """Limit input filenames and put derived PNGs in one output directory."""

    def __init__(self, input_dir: Path, output_dir: Path) -> None:
        self.input_dir = input_dir.resolve(strict=True)
        if not self.input_dir.is_dir():
            raise ValueError("input_dir must be a directory")
        self.output_dir = output_dir.resolve()

    def _read(self, image_name: str) -> np.ndarray:
        if not image_name or Path(image_name).name != image_name:
            raise ValueError("Use a filename inside the input directory")
        try:
            path = (self.input_dir / image_name).resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Image does not exist: {image_name}") from exc
        if not path.is_relative_to(self.input_dir) or not path.is_file():
            raise ValueError("Image must stay inside the input directory")
        return _grayscale(path)

    @staticmethod
    def _result(data: dict) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps(data, ensure_ascii=False))],
        )

    def inspect_image(
        self,
        image_name: str,
        roi_x: int = 0,
        roi_y: int = 0,
        roi_width: int | None = None,
        roi_height: int | None = None,
    ) -> ToolChunk:
        """Report measured gray levels of an image or ROI without writing.

        Args:
            image_name: Filename in the configured input directory.
            roi_x: Left coordinate in orientation-corrected pixels.
            roi_y: Top coordinate in orientation-corrected pixels.
            roi_width: Width; omit with roi_height for the whole image.
            roi_height: Height; omit with roi_width for the whole image.
        """
        selected, roi = _region(
            self._read(image_name),
            roi_x,
            roi_y,
            roi_width,
            roi_height,
        )
        return self._result(
            {
                "image_name": image_name,
                "roi_px": roi,
                "pixel_count": int(selected.size),
                "gray_min": int(selected.min()),
                "gray_max": int(selected.max()),
                "gray_mean": round(float(selected.mean()), 3),
            },
        )

    def segment_otsu(
        self,
        image_name: str,
        roi_x: int = 0,
        roi_y: int = 0,
        roi_width: int | None = None,
        roi_height: int | None = None,
    ) -> ToolChunk:
        """Save a bright-foreground Otsu mask for an image or ROI.

        Args:
            image_name: Filename in the configured input directory.
            roi_x: Left coordinate in orientation-corrected pixels.
            roi_y: Top coordinate in orientation-corrected pixels.
            roi_width: Width; omit with roi_height for the whole image.
            roi_height: Height; omit with roi_width for the whole image.
        """
        from PIL import Image

        selected, roi = _region(
            self._read(image_name),
            roi_x,
            roi_y,
            roi_width,
            roi_height,
        )
        mask, threshold = _otsu(selected)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / (
            f"{Path(image_name).stem}-otsu-{uuid4().hex}.png"
        )
        Image.fromarray(mask).save(path)
        return self._result(
            {
                "image_name": image_name,
                "roi_px": roi,
                "threshold_gray_level": threshold,
                "foreground_fraction": round(
                    float(np.count_nonzero(mask)) / mask.size,
                    6,
                ),
                "output_path": str(path),
                "output_scope": "ROI crop; white=bright foreground",
            },
        )

    def toolkit(self) -> Toolkit:
        """Allow inspection; ask the user before saving a mask."""
        read_permission = PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Inspection reads only a configured image.",
        )
        return Toolkit(
            tools=[
                FunctionTool(
                    self.inspect_image,
                    is_read_only=True,
                    permission=read_permission,
                ),
                FunctionTool(self.segment_otsu),
            ],
        )
