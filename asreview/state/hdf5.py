# Copyright 2019-2020 The ASReview Authors. All Rights Reserved.
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

import json
import zipfile
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse.csr import csr_matrix
from scipy.sparse import load_npz

from asreview.settings import ASReviewSettings
from asreview.state.base import BaseState
from asreview.state.errors import StateNotFoundError
from asreview.state.errors import StateError


LATEST_HDF5STATE_VERSION = "1.1"
RESULTS_TABLE_COLUMNS = ['indices', 'labels', 'predictor_classifiers', 'predictor_query_strategies',
                         'predictor_balance_strategies', 'predictor_feature_extraction',
                         'predictor_training_sets', 'labeling_times']


def create_sql_data_in_zipfile(zipobj):
    """Create a file 'results.sql', containing a table 'results'
    in the zipobj.

    Arguments
    ---------
    zipobj: zipfile.ZipFile
        Zipfile to which to add the sql database.
    """
    tempdir = tempfile.TemporaryDirectory()
    temppath = Path(tempdir.name)
    sql_fp = temppath / 'results.sql'
    con = sqlite3.connect(sql_fp)
    cur = con.cursor()

    # Create the results table.
    cur.execute('''CREATE TABLE results
                    (indices INTEGER, 
                    labels INTEGER, 
                    predictor_classifiers TEXT,
                    predictor_query_strategies TEXT,
                    predictor_balance_strategies TEXT,
                    predictor_feature_extraction TEXT,
                    predictor_training_sets INTEGER,
                    labeling_times TEXT)''')

    con.commit()
    con.close()
    zipobj.write(sql_fp, arcname='results.sql')


def read_sql_table_from_zipfile(zipobj, sql_name, table):
    """Read the table from the sqlite database with name sql_name from the given zipfile.

    Arguments
    ---------
    zipobj: zipfile.ZipFile
        Zipfile from which to read the data.
    sql_name: path-like
        Location of the sql file in the zipfile.
    table: str
        Name of the table in the sqlfile which to read.

    Returns
    -------
    pd.DataFrame
        Dataframe containing the data from the given table in the given sql file.
    """
    tempdir = tempfile.TemporaryDirectory()
    temppath = Path(tempdir.name)
    zipobj.extractall(temppath)
    sql_fp = temppath / sql_name

    con = sqlite3.connect(sql_fp)
    df = pd.read_sql_query(f'SELECT * FROM {table}', con)
    con.close()
    return df


class HDF5State(BaseState):
    """Class for storing the review state with HDF5 storage.

    Arguments
    ---------
    read_only: bool
        Open state in read only mode. Default False.
    """

    def __init__(self, read_only=True):
        super(HDF5State, self).__init__(read_only=read_only)

### OPEN, CLOSE, SAVE, INIT
    def _create_new_state_file(self, fp):
        if self.read_only:
            raise ValueError(
                "Can't create new state file in read_only mode."
            )

        # create folder to state file if not exist
        Path(fp).parent.mkdir(parents=True, exist_ok=True)

        self.f = zipfile.ZipFile(fp, 'a')

        # TODO(State): Add software version.
        # Create settings_metadata.json
        self.settings_metadata = {
            'start_time': str(datetime.now()),
            'end_time': "",
            'settings': "{}",
            'version': LATEST_HDF5STATE_VERSION
        }

        self.f.writestr('settings_metadata.json', json.dumps(self.settings_metadata))

        # Create results table.
        create_sql_data_in_zipfile(self.f)

        # Cache the results table.
        self.results = pd.DataFrame(columns=RESULTS_TABLE_COLUMNS)
        # TODO (State): Models being trained.

    def _restore(self, fp):
        """Restore the state file.

        Arguments
        ---------
        fp: str, pathlib.Path
            File path of the state file.
        """

        # If state already exist
        if not Path(fp).is_file():
            raise StateNotFoundError(f"State file {fp} doesn't exist.")

        # store read_only value
        mode = "r" if self.read_only else "a"

        # open or create state file
        self.f = zipfile.ZipFile(fp, mode)

        # Cache the settings.
        try:
            self.settings_metadata = json.load(self.f.open('settings_metadata.json'))
        except KeyError:
            raise AttributeError("'settings_metadata.json' not found in the state file.")

        # Cache the results.
        self.results = read_sql_table_from_zipfile(self.f, 'results.sql', 'results')

        # Cache the record table.
        self.record_table = read_sql_table_from_zipfile(self.f, 'results.sql', 'record_table')

        try:
            if not self._is_valid_version():
                raise ValueError(
                    f"State cannot be read: state version {self.version}, "
                    f"state file version {self.version}.")
        except AttributeError as err:
            raise ValueError(
                f"Unexpected error when opening state file: {err}"
            )

        self._is_valid_state()

    # TODO(State): Check more things?
    def _is_valid_state(self):
        for dataset in RESULTS_TABLE_COLUMNS:
            if dataset not in self.results.columns:
                raise KeyError(f"State file structure has not been initialized in time, {dataset} is not present. ")

    def save(self):
        """Save and close the state file."""
        self.f['end_time'] = str(datetime.now())
        self.f.flush()

    def close(self):
        # TODO{STATE} Merge with save?

        if not self.read_only:
            self.f.attrs['end_time'] = np.string_(datetime.now())

        self.f.close()

### PROPERTIES
    def _is_valid_version(self):
        """Check compatibility of state version."""
        # TODO check for version <= 1.1, should fail as well
        # QUESTION: Should all version < LATEST_HDF5STATE fail, or only versions
        # < LATEST_DEPRECATED_VERSION?
        return self.version[0] == LATEST_HDF5STATE_VERSION[0]

    @property
    def version(self):
        """Version number of the state file."""
        try:
            return self.settings_metadata['version']
        except KeyError:
            raise AttributeError("'settings_metadata.json' does not contain 'version'.")

    @property
    def start_time(self):
        """Init datetime of the state file."""
        try:
            # Time is saved as integer number of microseconds.
            # Divide by 10**6 to convert to second
            start_time = self.settings_metadata['start_time']
            return datetime.utcfromtimestamp(start_time/10**6)
        except Exception:
            raise AttributeError("Attribute 'start_time' not found.")

    @property
    def end_time(self):
        """Last modified (datetime) of the state file."""
        try:
            end_time = self.settings_metadata['end_time']
            return datetime.utcfromtimestamp(end_time/10**6)
        except Exception:
            raise AttributeError("Attribute 'end_time' not found.")

    @end_time.setter
    def end_time(self, time):
        timestamp = int(time.timestamp() * 10**6)
        self.settings_metadata['end_time'] = timestamp


    @property
    def settings(self):
        """Settings of the ASReview pipeline.

        Settings like models.

        Example
        -------

        Example of settings.

            model             : nb
            query_strategy    : max_random
            balance_strategy  : triple
            feature_extraction: tfidf
            n_instances       : 1
            n_queries         : 1
            n_prior_included  : 10
            n_prior_excluded  : 10
            mode              : simulate
            model_param       : {'alpha': 3.822}
            query_param       : {'strategy_1': 'max', 'strategy_2': 'random', 'mix_ratio': 0.95}
            feature_param     : {}
            balance_param     : {'a': 2.155, 'alpha': 0.94, ... 'gamma': 2.0, 'shuffle': True}
            abstract_only     : False

        """
        settings = self.settings_metadata['settings']
        if settings is None:
            return None
        return ASReviewSettings(**settings)

    @settings.setter
    def settings(self, settings):
        self.f.attrs.pop('settings', None)
        self.f.attrs['settings'] = np.string_(json.dumps(vars(settings)))

    @property
    def current_queries(self):
        """Get the current queries made by the model.

        This is useful to get back exactly to the state it was in before
        shutting down a review.

        Returns
        -------
        dict:
            The last known queries according to the state file.
        """
        str_queries = self.settings_metadata['current_queries']
        return {int(key): value for key, value in str_queries.items()}

    @current_queries.setter
    def current_queries(self, current_queries):
        str_queries = {
            str(key): value
            for key, value in current_queries.items()
        }
        data = np.string_(json.dumps(str_queries))
        self.f.attrs.pop("current_queries", None)
        self.f.attrs["current_queries"] = data

    @property
    def n_records_labeled(self):
        """Get the number of labeled records, where each prior is counted individually."""
        return len(self.results)

# TODO: Should this return 0 if it is empty?
    @property
    def n_predictor_models(self):
        """Get the number of unique (model type + training set) models that were used. """
        predictor_classifiers = list(self.results['predictor_classifiers'])[self.n_priors:]
        predictor_training_sets = list(self.results['predictor_training_sets'].astype(str))[self.n_priors:]
        # A model is uniquely determine by the string {model_code}{training_set}.
        model_ids = [model + tr_set for (model, tr_set) in zip(predictor_classifiers, predictor_training_sets)]
        # Return the number of unique model_ids plus one for the priors.
        return np.unique(model_ids).shape[0] + 1

    @property
    def n_priors(self):
        """Get the number of samples in the prior information.

        Returns
        -------
        int:
            Number of priors. If priors have not been selected returns None.
        """
        n_priors = list(self.results['predictor_query_strategies']).count('prior')
        if n_priors == 0:
            n_priors = None
        return n_priors

### Features, settings_metadata

    def _add_settings_metadata(self, key, value):
        """Add information to the settings_metadata dictionary."""
        self.settings_metadata[key] = value

        # Check if there is already a 'settings_metadata.json' in the zipfile.
        try:
            zipinfo = self.f.getinfo('settings_metadata.json')
        except KeyError:
            zipinfo = 'settings_metadata.json'

        self.f.writestr(zipinfo, json.dumps(self.settings_metadata))

    def _add_as_data(self, as_data, feature_matrix=None):
        record_table = as_data.record_ids
        data_hash = as_data.hash()
        try:
            data_group = self.f["/data_properties"]
        except KeyError:
            data_group = self.f.create_group("/data_properties")

        try:
            as_data_group = data_group[data_hash]
        except KeyError:
            as_data_group = data_group.create_group(data_hash)

        if "record_table" not in as_data_group:
            as_data_group.create_dataset("record_table", data=record_table)

        if feature_matrix is None:
            return
        if isinstance(feature_matrix, np.ndarray):
            if "feature_matrix" in as_data_group:
                return
            as_data_group.create_dataset("feature_matrix", data=feature_matrix)
            as_data_group.attrs['matrix_type'] = np.string_("ndarray")
        elif isinstance(feature_matrix, csr_matrix):
            if "indptr" in as_data_group:
                return
            as_data_group.create_dataset("indptr", data=feature_matrix.indptr)
            as_data_group.create_dataset("indices",
                                         data=feature_matrix.indices)
            as_data_group.create_dataset("shape",
                                         data=feature_matrix.shape,
                                         dtype=int)
            as_data_group.create_dataset("data", data=feature_matrix.data)
            as_data_group.attrs["matrix_type"] = np.string_("csr_matrix")
        else:
            as_data_group.create_dataset("feature_matrix", data=feature_matrix)
            as_data_group.attrs["matrix_type"] = np.string_("unknown")

    # TODO(State): Should the feature matrix be behind a data hash?
    def get_feature_matrix(self):
        return load_npz(self.f.open('feature_matrix.npz'))

### METHODS/FUNC
    # def set_labels(self, y):
    # Remove this
    #     """Set the initial labels as of the dataset.

    #     y: list
    #         List of outcome labels.
    #     """

    #     if "labels" not in self.f:
    #         # key labels doesn't exist, create and fill with data
    #         self.f.create_dataset("labels", y.shape, dtype=np.int, data=y)
    #     else:
    #         # exists, but overwrite
    #         self.f["labels"][...] = y

    # def set_final_labels(self, y):
    #     # Seems to be deprecated
    #     if "final_labels" not in self.f:
    #         self.f.create_dataset("final_labels",
    #                               y.shape,
    #                               dtype=np.int,
    #                               data=y)
    #     else:
    #         self.f["final_labels"][...] = y

    def _append_to_dataset(self, dataset, values):
        """Add the values to the dataset.

        Arguments
        ---------
        dataset: str
            Name of the dataset you want to add values to.
        values: list, np.array
            Values to add to dataset.
        """
        results = self.f['results']
        cur_size = results[dataset].shape[0]
        results[dataset].resize((cur_size + len(values),))
        results[dataset][cur_size: cur_size + len(values)] = values

# TODO (State): Where this function is used, still need to update it.
# TODO (State): Add models being trained (Start with only one model at the same time).
    def add_labeling_data(self, record_ids, labels, models, methods, training_sets, labeling_times):
        """Add all data of one labeling action. """
        # Check if the datasets have all been created.
        self._is_valid_state()

        # Check that all input data has the same length.
        lengths = [len(record_ids), len(labels), len(models), len(methods), len(training_sets), len(labeling_times)]
        if len(set(lengths)) != 1:
            raise ValueError("Input data should be of the same length.")

        # Convert record_ids to row indices.
        indices = np.array([self._record_id_to_row_index(record_id) for record_id in record_ids])

        # Add labeling data.
        self._append_to_dataset('indices', indices)
        self._append_to_dataset('labels', labels)
        self._append_to_dataset('predictor_models', models)
        self._append_to_dataset('predictor_methods', methods)
        self._append_to_dataset('predictor_training_sets', training_sets)
        self._append_to_dataset('labeling_times', labeling_times)

# TODO (State): Add custom datasets.
    # # def add_model_data(..)
    # def add_proba(self, pool_idx, train_idx, proba, query_i):
    #     """Add data after finishing training of a model."""
    #     g = _result_group(self.f, query_i)
    #     g.create_dataset("pool_idx", data=pool_idx, dtype=np.int)
    #     g.create_dataset("train_idx", data=train_idx, dtype=np.int)
    #     g.create_dataset("proba", data=proba, dtype=np.float)

    def _record_id_to_row_index(self, record_id):
        """Find the row index that corresponds to a given record id.

        Arguments
        ---------
        record_id: int
            Record_id of a record.

        Returns
        -------
        int:
            Row index of the given record_id in the dataset.
        """
        return np.where(self.record_table == record_id)[0][0]

    def _row_index_to_record_id(self, row_index):
        """Find the record_id that corresponds to a given row index.

        Arguments
        ----------
        row_index: int
            Row index.

        Returns
        -------
        str:
            Record_id of the record with given row index.

        """
        return self.record_table.iloc[row_index].item()

    def _get_dataset(self, results_column, query=None, record_id=None):
        """Get a column from the results table, or only the part corresponding to a given query or record_id.

        Arguments
        ---------
        results_column: str
            Name of the column of the results table you want to get.
        query: int
            Only return the data of the given query, where query=0 correspond to the prior information.
        record_id: str/int
            Only return the data corresponding to the given record_id.

        Returns
        -------
        np.ndarray:
            If both query and record_id are None, return the full dataset.
            If query is given, return the data from that query, where the 0-th query is the prior information.
            If record_id is given, return the data corresponding record.
            If both are given it raises a ValueError.
        """
        if (query is not None) and (record_id is not None):
            raise ValueError("You can not search by record_id and query at the same time.")

        if query is not None:
            # 0 corresponds to all priors.
            if query == 0:
                dataset_slice = range(self.n_priors)
            # query_i is in spot (i + n_priors - 1).
            else:
                dataset_slice = [query + self.n_priors - 1]
        elif record_id is not None:
            # Convert record id to row index.
            idx = self._record_id_to_row_index(record_id)
            # Find where this row number was labelled.
            dataset_slice = np.where(self.results['indices'][:] == idx)[0]
        else:
            # Return the whole dataset.
            dataset_slice = range(self.n_records_labeled)

        return np.array(self.results[results_column])[dataset_slice]

    def get_order_of_labeling(self):
        """Get full array of record id's in order that they were labeled.

        Returns
        -------
        np.ndarray:
            The record_id's in the order that they were labeled.
        """
        indices = self._get_dataset(results_column='indices')
        return np.array([self._row_index_to_record_id(idx) for idx in indices])

    def get_labels(self, query=None, record_id=None):
        """Get the labels from the state file.

        Arguments
        ---------
        query: int
            The query number from which you want to obtain the label.
            If this is 0, you get the label for all the priors.
        record_id: str
            The record_id of the sample from which you want to obtain the label.

        Returns
        -------
        np.ndarray:
            If query and record_id are None, it returns the full array with labels in the labeling order,
            else it returns only the specific one determined by query or record_id.
        """
        return self._get_dataset('labels', query=query, record_id=record_id)

    def get_predictor_classifiers(self, query=None, record_id=None):
        """Get the predictor classifiers from the state file.

        Arguments
        ---------
        query: int
            The query number from which you want to obtain the predictor classifiers.
            If this is 0, you get the predictor classifier for all the priors.
        record_id: str
            The record_id of the sample from which you want to obtain the predictor classifiers.

        Returns
        -------
        np.ndarray:
            If query and record_id are None, it returns the full array with predictor classifiers in the labeling order,
            else it returns only the specific one determined by query or record_id.
        """
        return self._get_dataset(results_column='predictor_classifiers', query=query, record_id=record_id)

    def get_predictor_query_strategies(self, query=None, record_id=None):
        """Get the predictor query strategies from the state file.

        Arguments
        ---------
        query: int
            The query number from which you want to obtain the predictor query strategies.
            If this is 0, you get the predictor query strategy for all the priors.
        record_id: str
            The record_id of the sample from which you want to obtain the predictor query strategies.

        Returns
        -------
        np.ndarray:
            If query and record_id are None, it returns the full array with predictor query strategies in the labeling
            order, else it returns only the specific one determined by query or record_id.
        """
        return self._get_dataset(results_column='predictor_query_strategies', query=query, record_id=record_id)

    def get_predictor_balance_strategies(self, query=None, record_id=None):
        """Get the predictor balance strategies from the state file.

        Arguments
        ---------
        query: int
            The query number from which you want to obtain the predictor balance strategies.
            If this is 0, you get the predictor balance strategy for all the priors.
        record_id: str
            The record_id of the sample from which you want to obtain the predictor balance strategies.

        Returns
        -------
        np.ndarray:
            If query and record_id are None, it returns the full array with predictor balane strategies in the labeling
            order, else it returns only the specific one determined by query or record_id.
        """
        return self._get_dataset(results_column='predictor_balance_strategies', query=query, record_id=record_id)

    def get_predictor_feature_extraction(self, query=None, record_id=None):
        """Get the predictor query strategies from the state file.

        Arguments
        ---------
        query: int
            The query number from which you want to obtain the predictor feature extraction methods.
            If this is 0, you get the predictor feature extraction methods for all the priors.
        record_id: str
            The record_id of the sample from which you want to obtain the predictor feature extraction methods.

        Returns
        -------
        np.ndarray:
            If query and record_id are None, it returns the full array with predictor feature extraction methods in the
            labeling order, else it returns only the specific one determined by query or record_id.
        """
        return self._get_dataset(results_column='predictor_feature_extraction', query=query, record_id=record_id)

    def get_predictor_training_sets(self, query=None, record_id=None):
        """Get the predictor training_sets from the state file.

        Arguments
        ---------
        query: int
            The query number from which you want to obtain the predictor training set.
            If this is 0, you get the predictor training set for all the priors.
        record_id: str
            The record_id of the sample from which you want to obtain the predictor training set.

        Returns
        -------
        np.ndarray:
            If query and record_id are None, it returns the full array with predictor training sets in the labeling
            order, else it returns only the specific one determined by query or record_id.
        """
        return self._get_dataset(results_column='predictor_training_sets', query=query, record_id=record_id)

    def get_labeling_time(self, query=None, record_id=None, format='int'):
        """Get the time of labeling the state file.

        Arguments
        ---------
        query: int
            The query number from which you want to obtain the time.
            If this is 0, you get the time the priors were entered,
            which is the same for all priors.
        record_id: str
            The record_id of the sample from which you want to obtain the time.
        format: 'int' or 'datetime'
            Format of the return value. If it is 'int' you get a UTC timestamp ,
            if it is 'datetime' you get datetime instead of an integer.

        Returns
        -------
        np.ndarray:
            If query and record_id are None, it returns the full array with times in the labeling order,
            else it returns only the specific one determined by query or record_id.
            If format='int' you get a UTC timestamp (integer number of microseconds) as np.int64 dtype,
            if it is 'datetime' you get np.datetime64 format.
        """
        times = self._get_dataset('labeling_times', query=query, record_id=record_id)

        # Convert time to datetime in string format.
        if format == 'datetime':
            times = np.array([datetime.utcfromtimestamp(time/10**6) for time in times], dtype=np.datetime64)

        if query == 0:
            times = times[[self.n_priors-1]]
        return times

