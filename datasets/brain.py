import os
import pandas as pd
import numpy as np
import pickle

import random
import torch
from torch.utils.data import Dataset
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.utils import class_weight


class BrainProcessor(object):
    def __init__(self,
                 root: str,
                 data_info: str,
                 data_type: str = 'mri',
                 mci_only: bool = False,
                 random_state: int = 2022):

        self.root = root
        # TODO: include multi... define __getitem__ = self.something
        assert data_type in ['mri', 'pet']
        self.data_type = data_type

        self.data_info = pd.read_csv(os.path.join(root, data_info), converters={'RID': str, 'Conv': int})
        self.data_info = self.data_info.loc[self.data_info.IS_FILE]
        if mci_only:
            self.data_info = self.data_info.loc[self.data_info.MCI == 1]

        if self.data_type == 'mri':
            self.data_info = self.data_info[~self.data_info.MRI.isna()]
        else:
            self.data_info = self.data_info[~self.data_info.PET.isna()]

        # unlabeled and labeled
        self.u_data_info = self.data_info[self.data_info['Conv'].isin([-1])].reset_index(drop=True)
        self.data_info = self.data_info[self.data_info['Conv'].isin([0, 1])].reset_index(drop=True)

        self.random_state = random_state
        self.demo_columns = ['PTGENDER (1=male, 2=female)', 'Age', 'PTEDUCAT',
                             'APOE Status', 'MMSCORE', 'CDGLOBAL', 'SUM BOXES']

    def process(self, n_splits=10, n_cv=0):

        # prepare
        rid = self.data_info.RID.tolist()
        conv = self.data_info.Conv.tolist()
        cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=self.random_state)
        assert 0 <= n_cv < n_splits

        # train-test split
        train_idx_list, test_idx_list = [], []
        for train_idx, test_idx in cv.split(X=rid, y=conv, groups=rid):
            train_idx_list.append(train_idx)
            test_idx_list.append(test_idx)

        train_idx, test_idx = train_idx_list[n_cv], test_idx_list[n_cv]
        train_info = self.data_info.iloc[train_idx].reset_index(drop=True)
        test_info = self.data_info.iloc[test_idx].reset_index(drop=True)

        # filter rids in unlabeled data
        test_rid = list(set(test_info.RID))
        self.u_data_info = self.u_data_info[~self.u_data_info.RID.isin(test_rid)]
        u_train_info = self.u_data_info.reset_index(drop=True)

        # parse to make paths
        train_data = self.parse_data(train_info)
        test_data = self.parse_data(test_info)
        u_train_data = self.parse_data(u_train_info)

        # set class weight
        self.class_weight = class_weight.compute_class_weight(class_weight='balanced',
                                                              classes=np.unique(train_data['y']),
                                                              y=train_data['y'])

        datasets = {'train': train_data,
                    'test': test_data,
                    'u_train': u_train_data}

        return datasets

    # TODO: demo -> numeric
    def parse_data(self, data_info):
        mri_files = [self.str2mri(p) if type(p) == str else p for p in data_info.MRI]
        pet_files = [self.str2pet(p) if type(p) == str else p for p in data_info.PET]
        # demo = data_info[self.demo_columns].values
        y = data_info.Conv.values
        # return dict(mri=mri_files, pet=pet_files, demo=demo, y=y)
        return dict(mri=mri_files, pet=pet_files, y=y)

    def str2mri(self, i):
        return os.path.join(self.root, 'template/FS7', f'{i}.pkl')

    def str2pet(self, i):
        return os.path.join(self.root, 'template/PUP_FBP', f'{i}.pkl')


class BrainBase(Dataset):

    def __init__(self,
                 dataset: dict,
                 data_type: str,
                 **kwargs):
        self.mri = dataset['mri']
        self.pet = dataset['pet']
        # self.demo = dataset['demo']
        self.y = dataset['y']

        assert data_type in ['mri', 'pet']
        self.data_type = data_type

        if self.data_type == 'mri':
            self.paths = self.mri
        elif self.data_type == 'pet':
            self.paths = self.pet
        else:
            raise ValueError

    def __len__(self):
        return len(self.y)

    @staticmethod
    def load_image(path):
        with open(path, 'rb') as fb:
            image = pickle.load(fb)
        return image


class Brain(BrainBase):

    def __init__(self, dataset, data_type, transform, **kwargs):
        super().__init__(dataset, data_type, **kwargs)
        self.transform = transform

    def __getitem__(self, idx):
        img = self.load_image(path=self.paths[idx])
        if self.transform is not None:
            img = self.transform(img)
        # demo = self.demo[idx]
        y = self.y[idx]
        return dict(x=img, y=y, idx=idx)
        # return dict(x=img, demo=demo, y=y, idx=idx)


class BrainMoCo(BrainBase):

    def __init__(self, dataset, data_type, query_transform, key_transform, **kwargs):
        super().__init__(dataset, data_type, **kwargs)
        self.query_transform = query_transform
        self.key_transform = key_transform

    def __getitem__(self, idx):
        img = self.load_image(path=self.paths[idx])
        x1 = self.query_transform(img)
        x2 = self.key_transform(img)
        # demo = self.demo[idx]
        y = self.y[idx]
        return dict(x1=x1, x2=x2, y=y, idx=idx)
        # return dict(x1=x1, x2=x2, demo=demo, y=y, idx=idx)


class BrainMixUp(BrainBase):

    def __init__(self, dataset, data_type, transform, alpha, **kwargs):
        super().__init__(dataset, data_type, **kwargs)
        self.transform = transform
        self.alpha = alpha

    def __getitem__(self, idx):

        # one-hot label
        y = torch.zeros(2)
        y[self.y[idx]] = 1.0

        # original image
        img = self.load_image(path=self.paths[idx])
        if self.transform is not None:
            img = self.transform(img)

        # mixup image
        idx_mix = random.randint(0, self.__len__() - 1)
        y_mix = torch.zeros(2)
        y_mix[self.y[idx_mix]] = 1.0

        img_mix = self.load_image(path=self.paths[idx_mix])
        if self.transform is not None:
            img_mix = self.transform(img_mix)

        lam = np.random.beta(self.alpha, self.alpha)
        img_mix = lam * img + (1 - lam) * img_mix
        y_mix = lam * y + (1 - lam) * y_mix

        return dict(x=img, x_mix=img_mix, y=y, y_mix=y_mix, idx=idx, idx_mix=idx_mix)
