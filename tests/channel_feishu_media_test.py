# -*- coding: utf-8 -*-
"""Regression tests for the resource key of a Feishu video message."""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from agentscope.app.channel._feishu._channel import FeishuChannel
from agentscope.message import DataBlock, Base64Source


class FeishuMediaResourceKeyTest(IsolatedAsyncioTestCase):
    """A video is downloaded from its file key, not from its cover."""

    def setUp(self) -> None:
        self.channel = FeishuChannel(
            "c",
            FeishuChannel.Credentials(app_id="a", app_secret="s"),
            FeishuChannel.Config(),
        )
        self.requests: list[tuple[str, str]] = []
        # pylint: disable-next=protected-access
        self.channel._download_resource = self._record_download

    async def _record_download(
        self,
        message_id: str,
        key: str,
        resource_type: str,
        default_mime: str,
        name: str,
    ) -> DataBlock:
        """Record the requested resource and return a stand-in block."""
        del message_id, name
        self.requests.append((key, resource_type))
        return DataBlock(
            source=Base64Source(data="Ymlu", media_type=default_mime),
        )

    async def test_video_message_downloads_the_video_file(self) -> None:
        """``media`` content holds both keys; the video is the file key."""
        message = SimpleNamespace(
            message_id="m1",
            content=(
                '{"file_key": "file-video", "image_key": "img-cover", '
                '"file_name": "clip.mp4"}'
            ),
        )

        await self.channel._download_media(  # pylint: disable=protected-access
            message,
            "media",
        )

        self.assertEqual(self.requests, [("file-video", "file")])

    async def test_image_message_still_downloads_its_image(self) -> None:
        """The fix does not change how an image message is fetched."""
        message = SimpleNamespace(
            message_id="m2",
            content='{"image_key": "img-1"}',
        )

        await self.channel._download_media(  # pylint: disable=protected-access
            message,
            "image",
        )

        self.assertEqual(self.requests, [("img-1", "image")])

    async def test_post_video_element_downloads_the_video_file(self) -> None:
        """A rich-text ``media`` element is downloaded like a video."""
        post = {
            "content": [
                [
                    {
                        "tag": "media",
                        "file_key": "post-video",
                        "image_key": "post-cover",
                    },
                ],
            ],
        }

        # pylint: disable-next=protected-access
        await self.channel._parse_post(post, "m3")

        self.assertEqual(self.requests, [("post-video", "file")])
