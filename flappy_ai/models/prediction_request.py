from typing import List

import attr


@attr.s(auto_attribs=True)
class PredictionRequest:
    data: any
    no_random: bool = attr.ib(default=False)
