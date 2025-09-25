# -*- coding: utf-8 -*-
""" Python web digest test."""

import unittest
from typing import Union, Sequence, List
from unittest.mock import patch, MagicMock, Mock

from agentscope.service import ServiceResponse
from agentscope.service import load_web, digest_webpage
from agentscope.service.service_status import ServiceExecStatus
from agentscope.model import ModelWrapperBase, ModelResponse
from agentscope.message import Msg


class TestWebDigest(unittest.TestCase):
    """Tests for web loading and digesting."""

    @patch("requests.get")
    def test_web_load(self, mock_get: MagicMock) -> None:
        """test web_load function loading html"""
        # Set up the mock response
        mock_response = Mock()
        mock_return_text = """
            <!DOCTYPE html>
            <html>
            <body>
                <div>
                    <h1>Hello World!</h1>
                    <div>
                        Testing!
                        <p>
                        Some intro text about Foo. <a href="xxx">Examples</a>
                        <ol>
                        <li>Test list.</li>
                        </ol>
                      </div>
                </div>
                <ol>
                    <li>Test list again.</li>
                </ol>
            </body>
            </html>
        """

        expected_result = ServiceResponse(
            status=ServiceExecStatus.SUCCESS,
            content={
                "raw": bytes(mock_return_text, "utf-8"),
                "html_to_text": "Hello World! Testing! Some intro text "
                "about Foo. Test list.Test list again.",
            },
        )

        mock_response.text = mock_return_text
        mock_response.content = bytes(mock_return_text, "utf-8")
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_get.return_value = mock_response

        # set parameters
        fake_url = "fake-url"

        results = load_web(
            url=fake_url,
            keep_raw=True,
            html_selected_tags=["p", "div", "h1", "li"],
        )
        self.assertEqual(
            results,
            expected_result,
        )

    def test_web_digest(self) -> None:
        """test web_digest function"""

        # test the case with dummy model
        class DummyModel(ModelWrapperBase):
            """Dummy model for testing"""

            def __init__(self) -> None:
                self.max_length = 1000

            def __call__(self, messages: list[Msg]) -> ModelResponse:
                return ModelResponse(text="model return")

            def format(
                self,
                *args: Union[Msg, Sequence[Msg], None],
            ) -> Union[List[dict], str]:
                return str(args)

        dummy_model = DummyModel()
        response = digest_webpage("testing", dummy_model)
        expected_result = ServiceResponse(
            status=ServiceExecStatus.SUCCESS,
            content="model return",
        )
        self.assertEqual(
            response,
            expected_result,
        )


# This allows the tests to be run from the command line
if __name__ == "__main__":
    unittest.main()
