# -*- coding: utf-8 -*-
"""A board agent class that can host a Gomoku game, and a function to
convert the board to an image."""
from typing import Optional, Union, Sequence

import numpy as np
from matplotlib import pyplot as plt, patches

from agentscope.message import Msg
from agentscope.agent import AgentBase


CURRENT_BOARD_PROMPT_TEMPLATE = """The current board is as follows:
{board}
{player}, it's your turn."""

NAME_BLACK = "Alice"
NAME_WHITE = "Bob"

# The mapping from name to piece
NAME_TO_PIECE = {
    NAME_BLACK: "o",
    NAME_WHITE: "x",
}

EMPTY_PIECE = "0"


def board2img(board: np.ndarray, save_path: str) -> str:
    """Convert the board to an image and save it to the specified path."""

    size = board.shape[0]
    fig, ax = plt.subplots(figsize=(10, 10))
    ax.set_xlim(0, size - 1)
    ax.set_ylim(0, size - 1)

    for i in range(size):
        ax.axhline(i, color="black", linewidth=1)
        ax.axvline(i, color="black", linewidth=1)

    for y in range(size):
        for x in range(size):
            if board[y, x] == NAME_TO_PIECE[NAME_BLACK]:  # black player
                circle = patches.Circle(
                    (x, y),
                    0.45,
                    edgecolor="black",
                    facecolor="black",
                    zorder=10,
                )
                ax.add_patch(circle)
            elif board[y, x] == NAME_TO_PIECE[NAME_WHITE]:  # white player
                circle = patches.Circle(
                    (x, y),
                    0.45,
                    edgecolor="black",
                    facecolor="white",
                    zorder=10,
                )
                ax.add_patch(circle)
    # Hide the axes and invert the y-axis
    ax.set_xticks(range(size))
    ax.set_yticks(range(size))
    ax.set_xticklabels(range(size))
    ax.set_yticklabels(range(size))
    ax.invert_yaxis()
    plt.savefig(save_path, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)  # Close the figure to free memory
    return save_path


class BoardAgent(AgentBase):
    """A board agent that can host a Gomoku game."""

    def __init__(self, name: str) -> None:
        super().__init__(name=name, use_memory=False)

        # Init the board
        self.size = 15
        self.board = np.full((self.size, self.size), EMPTY_PIECE)

        # Record the status of the game
        self.game_end = False

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        if x is None:
            # Beginning of the game
            content = (
                "Welcome to the Gomoku game! Black player goes first. "
                "Please make your move."
            )
        else:
            row, col = x.content

            self.assert_valid_move(row, col)

            # change the board
            self.board[row, col] = NAME_TO_PIECE[x.name]

            # check if the game ends
            if self.check_draw():
                content = "The game ends in a draw!"
                self.game_end = True
            else:
                next_player_name = (
                    NAME_BLACK if x.name == NAME_WHITE else NAME_WHITE
                )
                content = CURRENT_BOARD_PROMPT_TEMPLATE.format(
                    board=self.board2text(),
                    player=next_player_name,
                )

                if self.check_win(row, col, NAME_TO_PIECE[x.name]):
                    content = f"The game ends, {x.name} wins!"
                    self.game_end = True

        msg_host = Msg(self.name, content, role="assistant")
        self.speak(msg_host)

        # Note: we disable the image display here to avoid too many images
        # img = plt.imread(board2img(self.board, 'current_board.png'))
        # plt.imshow(img)
        # plt.axis('off')
        # plt.show()

        return msg_host

    def assert_valid_move(self, x: int, y: int) -> None:
        """Check if the move is valid."""
        if not (0 <= x < self.size and 0 <= y < self.size):
            raise RuntimeError(f"Invalid move: {[x, y]} out of board range.")

        if not self.board[x, y] == EMPTY_PIECE:
            raise RuntimeError(
                f"Invalid move: {[x, y]} is already "
                f"occupied by {self.board[x, y]}.",
            )

    def check_win(self, x: int, y: int, piece: str) -> bool:
        """Check if the player wins the game."""
        xline = self._check_line(self.board[x, :], piece)
        yline = self._check_line(self.board[:, y], piece)
        diag1 = self._check_line(np.diag(self.board, y - x), piece)
        diag2 = self._check_line(
            np.diag(np.fliplr(self.board), self.size - 1 - x - y),
            piece,
        )
        return xline or yline or diag1 or diag2

    def check_draw(self) -> bool:
        """Check if the game ends in a draw."""
        return np.all(self.board != EMPTY_PIECE)

    def board2text(self) -> str:
        """Convert the board to a text representation."""
        return "\n".join(
            [
                str(_)[1:-1].replace("'", "").replace(" ", "")
                for _ in self.board
            ],
        )

    def _check_line(self, line: np.ndarray, piece: str) -> bool:
        """Check if the player wins in a line."""
        count = 0
        for i in line:
            if i == piece:
                count += 1
                if count == 5:
                    return True
            else:
                count = 0
        return False
