# -*- coding: utf-8 -*-
"""Repaint a white-model render, through Wan 3.0 on Model Studio.

This lives in the example rather than in AgentScope on purpose: it is one
vendor's media API, pinned to one model, and there are several equivalent
ones. The framework owns the step that calls it, not the endpoint.
"""
import asyncio
import os
import urllib.request
from http import HTTPStatus

import requests
from dashscope import VideoSynthesis

VIDEO_MODEL = "wan3.0-video"


def _upload(video_path: str, api_key: str) -> str:
    """Put a local file in Model Studio's temporary space, as an
    ``oss://`` URL good for 48 hours.

    A reference video has to be reachable by the service: unlike an
    image it cannot be inlined as base64, so a local render has to go
    somewhere first. The upload is bound to the model that will read it.
    """
    policy = requests.get(
        "https://dashscope.aliyuncs.com/api/v1/uploads",
        params={"action": "getPolicy", "model": VIDEO_MODEL},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    ).json()["data"]

    name = os.path.basename(video_path)
    key = f"{policy['upload_dir']}/{name}"
    with open(video_path, "rb") as handle:
        response = requests.post(
            policy["upload_host"],
            files={
                "OSSAccessKeyId": (None, policy["oss_access_key_id"]),
                "Signature": (None, policy["signature"]),
                "policy": (None, policy["policy"]),
                "x-oss-object-acl": (None, policy["x_oss_object_acl"]),
                "x-oss-forbid-overwrite": (
                    None,
                    policy["x_oss_forbid_overwrite"],
                ),
                "key": (None, key),
                "success_action_status": (None, "200"),
                "file": (name, handle),
            },
            timeout=600,
        )
    response.raise_for_status()
    return f"oss://{key}"


async def restyle_video(video_path: str, look: str) -> str:
    """Paint a white-model render in a described look, and return the
    path of the result.

    The render goes in as a reference video, so its motion, timing and
    framing are what come back; the look is what changes.

    Args:
        video_path (`str`):
            Absolute path to the render. At most 15 seconds, mp4 or mov,
            at least 16 fps — the model's own limits on a reference.
        look (`str`):
            What it should look like, e.g. ``"把整个画面改成 1990 年代
            手绘体育动画，赛璐璐上色，胶片颗粒"``.
    """
    api_key = os.environ["DASHSCOPE_API_KEY"]

    def run() -> str:
        task = VideoSynthesis.async_call(
            api_key=api_key,
            model=VIDEO_MODEL,
            prompt=look,
            media=[
                {
                    "type": "reference_video",
                    "url": _upload(video_path, api_key),
                },
            ],
            resolution="720P",
            ratio="adaptive",
            # Let the model match the render; the default of 5 seconds
            # would cut a longer one short.
            duration=-1,
            prompt_extend=True,
        )
        done = VideoSynthesis.wait(task=task, api_key=api_key)
        if done.status_code != HTTPStatus.OK:
            raise RuntimeError(f"{done.code}: {done.message}")
        out = f"{os.path.splitext(video_path)[0]}.styled.mp4"
        urllib.request.urlretrieve(done.output.video_url, out)
        return out

    return await asyncio.to_thread(run)
