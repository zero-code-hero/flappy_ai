import json
import multiprocessing
import time
from typing import List

from structlog import get_logger

from flappy_ai.models import EpisodeResult, PredictionRequest
from flappy_ai.models.game_process import GameProcess
from flappy_ai.models.keras_process import KerasProcess
from flappy_ai.types.network_types import NetworkTypes
from flappy_ai.models.sql_models.saved_episode_result import SavedEpisodeResult
from flappy_ai import Session

logger = get_logger(__name__)

MAX_CLIENTS = 1
CLIENTS: List[GameProcess] = []
KERAS_PROCESS = None

# https://towardsdatascience.com/epoch-vs-iterations-vs-batch-size-4dfb9c7ce9c9
EPISODES = 30000  # TODO, figure out a optimal number

if __name__ == "__main__":
    session = Session()
    result = session.query(SavedEpisodeResult).order_by(SavedEpisodeResult.episode_number.desc()).first()
    if not result:
        CURRENT_EPISODES = 0
        COMPLETED_EPISODES = 0
    else:
        CURRENT_EPISODES = result.episode_number
        COMPLETED_EPISODES = result.episode_number
        logger.debug("Loaded episode info.", starting_episode=CURRENT_EPISODES)

    KERAS_PROCESS = KerasProcess()
    KERAS_PROCESS.start(network_type=NetworkTypes.DQN)
    # Give the keras process time to spin up, load models, etc.
    time.sleep(20)
    last_update = time.time()
    EPISODE_RESULTS: List[EpisodeResult] = []

    while True:
        if not KERAS_PROCESS.is_alive():
            raise Exception("Keras process died.")

        # Calls join on completed processes but does not block. =)
        multiprocessing.active_children()

        for client in CLIENTS:
            if client.parent_pipe and client.parent_pipe.poll():
                # Queue is FIFO
                # we may need to toss data if we get too slow?
                request = client.parent_pipe.recv()
                if isinstance(request, PredictionRequest):
                    KERAS_PROCESS.parent_pipe.send(request)
                    client.parent_pipe.send(KERAS_PROCESS.parent_pipe.recv())
                elif isinstance(request, EpisodeResult):
                    # The end result of the session
                    # Currently I consider set of GameData to be a batch size of one.
                    # This may be over training, idk
                    EPISODE_RESULTS.append(request)
                    COMPLETED_EPISODES += 1

        # Prune off any completed clients
        CLIENTS = [x for x in CLIENTS if x.is_alive()]

        if (time.time() - last_update) / 60 > 5:
            last_update = time.time()
            # Only print updates and save every 5 minutes
            logger.debug("UPDATE", target_episodes=EPISODES, completed_episodes=COMPLETED_EPISODES)

        # Do the batch training after all the clients have completed
        # Maybe I need to abstract the training out to it's own process?
        if not CLIENTS:
            session = Session()
            session.bulk_save_objects(
                [SavedEpisodeResult(episode_number=x.game_data.episode_number, score=x.game_data.score) for x in EPISODE_RESULTS]
            )
            session.commit()

            while EPISODE_RESULTS:
                KERAS_PROCESS.parent_pipe.send(EPISODE_RESULTS.pop())

                # We just want to make sure that we're done training before moving on.
                KERAS_PROCESS.parent_pipe.recv()

        # If we are still below the targets interations, refill the clients and continue
        if COMPLETED_EPISODES >= EPISODES:
            if CLIENTS:
                continue
            else:
                break
        elif COMPLETED_EPISODES < EPISODES and not CLIENTS:
            while len(CLIENTS) < MAX_CLIENTS:
                # Wrong place for this.
                CURRENT_EPISODES += 1
                c = GameProcess()
                CLIENTS.append(c)
                c.start(episode_number=CURRENT_EPISODES)
