# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root
# for full license information.
# ==============================================================================

"""
Unit tests for function extension
"""

from __future__ import division, print_function
import os
import numpy as np
from argparse import ArgumentParser
import cntk as C

from cntk.cntk_py import always_allow_setting_default_device
from cntk.ops.functions import UserFunction
from cntk import sigmoid
from cntk.device import cpu, gpu

np.random.seed(0)


def cntk_device(device_id):
    '''
    Converts the legacy device ID as it was used in CNTK 1 to a :class:`~cntk.device.DeviceDescriptor` instance.

    Args:
        device_id (int): device id, -1 for CPU, 0 or higher for GPU

    Returns:
        :class:`~cntk.device.DeviceDescriptor`
    '''
    if device_id == -1:
        return cpu()
    else:
        return gpu(device_id)


def os_process():
    '''
    Returns the process instance, which can be used e.g. to check the memory
    usage.
    '''
    import psutil
    return psutil.Process(os.getpid())


def mem_used(process):
    '''
    Return the non-swapped physical memory the Python process is using.
    '''
    return process.memory_info().rss

input_dim = 2
num_output_classes = 2


def generate_random_data_sample(sample_size, feature_dim, num_classes):
    # Create synthetic data using NumPy.
    Y = np.random.randint(size=(sample_size, 1), low=0, high=num_classes)

    # Make sure that the data is separable
    X = (np.random.randn(sample_size, feature_dim)+3) * (Y+1)
    X = X.astype(np.float32)
    class_ind = [Y == class_number for class_number in range(num_classes)]
    Y = np.asarray(np.hstack(class_ind), dtype=np.float32)
    return X, Y


def linear_layer(input_var, output_dim):
    input_dim = input_var.shape[0]
    times_param = C.parameter(shape=(input_dim, output_dim))
    bias_param = C.parameter(shape=(output_dim))

    t = C.times(input_var, times_param)
    return bias_param + t


def dense_layer(inp, output_dim, nonlinearity):
    r = linear_layer(inp, output_dim)
    r = nonlinearity(r)
    if isinstance(r, UserFunction):
        r = C.user_function(r)
    return r


def fully_connected_classifier_net(inp, num_output_classes, hidden_layer_dim,
                                   num_hidden_layers, nonlinearity):
    h = dense_layer(inp, hidden_layer_dim, nonlinearity)
    for i in range(1, num_hidden_layers):
        h = dense_layer(h, hidden_layer_dim, nonlinearity)
    r = linear_layer(h, num_output_classes)
    return r


def print_training_progress(trainer, mb, frequency):
    training_loss = "NA"
    eval_error = "NA"

    if mb % frequency == 0:
        training_loss = trainer.previous_minibatch_loss_average
        eval_error = trainer.previous_minibatch_evaluation_average

    return mb, training_loss, eval_error


def train(nonlinearity, num_hidden_layers, device_id,
          minibatch_size=10, num_samples=1000):
    np.random.seed(0)

    learning_rate = 0.5
    lr_schedule = C.learning_rate_schedule(learning_rate, C.UnitType.minibatch)

    hidden_layers_dim = 50

    inp = C.input_variable((input_dim), np.float32)
    label = C.input_variable((num_output_classes), np.float32)

    z = fully_connected_classifier_net(inp, num_output_classes, hidden_layers_dim,
                                       num_hidden_layers, nonlinearity)

    loss = C.cross_entropy_with_softmax(z, label)
    eval_error = C.classification_error(z, label)

    learner = C.sgd(z.parameters, lr_schedule)
    trainer = C.Trainer(z, (loss, eval_error), [learner])

    num_minibatches_to_train = int(num_samples / minibatch_size)

    training_progress_output_freq = 20

    losses = []
    errors = []

    for i in range(num_minibatches_to_train):
        features, labels = generate_random_data_sample(minibatch_size,
                                                       input_dim,
                                                       num_output_classes)

        # Specify the input variables mapping in the model to actual minibatch
        # data for training.
        trainer.train_minibatch({inp: features, label: labels},
                                device=cntk_device(device_id))

        batchsize, loss, error = print_training_progress(trainer, i,
                                                         training_progress_output_freq)

        if not (loss == "NA" or error == "NA"):
            losses.append(loss)
            errors.append(error)

    return losses, errors


def mem_leak_check(nonlinearity, num_hidden_layers, device_id,
                   minibatch_size=1, num_samples=100000):
    from cntk.cntk_py import always_allow_setting_default_device
    always_allow_setting_default_device()
    C.try_set_default_device(cntk_device(device_id))
    np.random.seed(0)

    learning_rate = 0.5
    lr_schedule = C.learning_rate_schedule(learning_rate, C.UnitType.minibatch)

    hidden_layers_dim = 2

    inp = C.input_variable((input_dim), np.float32)
    label = C.input_variable((num_output_classes), np.float32)

    z = fully_connected_classifier_net(inp, num_output_classes, hidden_layers_dim,
                                       num_hidden_layers, nonlinearity)

    loss = C.cross_entropy_with_softmax(z, label)
    eval_error = C.classification_error(z, label)

    learner = C.sgd(z.parameters, lr_schedule)
    trainer = C.Trainer(z, (loss, eval_error), [learner])

    num_minibatches_to_train = int(num_samples / minibatch_size)

    mem = np.zeros(num_minibatches_to_train)

    features, labels = generate_random_data_sample(minibatch_size,
                                                   input_dim,
                                                   num_output_classes)

    # Set a maximum fraction of iterations, in which the memory is allowed to
    # increase. Most likely these will be the first training runs.
    MEM_INCREASE_FRACTION_TOLERANCE = 0.1
    # Set a maximum allowed memory increase. We need to have this tolerance
    # because of the normal fluctuations when using CPU as the device. 
    MEM_INCREASE_TOLERANCE = 150*1024

    dev = cntk_device(device_id)
    i = 0
    proc = os_process()
    while i < num_minibatches_to_train:
        mem[i] = mem_used(proc)

        # Specify the input variables mapping in the model to actual minibatch
        # data for training.
        trainer.train_minibatch({inp: features, label: labels},
                                device=dev)
        i += 1
    
    mem_deltas = np.diff(mem)
    iterations_with_mem_increase = (mem_deltas > 0).sum()
    mem_inc_fraction = iterations_with_mem_increase/num_minibatches_to_train

    # Calculate the memory usage mean over the last `interval` runs and compare
    # it with mean over the middle `interval` runs.
    mid_point = int(len(mem)/2)
    interval = 1000
    mem_diff = mem[mid_point:mid_point+interval].mean() - mem[-interval:].mean()

    if mem_inc_fraction > MEM_INCREASE_FRACTION_TOLERANCE and \
            mem_diff > MEM_INCREASE_TOLERANCE:
        # For the rough leak estimation we take the memory footprint after the
        # dust of the first train_minibatch runs has settled.
        mem_changes = mem_deltas[mem_deltas != 0]
        raise ValueError('Potential memory leak of ~%i KB (%i%% of MBs '
                         'increased memory usage) detected with %s:\n%s' %
                         (int(mem_diff/1024), int(mem_inc_fraction*100),
                             nonlinearity, mem_changes))


class MySigmoid(UserFunction):
    def __init__(self, arg, name='MySigmoid'):
        super(MySigmoid, self).__init__([arg], name=name)

    def forward(self, argument, device=None, outputs_to_retain=None):
        sigmoid_x = 1/(1+np.exp(-argument))

        return sigmoid_x, sigmoid_x

    def backward(self, state, root_gradients):
        sigmoid_x = state

        return root_gradients * sigmoid_x * (1 - sigmoid_x)

    def infer_outputs(self):
        return [C.output_variable(self.inputs[0].shape, self.inputs[0].dtype,
                self.inputs[0].dynamic_axes)]


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('-d', '--deviceid', type=str, required=True,
                        help='device ID to use, if -1 or "cpu" -> CPU, '
                             'if >=0 -> GPU with the parameter as an ID, '
                             'if "gpu" -> GPU 0')
    args = parser.parse_args()

    DEVICE_MAP = {'cpu': -1, 'gpu': 0}
    device_id = int(DEVICE_MAP.get(args.deviceid.lower(), args.deviceid))

    always_allow_setting_default_device()
    C.try_set_default_device(cntk_device(device_id))

    print("Run memory leakage tests")
    print("Check sigmoid")
    mem_leak_check(sigmoid, 1, device_id)
    print("Check MySigmoid")
    mem_leak_check(MySigmoid, 1, device_id)
