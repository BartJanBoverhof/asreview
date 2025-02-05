# Copyright 2020 The ASReview Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging

try:
    import tensorflow as tf
    from tensorflow.keras.layers import Dense
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.wrappers.scikit_learn import KerasClassifier
    from tensorflow.keras import regularizers
    from tensorflow.keras.models import load_model
    from tensorflow.keras.callbacks import EarlyStopping 

except ImportError:
    TF_AVAILABLE = False
else:
    TF_AVAILABLE = True
    try:
        tf.logging.set_verbosity(tf.logging.ERROR)
    except AttributeError:
        logging.getLogger("tensorflow").setLevel(logging.ERROR)

import scipy
from os.path import isfile
import numpy as np


from asreview.models.classifiers.base import BaseTrainClassifier
from asreview.models.classifiers.lstm_base import _get_optimizer
from asreview.utils import _set_class_weight


def _check_tensorflow():
    if not TF_AVAILABLE:
        raise ImportError(
            "Install tensorflow package (`pip install tensorflow`) to use"
            " 'EmbeddingIdf'.")


class NN2LayerClassifier(BaseTrainClassifier):
    """Dense neural network classifier.

    Neural network with two hidden, dense layers of the same size.

    Recommended feature extraction model is
    :class:`asreview.models.feature_extraction.Doc2Vec`.

    .. note::

        This model requires ``tensorflow`` to be installed. Use ``pip install
        tensorflow`` or install all optional ASReview dependencies with ``pip
        install asreview[all]``

    .. warning::

        Might crash on some systems with limited memory in
        combination with :class:`asreview.models.feature_extraction.Tfidf`.

    Arguments
    ---------
    dense_width: int
        Size of the dense layers.
    optimizer: str
        Name of the Keras optimizer.
    learn_rate: float
        Learning rate multiplier of the default learning rate.
    regularization: float
        Strength of the regularization on the weights and biases.
    verbose: int
        Verbosity of the model mirroring the values for Keras.
    epochs: int
        Number of epochs to train the neural network.
    batch_size: int
        Batch size used for the neural network.
    shuffle: bool
        Whether to shuffle the training data prior to training.
    class_weight: float
        Class weights for inclusions (1's).
    """

    name = "nn-2-layer"

    def __init__(self,
                 dense_width=128,
                 optimizer='rmsprop',
                 learn_rate=1.0,
                 regularization=0.01,
                 verbose=0,
                 epochs=35,
                 batch_size=32,
                 shuffle=False,
                 class_weight=30.0):
        """Initialize the 2-layer neural network model."""
        super(NN2LayerClassifier, self).__init__()
        self.dense_width = int(dense_width)
        self.optimizer = optimizer
        self.learn_rate = learn_rate
        self.regularization = regularization
        self.verbose = verbose
        self.epochs = int(epochs)
        self.batch_size = int(batch_size)
        self.shuffle = shuffle
        self.class_weight = class_weight
        self.delta=0.03
        self.patience=2
        self.iteration = 1

        self._model = None
        self.input_dim = None

    def fit(self, X, y):

        # check is tensorflow is available
        _check_tensorflow()

        if scipy.sparse.issparse(X):
            X = X.toarray()

        self.input_dim = X.shape[1]
        keras_model = _create_dense_nn_model(
            self.input_dim, self.dense_width, self.optimizer,
            self.learn_rate, self.regularization, self.verbose)
        self._model = KerasClassifier(keras_model, verbose=self.verbose)
        self.earlystop = EarlyStopping(monitor='loss', mode='min', min_delta = self.delta, patience = self.patience, restore_best_weights=True)

        history = self._model.fit(
            X,
            y,
            batch_size=self.batch_size,
            epochs=self.epochs,
            shuffle=self.shuffle,
            verbose=self.verbose,
            callbacks=[self.earlystop],
            class_weight=_set_class_weight(self.class_weight))

        history.model.save("model.h5")
        print("Iteration: ", self.iteration, "Amount of epochs: ",len(history.history["loss"]))    
        self.iteration = self.iteration+1

    def predict_proba(self, X):
        if scipy.sparse.issparse(X):
            X = X.toarray()
        return super(NN2LayerClassifier, self).predict_proba(X)

    def full_hyper_space(self):
        from hyperopt import hp
        hyper_choices = {
            "mdl_optimizer": ["sgd", "rmsprop", "adagrad", "adam", "nadam"]
        }
        hyper_space = {
            "mdl_dense_width":
            hp.quniform("mdl_dense_width", 2, 100, 1),
            "mdl_epochs":
            hp.quniform("mdl_epochs", 20, 60, 1),
            "mdl_optimizer":
            hp.choice("mdl_optimizer", hyper_choices["mdl_optimizer"]),
            "mdl_learn_rate":
            hp.lognormal("mdl_learn_rate", 0, 1),
            "mdl_class_weight":
            hp.lognormal("mdl_class_weight", 3, 1),
            "mdl_regularization":
            hp.lognormal("mdl_regularization", -4, 2),
        }
        return hyper_space, hyper_choices


def _create_dense_nn_model(vector_size=40,
                           dense_width=128,
                           optimizer='rmsprop',
                           learn_rate_mult=1.0,
                           regularization=0.01,
                           verbose=1):
    """Return callable lstm model.

    Returns
    -------
    callable:
        A function that return the Keras Sklearn model when
        called.

    """

    # check is tensorflow is available
    _check_tensorflow()

    def model_wrapper():

        if isfile('model.h5'):
            model = load_model("model.h5")
            return model

        model = Sequential()

        model.add(
            Dense(
                dense_width,
                input_dim=vector_size,
                kernel_regularizer=regularizers.l2(regularization),
                activity_regularizer=regularizers.l1(regularization),
                activation='relu',
            ))

        # add Dense layer with relu activation
        model.add(
            Dense(
                dense_width,
                kernel_regularizer=regularizers.l2(regularization),
                activity_regularizer=regularizers.l1(regularization),
                activation='relu',
            ))

        # add Dense layer
        model.add(Dense(1, activation='sigmoid'))

        optimizer_fn = _get_optimizer(optimizer, learn_rate_mult)

        # Compile model
        model.compile(
            loss='binary_crossentropy',
            optimizer=optimizer_fn,
            metrics=['acc'])

        if verbose >= 1:
            model.summary()

        return model

    return model_wrapper