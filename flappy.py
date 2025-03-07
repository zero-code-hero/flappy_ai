import json
import random
import time
from typing import List

import matplotlib.pyplot as plt
import numpy as np
import tensorflow as tf
from cattr import structure, unstructure
from keras.layers import (BatchNormalization, Conv2D, Dense, Flatten, Input,
                          Lambda)
from keras.models import Sequential
from keras.optimizers import RMSprop
from structlog import get_logger

from flappy_ai.models.game import Game
from flappy_ai.models.game_data import GameData
from flappy_ai.models.game_history import GameHistory
from flappy_ai.models.memory_item import MemoryItem

logger = get_logger(__name__)
config = tf.ConfigProto()
config.gpu_options.allow_growth = True
session = tf.Session(config=config)

EPISODES = 100000


class DQNAgent:
    def __init__(self, state_size, action_size):
        self.data_shape = (159, 81, 1)
        self.state_size = state_size
        self.action_size = action_size
        self.memory = GameHistory(size=100000)
        self.gamma = 0.99  # discount rate
        # self.epsilon = 1.0  # exploration rate
        """
        E is best ot start at 1 but we dont want the bird to flap too much.
        # see https://github.com/yenchenlin/DeepLearningFlappyBird
        """
        """
        In these experiments, we used the RMSProp algorithm with minibatches of size 32.  
        The behaviorpolicy during training was-greedy withannealed linearly from1to0.
        1over the first million frames, and fixed at0.1thereafter. 
         We trained for a total of10million frames and used a replay
         memory of one million most recent frames.
        """
        self.start_epsilon = 1
        self.epsilon = self.start_epsilon
        self.epsilon_min = 0.1
        self.explore_rate = 200000
        # self.observe_rate = 100000 # frames before we start training.
        self.observe_rate = 10000  # frames before we start training.
        self.learning_rate = 0.001
        self.model = self._build_model()
        self.loss_history: List[float] = []
        self.acc_history: List[float] = []

    def _build_model(self):

        # Deepmind paper on their atari breakout agent.
        # https://arxiv.org/pdf/1312.5602v1.pdf

        # With the functional API we need to define the inputs.
        frames_input = Input(self.data_shape, name="frames")

        # Assuming that the input frames are still encoded from 0 to 255. Transforming to [0, 1].
        normalized = Lambda(lambda x: x / 255.0)(frames_input)

        model = Sequential()
        model.add(BatchNormalization(input_shape=self.data_shape))
        model.add(Conv2D(16, 8, strides=(4, 4), padding="valid", activation="relu"))
        model.add(Conv2D(32, 4, strides=(2, 2), padding="valid", activation="relu"))
        # model.add(Conv2D(64, 3, strides=(1, 1), padding='valid', activation='relu'))
        model.add(Flatten())
        model.add(Dense(256, activation="relu"))
        model.add(Dense(self.action_size))
        # Info on opts
        # http://ruder.io/optimizing-gradient-descent/
        opt = RMSprop(lr=self.learning_rate)
        model.compile(loss="mean_squared_error", optimizer=opt, metrics=["accuracy"])

        return model

    def act(self, state):
        """
        Note for later, predict expects and returns an array of items.
        so it wants an array of states, even if we only have 1 it still needs to be in a shape of (1, x, x, x)
        https://stackoverflow.com/questions/41563720/error-when-checking-model-input-expected-convolution2d-input-1-to-have-4-dimens
        state.shape
        (159, 81, 1)
        np.expand_dims(state, axis=0).shape
        (1, 159, 81, 1)
        """
        act_values = self.model.predict(np.expand_dims(state, axis=0))
        # act_values -> array([[ -3.0126321, -11.75323  ]], dtype=float32)

        random_action = False
        if np.random.rand() <= self.epsilon:
            random_action = True
            action = random.randrange(2)
        else:
            action = np.argmax(act_values[0])  # returns action

        logger.debug(
            "[act]", predicted_actions=act_values.tolist(), using_random_action=random_action, chosen_action=action
        )

        return action

    # def fit_batch(self, start_states, actions, rewards, next_states, is_terminal):
    def fit_batch(self, batch_games: List[GameData]):
        """Do one deep Q learning iteration.

        Params:
        - model: The DQN
        - gamma: Discount factor (should be 0.99)
        - start_states: numpy array of starting states
        - actions: numpy array of one-hot encoded actions corresponding to the start states
        - rewards: numpy array of rewards corresponding to the start states and actions
        - next_states: numpy array of the resulting states corresponding to the start states and actions
        - is_terminal: numpy boolean array of whether the resulting state is terminal

        If yoy wish to see the images at this level you can use
        plt.imshow(start_states[0][:,:,0], cmap=plt.cm.binary)
        though the colors will be fucked
        """
        for game_data in batch_games:
            # we're offset by one here as the finished state for one is the start state for the next, I think.
            # start_states = np.array([x.merged_state for x in game_data][:-1])
            start_states = np.array([x.state for x in game_data][:-1])
            actions = np.array([x.action for x in game_data][:-1])
            rewards = np.array([x.reward for x in game_data][:-1])
            # next_states = np.array([x.merged_state for x in game_data][1:])
            next_states = np.array([x.state for x in game_data][1:])
            is_terminal = np.array([x.is_terminal for x in game_data][:-1])

            # First, predict the Q values of the next states.
            next_Q_values = self.model.predict(next_states)
            # The Q values of the terminal states is 0 by definition, so override them
            next_Q_values[is_terminal] = 0
            # The Q values of each start state is the reward + gamma * the max next state Q value
            Q_values = rewards + self.gamma * np.max(next_Q_values, axis=1)
            # Fit the keras model. Note how we are passing the actions as the mask and multiplying
            # the targets by the actions.
            history = self.model.fit(
                x=start_states, y=actions * Q_values[:, None], epochs=1, batch_size=len(start_states), verbose=0
            )
            self.loss_history.append(history.history["loss"])
            self.acc_history.append(history.history["acc"])
            # if self.epsilon > self.epsilon_min:
        #    self.epsilon *= self.epsilon_decay

    def load(self):
        try:
            self.model.load_weights("save/flappy.h5")
        except OSError as e:
            logger.warn("Unable to load saved weights.")

        try:
            with open("save/data.json", "r") as file:
                data = json.loads(file.read())
                self.epsilon = data["epsilon"]
                self.loss_history = data["loss_history"]
                self.acc_history = data["acc_history"]
        except FileNotFoundError as e:
            logger.warn("Unable to load saved memory.")

    def save(self):
        self.model.save_weights("save/flappy.h5")
        with open("save/data.json", "w+") as file:
            file.write(
                json.dumps(
                    {"epsilon": self.epsilon, "loss_history": self.loss_history, "acc_history": self.acc_history},
                    indent=4,
                )
            )

    def display_data(self):
        # list all data in history
        # summarize history for accuracy
        plt.plot(self.acc_history)
        plt.title("Accuracy")
        plt.ylabel("accuracy")
        plt.show()
        # summarize history for loss
        plt.plot(history.history["loss"])
        plt.title("model loss")
        plt.plot(self.loss_history)
        plt.ylabel("loss")
        plt.show()


if __name__ == "__main__":
    # env = gym.make('CartPole-v1')
    env = Game()
    env.start_game()

    state_size = None  # env.observation_space.shape[0]
    action_size = env.actions
    agent = DQNAgent(state_size, action_size)
    agent.load()
    done = False
    BATCH_SIZE = 32
    LOOK_BACK_FRAMES = 4
    FRAME_COUNT = 0

    for e in range(EPISODES):
        game_data = GameData(movement_frames=LOOK_BACK_FRAMES)
        time.sleep(3)
        env.reset()
        done = False
        frames = 0
        start_time = time.time()
        while not done:
            if time.time() - start_time < 0.2:
                continue
            else:
                start_time = time.time()
                state, reward, done = env.step(0)

                item = MemoryItem(state=state, action=[1, 0], next_state=None)
                game_data.append(item)

            if len(game_data) > 0:
                state, _, _ = env.step(0)
            else:
                state = game_data[1].state

            action = agent.act(np.array(state))
            next_state, reward, done = env.step(action)
            # cv2.imwrite(f"tmp/{game_data.total_frames()}.png", next_state)

            # The reward goes back one memory item since that is the action that created it.
            # same wth the terminal state.
            if len(game_data) > 0:
                game_data[-1].reward = reward
                game_data[-1].is_terminal = done
                game_data[-1].next_state = state

            game_data.score += reward
            if action == 0:
                action = [1, 0]
            else:
                action = [0, 1]

            game_data.append(MemoryItem(state=next_state, action=action))

            # causal image reshaping and merging
            # 1: get the past 4 images
            # 2: average them into a single image.
            # 3: Reshape for keras 2d.
            # agent.remember(
            #    np.reshape(np.mean(np.array(local_screen_history[-8:-4]), axis=0), (x, y, 1)),
            #    action,
            #    reward,
            #    np.reshape(np.mean(np.array(local_screen_history[-4:]), axis=0), (x, y, 1)),
            #    done
            # )

            # update epsilon
            if agent.epsilon > agent.epsilon_min and FRAME_COUNT > agent.observe_rate:
                agent.epsilon -= (agent.start_epsilon - agent.epsilon_min) / agent.explore_rate
            # if agent.epsilon > agent.epsilon_min and frame_count > agent.observe_rate:
            #    agent.epsilon *= agent.epsilon_decay

        if len(game_data) > 10:
            # Toss out very short games.
            agent.memory.append(game_data=game_data)

        # Minibatch training.
        if FRAME_COUNT > agent.observe_rate:
            if len(agent.memory) > int(BATCH_SIZE):
                history = agent.fit_batch(agent.memory.get_sample_batch(batch_size=BATCH_SIZE))
                agent.save()

        FRAME_COUNT += game_data.total_frames()
        logger.debug(
            "Finished EPISODE.",
            frame_count=FRAME_COUNT,
            episode=e,
            score=game_data.score,
            epsilon=agent.epsilon,
            memory_len=len(agent.memory),
            game_length=len(game_data),
        )
        logger.debug("Stats", loss=np.mean(agent.loss_history), acc=np.mean(agent.acc_history))


# cv2.imwrite(f"tmp/{a}_screen.png", a._grab_screen())
