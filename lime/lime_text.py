"""
Functions for explaining text classifiers.
"""
from . import lime_base
from . import explanation
import numpy as np
import scipy as sp
import sklearn
import re
import itertools

class ScikitClassifier(object):
    """ Takes a classifier and vectorizer, implements predict_proba on raw text.
    """
    def __init__(self, classifier, vectorizer):
        """Initializer.

        Args:
            classifier: trained sklearn classifier, must implement predict_proba
            vectorizer: Count or Tfidf vectorizers, in sklearn.feature_extraction.text
        """
        self.classifier = classifier
        self.vectorizer = vectorizer
    def predict_proba(self, string_list):
        """Transforms and predicts on an input string

        Args:
            string_list: list of raw text strings.

        Returns:
            same as classifier.predict_proba
        """
        return self.classifier.predict_proba(
            self.vectorizer.transform(string_list))

class IndexedString(object):
    """String with various indexes."""
    def __init__(self, raw_string, bow=True):
        """Initializer.

        Args:
            raw_string: string with raw text in it
            bow: if True, a word is the same everywhere in the text - i.e. we
                 will index multiple ocurrences of the same word. If False,
                 order matters, so that the same word will have different ids
                 according to position.
        """
        self.raw = raw_string
        self.as_list = re.split(r'(\W+)', self.raw)
        self.as_np = np.array(self.as_list)
        non_word = re.compile(r'\W+').match
        self.string_start = np.hstack(([0], np.cumsum([len(x) for x in self.as_np[:-1]])))
        vocab = {}
        self.inverse_vocab = []
        self.positions = []
        self.bow = bow
        non_vocab = set()
        for i, word in enumerate(self.as_np):
            if word in non_vocab:
                continue
            if non_word(word):
                non_vocab.add(word)
                continue
            if bow:
                if word not in vocab:
                    vocab[word] = len(vocab)
                    self.inverse_vocab.append(word)
                    self.positions.append([])
                idx_word = vocab[word]
                self.positions[idx_word].append(i)
            else:
                self.inverse_vocab.append(word)
                self.positions.append(i)
        if not bow:
            self.positions = np.array(self.positions)
    def raw_string(self):
        """Returns the original raw string"""
        return self.raw
    def num_words(self):
        """Returns the number of tokens in the vocabulary for this document."""
        return len(self.inverse_vocab)
    def word(self, id_):
        """Returns the word that corresponds to id_ (int)"""
        return self.inverse_vocab[id_]
    def string_position(self, id_):
        """Returns a np array with indices to id_ (int) ocurrences"""
        if self.bow:
            return self.string_start[self.positions[id_]]
        else:
            return self.string_start[[self.positions[id_]]]
    def inverse_removing(self, words_to_remove):
        """Returns a string after removing the appropriate words.

        Args:
            words_to_remove: list of ids (ints) to remove

        Returns:
            original raw string with appropriate words removed.
        """
        mask = np.ones(self.as_np.shape[0], dtype='bool')
        mask[self.__get_idxs(words_to_remove)] = False
        return ''.join([self.as_list[v] for v in mask.nonzero()[0]])
    def __get_idxs(self, words):
        """Returns indexes to appropriate words."""
        if self.bow:
            return list(itertools.chain.from_iterable([self.positions[z] for z in words]))
        else:
            return self.positions[words]


class LimeTextExplainer(object):
    """Explains text classifiers.
       Currently, we are using an exponential kernel on cosine distance, and
       restricting explanations to words that are present in documents."""
    def __init__(self,
                 kernel_width=25,
                 verbose=False,
                 class_names=None,
                 feature_selection='auto',
                 bow='bow'):
        """Init function.

        Args:
            kernel_width: kernel width for the exponential kernel
            verbose: if true, print local prediction values from linear model
            class_names: list of class names, ordered according to whatever the
                classifier is using. If not present, class names will be '0',
                '1', ...
            feature_selection: feature selection method. can be
                'forward_selection', 'lasso_path', 'none' or 'auto'.
                See function 'explain_instance_with_data' in lime_base.py for
                details on what each of the options does.
            bow: if True (bag of words), will perturb input data by removing all
                ocurrences of individual words.  Explanations will be in terms of
                these words. Otherwise, will explain in terms of word-positions,
                so that a word may be important the first time it appears and
                uninportant the second. Only set to false if the classifier uses
                word order in some way (bigrams, etc).

        """
        # exponential kernel
        kernel = lambda d: np.sqrt(np.exp(-(d**2) / kernel_width ** 2))
        self.base = lime_base.LimeBase(kernel, verbose)
        self.class_names = class_names
        self.vocabulary = None
        self.feature_selection = feature_selection
        self.bow = bow
    def explain_instance(self,
                         text_instance,
                         classifier_fn,
                         labels=(1,),
                         top_labels=None,
                         num_features=10,
                         num_samples=5000):
        """Generates explanations for a prediction.

        First, we generate neighborhood data by randomly hiding features from
        the instance (see __data_labels_distance_mapping). We then learn
        locally weighted linear models on this neighborhood data to explain each
        of the classes in an interpretable way (see lime_base.py).

        Args:
            text_instance: raw text string to be explained.
            classifier_fn: classifier prediction probability function, which
                takes a string and outputs prediction probabilities.  For
                ScikitClassifiers , this is classifier.predict_proba.
            labels: iterable with labels to be explained.
            top_labels: if not None, ignore labels and produce explanations for
                the K labels with highest prediction probabilities, where K is
                this parameter.
            num_features: maximum number of features present in explanation
            num_samples: size of the neighborhood to learn the linear model

        Returns:
            An Explanation object (see explanation.py) with the corresponding
            explanations.
        """
        indexed_string = IndexedString(text_instance, bow=self.bow)
        data, yss, distances = self.__data_labels_distances(
            indexed_string, classifier_fn, num_samples)
        if not self.class_names:
            self.class_names = [str(x) for x in range(yss[0].shape[0])]
        ret_exp = explanation.Explanation(indexed_string=indexed_string,
                                          class_names=self.class_names)
        ret_exp.predict_proba = yss[0]
        #map_exp = lambda exp: [(indexed_string.word(x[0]), x[1]) for x in exp]
        if top_labels:
            labels = np.argsort(yss[0])[-top_labels:]
            ret_exp.top_labels = list(labels)
            ret_exp.top_labels.reverse()
        for label in labels:
            ret_exp.local_exp[label] = self.base.explain_instance_with_data(
                data, yss, distances, label, num_features,
                feature_selection=self.feature_selection)
        return ret_exp

    @classmethod
    def __data_labels_distances(cls,
                                indexed_string,
                                classifier_fn,
                                num_samples):
        """Generates a neighborhood around a prediction.

        Generates neighborhood data by randomly removing words from
        the instance, and predicting with the classifier. Uses cosine distance
        to compute distances between original and perturbed instances.
        Args:
            indexed_string: document (IndexedString) to be explained,
            classifier_fn: classifier prediction probability function, which
                takes a string and outputs prediction probabilities. For
                ScikitClassifier, this is classifier.predict_proba.
            num_samples: size of the neighborhood to learn the linear model

        Returns:
            A tuple (data, labels, distances), where:
                data: dense num_samples * K binary matrix, where K is the
                    number of tokens in indexed_string. The first row is the
                    original instance, and thus a row of ones.
                labels: num_samples * L matrix, where L is the number of target
                    labels
                distances: cosine distance between the original instance and
                    each perturbed instance (computed in the binary 'data'
                    matrix), times 100.
        """
        distance_fn = lambda x: sklearn.metrics.pairwise.cosine_distances(x[0], x)[0] * 100
        doc_size = indexed_string.num_words()
        sample = np.random.randint(1, doc_size, num_samples - 1)
        data = np.ones((num_samples, doc_size))
        data[0] = np.ones(doc_size)
        features_range = range(doc_size)
        inverse_data = [indexed_string.raw_string()]
        for i, size in enumerate(sample, start=1):
            inactive = np.random.choice(features_range, size, replace=False)
            data[i, inactive] = 0
            inverse_data.append(indexed_string.inverse_removing(inactive))
        labels = classifier_fn(inverse_data)
        distances = distance_fn(sp.sparse.csr_matrix(data))
        return data, labels, distances