# -*- coding: utf-8 -*-

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from sklearn.neighbors import KNeighborsClassifier
from sklearn.metrics import roc_auc_score
from torchmetrics.functional import precision, recall, confusion_matrix

from utils.logging import get_rich_pbar
from utils.metrics import classification_result


class KNNEvaluator(object):
    def __init__(self,
                 num_neighbors: int or list,
                 num_classes: int,
                 temperature: float = 0.1):
        
        if isinstance(num_neighbors, int):
            self.num_neighbors = [num_neighbors]
        elif isinstance(num_neighbors, list):
            self.num_neighbors = num_neighbors
        else:
            raise NotImplementedError
        self.num_classes = num_classes
        self.temperature = temperature

    @torch.no_grad()
    def evaluate(self,
                 net: nn.Module,
                 memory_loader: torch.utils.data.DataLoader,
                 query_loader: torch.utils.data.DataLoader):
        """
        Evaluate model.
        Arguments:
            net: a `nn.Module` instance.
            memory_loader: a `DataLoader` instance of train data. Apply
                    minimal augmentation as if used for training for linear evaluation.
                    (i.e., HorizontalFlip(0.5), etc.)
            query_loader: a `DataLoader` instance of test data. Apply
                    minimal data augmentation as used for testing for linear evaluation.
                    (i.e., Resize + Crop (0.875 x size), etc.)
        """

        net.eval()
        device = next(net.parameters()).device

        with get_rich_pbar(transient=True, auto_refresh=False) as pg:

            desc_1 = "[bold yellow] Extracting features..."
            task_1 = pg.add_task(desc_1, total=len(memory_loader))
            desc_2 = f"[bold cyan] {self.num_neighbors}-NN score: "
            task_2 = pg.add_task(desc_2, total=len(query_loader))

            # 1. Extract memory features (train data to compare against)
            memory_bank, memory_labels = [], []
            for _, batch in enumerate(memory_loader):
                z = net(batch['x'].to(device, non_blocking=True))
                memory_bank += [F.normalize(z, dim=1)]
                memory_labels += [batch['y'].to(device)]
                pg.update(task_1, advance=1.)

            memory_bank = torch.cat(memory_bank, dim=0).T
            memory_labels = torch.cat(memory_labels, dim=0)

            # 2. Extract query features (test data to evaluate) and
            #  and evalute against memory features.
            scores = dict()
            corrects = [0] * len(self.num_neighbors)
            for _, batch in enumerate(query_loader):
                z = F.normalize(net(batch['x'].to(device)), dim=1)
                y = batch['y'].to(device)
                for i, k in enumerate(self.num_neighbors):
                    y_pred = self.predict(k,
                                          query=z,
                                          memory_bank=memory_bank,
                                          memory_labels=memory_labels)[:, 0].squeeze()
                    corrects[i] += y.eq(y_pred).sum().item()
                pg.update(task_2, advance=1.) 
            
            for i, k in enumerate(self.num_neighbors):
                scores[f'knn@{k}'] = corrects[i] / len(query_loader.dataset)

            return scores  # dict

    @torch.no_grad()
    def predict(self,
                k: int,
                query: torch.FloatTensor,
                memory_bank: torch.FloatTensor,
                memory_labels: torch.LongTensor):

        C = self.num_classes
        T = self.temperature
        B, _ = query.size()

        # Compute cosine similarity
        sim_matrix = torch.einsum('bf,fm->bm', [query, memory_bank])       # (b, f) @ (f, M) -> (b, M)
        sim_weight, sim_indices = sim_matrix.sort(dim=1, descending=True)  # (b, M), (b, M)
        sim_weight, sim_indices = sim_weight[:, :k], sim_indices[:, :k]    # (b, k), (b, k)
        sim_weight = (sim_weight / T).exp()                                # (b, k)
        sim_labels = torch.gather(
            memory_labels.expand(B, -1),                                   # (1, M) -> (b, M)
            dim=1,
            index=sim_indices
        )                                                                  # (b, M)

        one_hot = torch.zeros(B * k, C, device=sim_labels.device)          # (bk, C)
        sim_labels = sim_labels.type(torch.int64)                          # error occurred in imagenet32
        one_hot.scatter_(dim=-1, index=sim_labels.view(-1, 1), value=1)    # (bk, C) <- scatter <- (bk, 1)
        pred = one_hot.view(B, k, C) * sim_weight.unsqueeze(dim=-1)        # (b, k, C) * (b, k, 1)
        pred = pred.sum(dim=1)                                             # (b, C)

        return pred.argsort(dim=-1, descending=True)                       # (b, C); first column gives label of highest confidence


class BinaryKNN(object):

    def __init__(self, num_classes, num_neighbors, threshold=0.5):
        self.num_classes = num_classes
        if isinstance(num_neighbors, int):
            self.num_neighbors = [num_neighbors]
        elif isinstance(num_neighbors, list):
            self.num_neighbors = num_neighbors
        else:
            raise NotImplementedError
        self.threshold = threshold

    @torch.no_grad()
    def evaluate(self, net, train_loader, test_loader, adjusted=False):

        net.eval()
        device = next(net.parameters()).device

        # extract features
        with get_rich_pbar(transient=True, auto_refresh=False) as pg:

            desc = "[bold yellow] Extracting features..."
            task = pg.add_task(desc, total=len(train_loader) + len(test_loader))

            # 1. Extract features of training data
            train_z, labels_train = [], []
            for batch in train_loader:
                z = net(batch['x'].to(device, non_blocking=True))
                train_z += [F.normalize(z, dim=1).cpu().numpy()]
                labels_train += [batch['y'].cpu().numpy()]
                pg.update(task, advance=1.)
            train_z = np.concatenate(train_z, axis=0)
            labels_train = np.concatenate(labels_train, axis=0)

            # 2. Extract features of testing data
            test_z, labels_test = [], []
            for batch in test_loader:
                z = net(batch['x'].to(device, non_blocking=True))
                test_z += [F.normalize(z, dim=1).cpu().numpy()]
                labels_test += [batch['y'].to(device).cpu().numpy()]
                pg.update(task, advance=1.)
            test_z = np.concatenate(test_z, axis=0)
            labels_test = np.concatenate(labels_test, axis=0)

        # k-nn
        scores = dict()
        for num_neighbor in self.num_neighbors:
            knn = KNeighborsClassifier(n_neighbors=num_neighbor, metric='cosine')
            knn.fit(train_z, labels_train)
            y_pred = knn.predict_proba(test_z)
            result = classification_result(y_true=labels_test,
                                           y_pred=y_pred,
                                           adjusted=adjusted)
            scores[f'knn@{num_neighbor}'] = result
        return scores
