# -*- coding: utf-8 -*-
"""The structured output models used in the werewolf game."""
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from agentscope.agent import AgentBase


class DiscussionModel(BaseModel):
    """The output format for discussion."""

    reach_agreement: bool = Field(
        description="Whether you have reached an agreement or not",
    )


def get_vote_model(agents: list[AgentBase]) -> type[BaseModel]:
    """Get the vote model by player names."""

    class VoteModel(BaseModel):
        """The vote output format."""

        vote: Literal[tuple(_.name for _ in agents)] = Field(  # type: ignore
            description="The name of the player you want to vote for",
        )

    return VoteModel


class WitchResurrectModel(BaseModel):
    """The output format for witch resurrect action."""

    resurrect: bool = Field(
        description="Whether you want to resurrect the player",
    )


def get_poison_model(agents: list[AgentBase]) -> type[BaseModel]:
    """Get the poison model by player names."""

    class WitchPoisonModel(BaseModel):
        """The output format for witch poison action."""

        poison: bool = Field(
            description="Do you want to use the poison potion",
        )
        name: Literal[  # type: ignore
            tuple(_.name for _ in agents)
        ] | None = Field(
            description="The name of the player you want to poison, if you "
            "don't want to poison anyone, just leave it empty",
            default=None,
        )

        # pylint: disable=no-self-argument
        @model_validator(mode="before")
        def clear_name_if_no_poison(cls, values: dict) -> dict:
            """Clear name if no poison is used.
            This is to avoid validation errors when `poison` is false but a
             `name` is provided.

            Args:
                values (`dict`):
                    The input data for the model.
            Returns:
                `dict`:
                    The validated and possibly modified data.
            """
            if isinstance(values, dict) and not values.get("poison"):
                values["name"] = None
            return values

    return WitchPoisonModel


def get_seer_model(agents: list[AgentBase]) -> type[BaseModel]:
    """Get the seer model by player names."""

    class SeerModel(BaseModel):
        """The output format for seer action."""

        name: Literal[tuple(_.name for _ in agents)] = Field(  # type: ignore
            description="The name of the player you want to check",
        )

    return SeerModel


def get_hunter_model(agents: list[AgentBase]) -> type[BaseModel]:
    """Get the hunter model by player agents."""

    class HunterModel(BaseModel):
        """The output format for hunter action."""

        shoot: bool = Field(
            description="Whether you want to use the shooting ability or not",
        )
        name: Literal[  # type: ignore
            tuple(_.name for _ in agents)
        ] | None = Field(
            description="The name of the player you want to shoot, if you "
            "don't want to the ability, just leave it empty",
            default=None,
        )

        # pylint: disable=no-self-argument
        @model_validator(mode="before")
        def clear_name_if_no_shoot(cls, values: dict) -> dict:
            """If shoot is false, set name to None to skip validation.
            This is to avoid validation errors when `shoot` is false but a
             `name` is provided.

            Args:
                values (`dict`):
                    The input data for the model.
            Returns:
                `dict`:
                    The validated and possibly modified data.
            """
            if isinstance(values, dict) and not values.get("shoot"):
                values["name"] = None
            return values

    return HunterModel
