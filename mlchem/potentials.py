from ase.calculators.calculator import Calculator, Parameters
import codecs
import copy
import json
import torch

from mlchem.data.handler import DataSet
from mlchem.backends.available import available_backends


class Potentials(Calculator, object):
    """Machine-Learning for Chemistry

    This class is highly inspired on the Atomistic Machine-Learning package
    (Amp).

    Parameters
    ----------
    fingerprints : object
        Local chemical environments to build the feature space.
    model : object
        Machine-learning model to perform training.
    path : str
        PATH where to save files.
    label : str
        Name for files. Default mlchem.
    """
    # This is needed by ASE
    implemented_properties = ['energy', 'forces']

    def __init__(self, fingerprints=None, model=None, path=None,
                 label='mlchem', atoms=None, mlchem_path=None):

        Calculator.__init__(self, label=label, atoms=atoms)
        self.fingerprints = fingerprints
        self.available_backends = available_backends()
        self.path = path
        self.label = label
        self.model = model
        self.mlchem_path = mlchem_path

        print('Available backends', self.available_backends)

    @classmethod
    def load(Cls, mlchem, params, **kwargs):
        """Load a model
        Parameters
        ----------
        mlchem : str
            The path to load .mlchem file for inference.
        params : srt
            The path to load .params file with users' inputs.
        """
        kwargs['mlchem_path'] = mlchem
        with open(params) as mlchem_params:
            import torch
            mlchem_params = json.load(mlchem_params)

            # Instantiate the model class
            model_params = mlchem_params['model']
            del model_params['name']   # delete unneeded key, value
            from mlchem.models.neuralnetwork import NeuralNetwork
            model = NeuralNetwork(**model_params)

            # Instatiation of fingerprint class
            from mlchem.fingerprints import Gaussian
            fingerprint_params = mlchem_params['fingerprints']
            del fingerprint_params['name']
            fingerprints = Gaussian(**fingerprint_params)

            calc = Cls(fingerprints=fingerprints, model=model, **kwargs)

        return calc

    def save(self, model, path=None, label=None):
        """Save a model

        Parameters
        ----------
        model : obj
            The model to be saved.
        path : str
            The path where to save the model.
        label : str
            Name for files. Default mlchem.
        """

        model_name = model.name()

        if path is None and label is None:
            path = 'model'
        elif path is None and label is not None:
            path = label
        else:
            path += label

        fingerprints = {'fingerprints': self.fingerprints.params}

        if model_name == 'PytorchPotentials':
            import torch

            params = {'model': {'name': model_name,
                                'hiddenlayers': model.hiddenlayers,
                                'activation': model.activation}}

            params.update(fingerprints)

            with open(path + '.params', 'wb') as json_file:
                json.dump(params, codecs.getwriter('utf-8')(json_file),
                          ensure_ascii=False, indent=4)

            torch.save(model.state_dict(), path + '.mlchem')

    def train(self, training_set, epochs=100, lr=0.001, convergence=None,
              device='cpu',  optimizer=None, lossfxn=None, weight_decay=0.,
              regularization=0.):
        """Method to train models

        Parameters
        ----------
        training_set : object, list
            List containing the training set.
        epochs : int
            Number of full training cycles.
        lr : float
            Learning rate.
        convergence : dict
            Instead of using epochs, users can set a convergence criterion.
        device : str
            Calculation can be run in the cpu or gpu.
        optimizer : object
            An optimizer class.
        lossfxn : object
            A loss function object.
        weight_decay : float
            Weight decay passed to the optimizer. Default is 0.
        regularization : float
            This is the L2 regularization. It is not the same as weight decay.
        """

        data_handler = DataSet(training_set, self.model, purpose='training')

        # Raw input and targets aka X, y
        training_set, targets = data_handler.get_images(purpose='training')

        # Mapping raw positions into a feature space aka X
        feature_space = self.fingerprints.calculate_features(training_set,
                                                             data=data_handler)

        # Now let's train
        # Fixed fingerprint dimension
        input_dimension = len(list(feature_space.values())[0][0][-1])
        self.model.prepare_model(input_dimension, data=data_handler)

        from mlchem.models.neuralnetwork import train
        train(feature_space, targets, model=self.model, data=data_handler,
              optimizer=optimizer, lr=lr, weight_decay=weight_decay,
              regularization=regularization, epochs=epochs,
              convergence=convergence, lossfxn=lossfxn)

        self.save(self.model, path=self.path, label=self.label)

        self.data_handler = data_handler

    def calculate(self, atoms, properties, system_changes):
        """Calculate things

        Parameters
        ----------
        atoms : object, list
            List if images in ASE format.
        properties :
        """
        Calculator.calculate(self, atoms, properties, system_changes)

        # We convert the atoms in atomic fingerprints
        data_handler = DataSet([atoms], self.model, purpose='training')
        atoms, targets = data_handler.get_images(purpose='training')

        # We copy the loaded fingerprint class
        fingerprints = copy.deepcopy(self.fingerprints)
        fingerprints = fingerprints.calculate_features(atoms,
                                                       data=data_handler)

        if 'energy' in properties:
            print('Calculating energy')
            input_dimension = len(list(fingerprints.values())[0][0][-1])
            self.model.prepare_model(input_dimension, data=data_handler,
                                     purpose='inference')
            self.model.load_state_dict(torch.load(self.mlchem_path),
                                       strict=True)
            self.model.eval()
            energy = self.model(fingerprints)

            # Populate ASE's self.results dict
            self.results['energy'] = energy.item()
