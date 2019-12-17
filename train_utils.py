import os
import sys

import socket
import time
import argparse
import pickle
import datetime
import multiprocessing

import numpy as np
import torch
import torch.optim as optim
import torch.nn.functional as torchF
from torch.optim import lr_scheduler
from torch.utils.data import DataLoader
import torch.multiprocessing as mp


class MyArgs:
    '''Stores arguments to __dict__ of the class such that all arguments can be visited as attributes. This class is designed for visiting dictionary keys as attributes.
    It's recommended to store experiment arguments and runtime arguments in two objects.
        - Experiment arguments: arguments having effect on training, such as learning rate, batch size, random seed, and model hyperparameters;
        - Runtime arguments: arguments only related to running (theoretically), such as device, GPU numbers, training/test mode...
    Usually we only need to serialize and persist experiment arguments.

    Args:
        argdict: a dictionary storing all arguments, such as lr (learning rate), batch_size, ...
    '''
    def __init__(self, **argdict):
        for k, v in argdict.items():
            if isinstance(v, dict):
                self.__dict__[k] = MyArgs(**v)
            else:
                self.__dict__[k] = v

    def to_argdict(self):
        '''Transform the object to a dictionary. Can be saved with yaml or other serialization tools.
        '''
        argdict = dict()
        for k, v in self.__dict__.items():
            if isinstance(v, MyArgs):
                argdict[k] = v.to_argdict()
            else:
                argdict[k] = v
        return argdict

    def load_argdict(self, argdict):
        '''Transform a dictionary to the object. Will overwrite existing keys!
        '''
        for k, v in argdict.items():
            if isinstance(v, dict):
                self.__dict__[k] = MyArgs(**v)
            else:
                self.__dict__[k] = v


def fetch_ckpt_namelist(ckptdir, suffix='_checkpoint.pt'):
    '''Auxiliary function to get a list of numbered checkpoints under the specified directory.
    Here we assume that all numbered checkpoint files are named as `{:d}{}`.format(epochnum, suffix), e.g., `1_checkpoint.pt`.
    The best checkpoint file storing the model with the best performance on the validaiton set won't be listed.

    Return a list of checkpoint file names sorted via numbers (or an empty list if there is no numbered checkpoint files).
    '''
    ckpts = []
    for x in os.listdir(ckptdir):
        if x.endswith(suffix) and (not x.startswith('best')):
            xs = x.replace(suffix, '')
            ckpts.append((x, int(xs)))
    if len(ckpts) == 0:
        return []
    else:
        ckpts.sort(key=lambda x: x[1])
        return ckpts


def get_last_ckpt(ckptdir, device, suffix='_checkpoint.pt', specify=None):
    '''Auxiliary function for getting the latest checkpoint or the specified checkpoint.
    '''
    if specify is not None:
        last_ckpt = torch.load(os.path.join(ckptdir, '{}'.format(specify) + suffix))
    else:
        ckpts = fetch_ckpt_namelist(ckptdir, suffix)
        if len(ckpts) == 0:
            last_ckpt = None
        else:
            last_ckpt = torch.load(os.path.join(ckptdir, ckpts[-1][0]), map_location=device)
    if os.path.exists(os.path.join(ckptdir, 'best' + suffix)):
        best_ckpt = torch.load(os.path.join(ckptdir, 'best' + suffix), map_location=device)
    else:
        best_ckpt = None
    return {
        'last': last_ckpt, 'best': best_ckpt
    }


def save_ckpt(epoch, best_valid_loss, best_valid_epoch, model, optimizer, scheduler, ckptdir,
              prefix, suffix='_checkpoint.pt', max_to_keep=3):
    '''Save checkpoints and keep only latest several checkpoints.
    '''
    ckptdict = {
        'epoch': epoch,
        'best_valid_loss': best_valid_loss,
        'best_valid_epoch': best_valid_epoch,
        'model': model.state_dict(),
        'optimizer': optimizer.state_dict(),
        'scheduler': scheduler.state_dict()
    }
    torch.save(ckptdict, os.path.join(ckptdir, prefix + suffix))
    # remove too old ckpts
    ckpts = fetch_ckpt_namelist(ckptdir, suffix)
    if len(ckpts) > max_to_keep:
        for tdfname, _ in ckpts[:len(ckpts) - max_to_keep]:
            to_del_path = os.path.join(ckptdir, tdfname)
            os.remove(to_del_path)
    return ckptdict


def load_ckpt(model, optimizer, scheduler, ckpt, restore_opt_sche=True):
    epoch = ckpt['epoch']
    best_valid_loss = ckpt['best_valid_loss']
    best_valid_epoch = ckpt['best_valid_epoch']
    try:
        model.load_state_dict(ckpt['model'])
    except:
        model = torch.nn.DataParallel(model)
        model.load_state_dict(ckpt['model'])
        model = model.module
    if restore_opt_sche:
        optimizer.load_state_dict(ckpt['optimizer'])
        scheduler.load_state_dict(ckpt['scheduler'])
    return epoch, best_valid_loss, best_valid_epoch, model, optimizer, scheduler


def print_2way(f, *x):
    print(*x)
    print(*x, file=f)
    f.flush()