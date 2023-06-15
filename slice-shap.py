import json
import os
import sys

sys.path.append('../')

import numpy as np

import tqdm

import torch

from datasets.brain import BrainProcessor, Brain
from datasets.slice.transforms import make_transforms

from utils.gpu import set_gpu

from easydict import EasyDict as edict
from torch.utils.data import DataLoader


hashs = [
    "2023-03-28_20-48-43", # random 100 145
    "2023-03-28_16-36-04", # fixed 50 145
    "2023-03-28_15-40-43", # random 50 145
    "2023-03-27_02-40-00", # random 50 72
    "2023-03-26_21-41-40", # fixed 50 72
]

hash = hashs[-1]

gpus = ["0"]
server = "workstation2"
local_rank = 0
loader_type = 'train' # 'test'
topk = 3
masking = False



config = os.path.join(f'checkpoints/pet-SliceClassification/resnet50/{hash}/configs.json')
with open(config, 'rb') as fb:
    config = json.load(fb)
config = edict(config)

config.model_param = os.path.join(f'checkpoints/pet-SliceClassification/resnet50/{hash}/ckpt.last.pth.tar')

set_gpu(config)
np.random.seed(config.random_state)
torch.manual_seed(config.random_state)
torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.benchmark = True
torch.backends.cudnn.deterministic = False
torch.backends.cudnn.allow_tf32 = True
torch.cuda.set_device(local_rank)


# load data
data_processor = BrainProcessor(root=config.root,
                                data_info=config.data_info,
                                data_type=config.data_type,
                                mci_only=config.mci_only,
                                random_state=config.random_state)
datasets = data_processor.process(n_splits=config.n_splits, n_cv=config.n_cv)

train_transform, test_transform = make_transforms(image_size=config.image_size,
                                                  intensity=config.intensity,
                                                  crop_size=config.crop_size,
                                                  rotate=config.rotate,
                                                  flip=config.flip,
                                                  affine=config.affine,
                                                  blur_std=config.blur_std,
                                                  num_slices=config.num_slices,
                                                  slice_range=config.slice_range,
                                                  prob=config.prob)

train_set = Brain(dataset=datasets['train'], data_type=config.data_type, transform=test_transform)
test_set = Brain(dataset=datasets['test'], data_type=config.data_type, transform=test_transform)

train_loader = DataLoader(dataset=train_set, batch_size=1, drop_last=False)
test_loader = DataLoader(dataset=test_set, batch_size=1, drop_last=False)

#
from models.slice.resnet import resnet50, resnet18
import torch.nn as nn

if config.backbone_type == 'resnet50':
    # TODO: initialization
    network = resnet50(num_classes=2)
elif config.backbone_type == 'resnet18':
    network = resnet18(num_classes=2)
else:
    raise ValueError
network.conv1 = nn.Conv2d(1, 64, kernel_size=7, stride=2, padding=3, bias=False)

if config.small_kernel:
    conv1 = network.conv1
    network.conv1 = nn.Conv2d(conv1.in_channels, conv1.out_channels,
                              kernel_size=3, stride=1, padding=1, bias=False)

ckpt = torch.load(config.model_param, map_location='cpu')
network.load_state_dict(ckpt['network'])

network.to(local_rank)
network.eval()







image_converter = {'sagittal': [], 'coronal': [], 'axial': [], 'confidence': []}
image_nonconverter = {'sagittal': [], 'coronal': [], 'axial': [], 'confidence': []}

if loader_type == 'train':
    loader = train_loader
elif loader_type == 'test':
    loader = test_loader
else:
    raise ValueError

for batch in tqdm.tqdm(loader):
    idx = batch['idx'].item()
    y = batch['y'].item()

    x = torch.concat(batch['x']).float().to(local_rank)
    logits = network(x)
    logits = logits.softmax(1)
    logits = logits.reshape(3, -1, 2).mean(0)

    confidence = logits[0, batch['y'].item()].item()
    confidence_ = "{:.3f}".format(logits[0, batch['y'].item()].item())

    if y == logits.argmax().item():
        a, b, c = x.chunk(3)
        if y == 0:
            image_nonconverter['sagittal'].append(a)
            image_nonconverter['coronal'].append(b)
            image_nonconverter['axial'].append(c)
            image_nonconverter['confidence'].append(torch.tensor(confidence))
        else:
            image_converter['sagittal'].append(a)
            image_converter['coronal'].append(b)
            image_converter['axial'].append(c)
            image_converter['confidence'].append(torch.tensor(confidence))

    if len(image_converter['sagittal']) + len(image_nonconverter['sagittal']) == 100:
        break

converter_idx = torch.tensor(image_converter['confidence']).topk(topk)[1]
nonconverter_idx = torch.tensor(image_nonconverter['confidence']).topk(topk)[1]
import shap

# converter
for view in ['sagittal', 'coronal', 'axial']:

    test_image = torch.concat(image_converter[view])[converter_idx].to(local_rank)

    if masking:
        background1 = torch.concat(image_converter[view]).to(local_rank)
        mask = torch.ones(len(background1), dtype=bool)
        mask[converter_idx] = False
        background1 = background1[mask]
        background2 = torch.concat(image_nonconverter[view]).to(local_rank)

        background = torch.concat([background1, background2]).to(local_rank)
    else:
        background = torch.concat(image_nonconverter[view]).to(local_rank)

    e = shap.DeepExplainer(network, background)
    shap_values = e.shap_values(test_image)

    shap_numpy = [np.swapaxes(np.swapaxes(s, 1, -1), 1, 2) for s in shap_values]
    test_numpy = np.swapaxes(np.swapaxes(test_image.cpu().numpy(), 1, -1), 1, 2)

    # shap.image_plot(shap_numpy, -test_numpy)
    import matplotlib.pyplot as plt
    from shap.plots.colors._colors import red_transparent_blue

    shap_values = shap_numpy
    pixel_values = -test_numpy
    cmap = red_transparent_blue
    status2idx = {'non-converter': 0, 'converter': 1}
    status = 'converter'

    multi_output = True
    if not isinstance(shap_values, list):
        multi_output = False
        shap_values = [shap_values]

    # plot our explanations
    x = pixel_values

    fig_size = np.array([3 * 2, 2.5 * (x.shape[0] + 1)])
    abs_vals = np.stack([np.abs(shap_values[i].sum(-1)) for i in range(len(shap_values))], 0).flatten()
    max_val = np.nanpercentile(abs_vals, 99.9)
    i = status2idx[status]
    sv = shap_values[i].sum(-1)
    im = axes[row, 1].imshow(sv, cmap=cmap, vmin=-max_val, vmax=max_val)
    axes[row, 1].axis('off')
    fig.tight_layout()
    plt.show()
    


# non-converter
for view in ['sagittal', 'coronal', 'axial']:

    test_image = torch.concat(image_nonconverter[view])[converter_idx].to(local_rank)

    if masking:
        background1 = torch.concat(image_nonconverter[view]).to(local_rank)
        mask = torch.ones(len(background1), dtype=bool)
        mask[converter_idx] = False
        background1 = background1[mask]
        background2 = torch.concat(image_converter[view]).to(local_rank)

        background = torch.concat([background1, background2]).to(local_rank)
    else:
        background = torch.concat(image_converter[view]).to(local_rank)


    e = shap.DeepExplainer(network, background)
    shap_values = e.shap_values(test_image)

    shap_numpy = [np.swapaxes(np.swapaxes(s, 1, -1), 1, 2) for s in shap_values]
    test_numpy = np.swapaxes(np.swapaxes(test_image.cpu().numpy(), 1, -1), 1, 2)

    shap.image_plot(shap_numpy, -test_numpy)
