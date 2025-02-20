from typing import List

import attr

from flappy_ai.models.game_data import GameData


@attr.s(auto_attribs=True)
class EpisodeResult:
    """
    The data for our game session.
    """

    game_data: GameData
