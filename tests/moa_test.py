# -*- coding: utf-8 -*-
"""
Unit tests for MixtureOfAgents strategy
"""
import unittest
from unittest.mock import MagicMock, patch
from agentscope.message import Msg
from agentscope.model import ModelWrapperBase
from agentscope.strategy import MixtureOfAgents


class TestMixtureOfAgents(unittest.TestCase):
    """
    Test class for MixtureOfAgents strategy.
    """

    def setUp(self) -> None:
        """
        Set up the test environment.
        """
        # Mock main model and reference model
        self.mock_main_model = MagicMock(spec=ModelWrapperBase)
        self.mock_ref_model_1 = MagicMock(spec=ModelWrapperBase)
        self.mock_ref_model_2 = MagicMock(spec=ModelWrapperBase)

        # Mocking the format and call behavior of the model
        self.mock_main_model.format.return_value = "formatted_main_msg"
        self.mock_main_model.return_value.text = "main_model_response"

        self.mock_ref_model_1.format.return_value = "formatted_ref_msg_1"
        self.mock_ref_model_1.return_value.text = "ref_model_response_1"

        self.mock_ref_model_2.format.return_value = "formatted_ref_msg_2"
        self.mock_ref_model_2.return_value.text = "ref_model_response_2"

        self.models = [self.mock_ref_model_1, "load_mock_ref_model2"]
        self.rounds = 2

    @patch("agentscope.manager.ModelManager.get_model_by_config_name")
    def test_mixture_of_agents_call(self, mock_load_model: MagicMock) -> None:
        """
        Test the call method of MixtureOfAgents.
        """
        # Mock load_model_by_config_name, init model with str
        mock_load_model.return_value = self.mock_ref_model_2

        mixture = MixtureOfAgents(
            main_model=self.mock_main_model,
            reference_models=self.models,
            rounds=self.rounds,
            show_internal=False,
        )

        msg = Msg(
            role="user",
            content="Hello, what is the weather today?",
            name="user",
        )

        result = mixture(msg)

        self.mock_ref_model_1.format.assert_called()
        self.mock_ref_model_2.format.assert_called()

        self.assertEqual(self.mock_ref_model_1.call_count, self.rounds + 1)
        self.assertEqual(self.mock_ref_model_2.call_count, self.rounds + 1)

        self.mock_main_model.format.assert_called_once()
        self.mock_main_model.assert_called_once()

        self.assertEqual(result, "main_model_response")


if __name__ == "__main__":
    unittest.main()
