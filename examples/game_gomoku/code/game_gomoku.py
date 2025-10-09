# -*- coding: utf-8 -*-
"""The main script to start a Gomoku game between two agent and a board
agent."""

from board_agent import NAME_TO_PIECE, NAME_BLACK, NAME_WHITE, BoardAgent
from gomoku_agent import GomokuAgent

from agentscope import msghub

import agentscope


MAX_STEPS = 10

SYS_PROMPT_TEMPLATE = """
You're a skillful Gomoku player. You should play against your opponent according to the following rules:

Game Rules:
1. This Gomoku board is a 15*15 grid. Moves are made by specifying row and column indexes, with [0, 0] marking the top-left corner and [14, 14] indicating the bottom-right corner.
2. The goal is to be the first player to form an unbroken line of your pieces horizontally, vertically, or diagonally.
3. If the board is completely filled with pieces and no player has formed a row of five, the game is declared a draw.

Note:
1. Your pieces are represented by '{}', your opponent's by '{}'. 0 represents an empty spot on the board.
2. You should think carefully about your strategy and moves, considering both your and your opponent's subsequent moves.
3. Make sure you don't place your piece on a spot that has already been occupied.
4. Only an unbroken line of five same pieces will win the game. For example, "xxxoxx" won't be considered a win.
5. Note the unbroken line can be formed in any direction: horizontal, vertical, or diagonal.
"""  # noqa

# Prepare the model configuration
YOUR_MODEL_CONFIGURATION_NAME = "{YOUR_MODEL_CONFIGURATION_NAME}"
YOUR_MODEL_CONFIGURATION = {
    "config_name": YOUR_MODEL_CONFIGURATION_NAME,
    # ...
}

# Initialize the agent
agentscope.init(model_configs=YOUR_MODEL_CONFIGURATION)

piece_black = NAME_TO_PIECE[NAME_BLACK]
piece_white = NAME_TO_PIECE[NAME_WHITE]

black = GomokuAgent(
    NAME_BLACK,
    model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
    sys_prompt=SYS_PROMPT_TEMPLATE.format(piece_black, piece_white),
)

white = GomokuAgent(
    NAME_WHITE,
    model_config_name=YOUR_MODEL_CONFIGURATION_NAME,
    sys_prompt=SYS_PROMPT_TEMPLATE.format(piece_white, piece_black),
)

board = BoardAgent(name="Host")

# Start the game!
msg = None
i = 0
# Use a msg hub to share conversation between two players, e.g. white player
# can hear what black player says to the board
with msghub(participants=[black, white, board]):
    while not board.game_end and i < MAX_STEPS:
        for player in [black, white]:
            # receive the move from the player, judge if the game ends and
            # remind the player to make a move
            msg = board(msg)

            # end the game if draw or win
            if board.game_end:
                break

            # make a move
            msg = player(msg)

            i += 1
