# -*- coding: utf-8 -*-
"""The structured output models for werewolf game."""
from typing import Literal

from pydantic import BaseModel, Field

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

        vote: Literal[tuple(_.name for _ in agents) + ("弃票",)] = Field(  # type: ignore
            description="The name of the player you want to vote for, or '弃票' to abstain",
        )

    return VoteModel


def get_werewolf_vote_model(agents: list[AgentBase]) -> type[BaseModel]:
    """Get the werewolf vote model by player names."""

    class WerewolfVoteModel(BaseModel):
        """The werewolf vote output format."""

        vote: Literal[tuple(_.name for _ in agents) + ("空刀",)] = Field(  # type: ignore
            description="The name of the player you want to kill, or '空刀' to skip killing",
        )

    return WerewolfVoteModel


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

    return WitchPoisonModel


def get_seer_model(agents: list[AgentBase]) -> type[BaseModel]:
    """Get the seer model by player names."""

    class SeerModel(BaseModel):
        """The output format for seer action."""

        name: Literal[tuple(_.name for _ in agents)] = Field(  # type: ignore
            description="The name of the player you want to check",
        )

    return SeerModel

def get_guard_model(agents: list[AgentBase]) -> type[BaseModel]:
    """Get the guard model by player names."""

    class GuardModel(BaseModel):
        """The output format for guard action."""

        name: Literal[tuple(_.name for _ in agents) + ("空守",)] = Field(  # type: ignore
            description="The name of the player you want to guard, or '空守' to skip guarding",
        )

    return GuardModel


def get_sheriff_election_model(candidates: list[AgentBase]) -> type[BaseModel]:
    """Get the sheriff election model by candidate names."""

    class SheriffElectionModel(BaseModel):
        """The output format for sheriff election."""

        vote: Literal[tuple(_.name for _ in candidates) + ("弃票",)] = Field(  # type: ignore
            description="The name of the candidate you want to vote for, or '弃票' to abstain",
        )

    return SheriffElectionModel


def get_sheriff_badge_transfer_model(agents: list[AgentBase]) -> type[BaseModel]:
    """Get the sheriff badge transfer model by player names."""

    class SheriffBadgeTransferModel(BaseModel):
        """The output format for sheriff badge transfer."""

        name: Literal[tuple(_.name for _ in agents) + ("撕警徽",)] = Field(  # type: ignore
            description="The name of the player to transfer the badge to, or '撕警徽' to destroy the badge",
        )

    return SheriffBadgeTransferModel


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

    return HunterModel


def get_self_explode_model() -> type[BaseModel]:
    """Get the self explode model."""
    class SelfExplodeModel(BaseModel):
        """The output format for self explode."""
        self_explode: bool = Field(
            description="Whether you want to self explode (only werewolves can do this, wolf king cannot)",
        )

    return SelfExplodeModel


def get_withdraw_model() -> type[BaseModel]:
    """Get the withdraw model."""
    class WithdrawModel(BaseModel):
        """The output format for withdraw."""
        withdraw: bool = Field(
            description="Whether you want to withdraw from sheriff election",
        )

    return WithdrawModel
