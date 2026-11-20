# -*- coding: utf-8 -*-
"""Behavioral checks for the permission-aware image example."""

import asyncio
import json
from pathlib import Path

import numpy as np
import pytest

from examples.image_analysis.image_tools import ImageTools
from examples.image_analysis.main import run_demo
from agentscope.tool import ToolChunk

Image = pytest.importorskip("PIL.Image")


def _data(chunk: ToolChunk) -> dict:
    """Decode one JSON result from a tool chunk."""
    return json.loads(chunk.content[0].text)


def test_roi_statistics_and_mask_do_not_modify_source(tmp_path: Path) -> None:
    """An ROI yields measured values and a cropped, bright-foreground mask."""
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    source = image_dir / "sample.png"
    pixels = np.array([[10, 10, 10, 10], [10, 10, 200, 200]], dtype=np.uint8)
    Image.fromarray(pixels).save(source)
    before = source.read_bytes()
    tools = ImageTools(image_dir, tmp_path / "outputs")

    stats = _data(tools.inspect_image("sample.png", 1, 0, 3, 2))
    assert stats["roi_px"] == {"x": 1, "y": 0, "width": 3, "height": 2}
    assert stats["pixel_count"] == 6
    assert stats["gray_min"] == 10
    assert stats["gray_max"] == 200
    assert stats["gray_mean"] == pytest.approx(73.333, abs=0.001)

    mask = _data(tools.segment_otsu("sample.png", 1, 0, 3, 2))
    assert mask["threshold_gray_level"] == 10
    assert mask["foreground_fraction"] == pytest.approx(1 / 3, abs=1e-6)
    assert np.array_equal(
        np.asarray(Image.open(mask["output_path"])),
        np.array([[0, 0, 0], [0, 255, 255]], dtype=np.uint8),
    )
    assert source.read_bytes() == before


def test_flat_image_has_no_foreground(tmp_path: Path) -> None:
    """Uniform images have no meaningful Otsu split."""
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    Image.fromarray(np.full((4, 4), 80, dtype=np.uint8)).save(
        image_dir / "flat.png",
    )
    result = _data(
        ImageTools(image_dir, tmp_path / "out").segment_otsu("flat.png"),
    )
    assert result["threshold_gray_level"] is None
    assert result["foreground_fraction"] == 0
    assert not np.asarray(Image.open(result["output_path"])).any()


def test_rejects_out_of_scope_names_and_bad_roi(tmp_path: Path) -> None:
    """A tool cannot read outside its input directory or use an invalid ROI."""
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    Image.fromarray(np.zeros((2, 2), dtype=np.uint8)).save(
        image_dir / "small.png",
    )
    tools = ImageTools(image_dir, tmp_path / "out")
    with pytest.raises(ValueError, match="filename"):
        tools.inspect_image("../small.png")
    with pytest.raises(ValueError, match="outside"):
        tools.inspect_image("small.png", roi_width=3, roi_height=2)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("approve", [True, False])
def test_real_agent_asks_before_writing(tmp_path: Path, approve: bool) -> None:
    """The actual AgentScope reply flow gates the side-effecting tool."""
    report = asyncio.run(run_demo(tmp_path, approve))
    assert report["confirmations"] == [
        {
            "tool": "segment_otsu",
            "approved": approve,
            "outputs_before_decision": 0,
        },
    ]
    assert report["results"][0]["state"] == "success"
    assert report["results"][0]["output"]["gray_mean"] == 72.5
    assert report["results"][2]["state"] == "error"
    assert "missing.png" in report["results"][2]["output"]
    assert len(report["output_files"]) == int(approve)
    if approve:
        output = report["results"][1]["output"]
        assert output["threshold_gray_level"] == 30
        assert output["foreground_fraction"] == 0.25
        assert Path(output["output_path"]).is_file()
    else:
        assert report["results"][1]["state"] != "success"
