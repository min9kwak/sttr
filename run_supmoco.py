# -*- coding: utf-8 -*-

import os
import sys
import time
import rich
import numpy as np
import wandb

import torch
import torch.multiprocessing as mp
import torch.distributed as dist

from configs.supmoco import SupMoCoConfig
from tasks.supmoco import SupMoCo, MemoryQueue, SupMoCoLoss

from models.backbone.base import calculate_out_features
from models.backbone.densenet import DenseNetBackbone
from models.backbone.resnet import build_resnet_backbone
from models.head.projector import MLPHead
from layers.batchnorm import SplitBatchNorm3d

from datasets.mri import MRI, MRIMoCo, MRIProcessor
from datasets.pet import PET, PETMoCo, PETProcessor

from datasets.transforms import make_transforms, compute_statistics

from utils.logging import get_rich_logger


def main():
    """Main function for single/distributed linear classification."""

    config = SupMoCoConfig.parse_arguments()

    config.task = config.data_type + f'-supmoco-{config.segment}'

    os.environ['CUDA_VISIBLE_DEVICES'] = ','.join([str(gpu) for gpu in config.gpus])
    num_gpus_per_node = len(config.gpus)
    world_size = config.num_nodes * num_gpus_per_node
    distributed = world_size > 1
    setattr(config, 'num_gpus_per_node', num_gpus_per_node)
    setattr(config, 'world_size', world_size)
    setattr(config, 'distributed', distributed)

    # str -> list arguments
    if config.model_name == 'densenet':
        setattr(config, 'block_config', tuple(int(a) for a in config.block_config.split(',')))
    setattr(config, 'knn_k', [int(a) for a in config.knn_k.split(',')])
    setattr(config, 'alphas', [float(a) for a in config.alphas.split(',')])
    setattr(config, 'alphas_min', [float(a) for a in config.alphas_min.split(',')])
    setattr(config, 'alphas_decay_end', [int(a) for a in config.alphas_decay_end.split(',')])

    rich.print(config.__dict__)
    config.save()

    np.random.seed(config.random_state)
    torch.manual_seed(config.random_state)

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.benchmark = True
    torch.backends.cudnn.deterministic = False
    torch.backends.cudnn.allow_tf32 = True

    if config.distributed:
        rich.print(f"Distributed training on {world_size} GPUs.")
        mp.spawn(
            main_worker,
            nprocs=config.num_gpus_per_node,
            args=(config, )
        )
    else:
        rich.print(f"Single GPU training.")
        main_worker(0, config=config)  # single machine, single gpu


def main_worker(local_rank: int, config: object):
    """Single process."""

    torch.cuda.set_device(local_rank)
    if config.distributed:
        dist_rank = config.node_rank * config.num_gpus_per_node + local_rank
        dist.init_process_group(
            backend=config.dist_backend,
            init_method=config.dist_url,
            world_size=config.world_size,
            rank=dist_rank,
        )

    config.batch_size = config.batch_size // config.world_size
    config.num_workers = config.num_workers // config.num_gpus_per_node

    # logging
    if local_rank == 0:
        logfile = os.path.join(config.checkpoint_dir, 'main.log')
        logger = get_rich_logger(logfile=logfile)
        if config.enable_wandb:
            wandb.init(
                name=f'{config.model_name} : {config.hash}',
                project=f'sttr-{config.task}',
                config=config.__dict__
            )
    else:
        logger = None

    # Networks
    if config.model_name == 'densenet':
        backbone = DenseNetBackbone(in_channels=1,
                                    init_features=config.init_features,
                                    growth_rate=config.growth_rate,
                                    block_config=config.block_config,
                                    bn_size=config.bn_size,
                                    dropout_rate=config.dropout_rate,
                                    semi=False)
    elif config.model_name == 'resnet':
        backbone = build_resnet_backbone(arch=config.arch,
                                         no_max_pool=config.no_max_pool,
                                         in_channels=1,
                                         semi=False)
    else:
        raise NotImplementedError

    if config.small_kernel:
        backbone._fix_first_conv()

    out_dim = calculate_out_features(backbone=backbone, in_channels=1, image_size=config.image_size)
    projector = MLPHead(out_dim, config.projector_dim)

    if config.split_bn:
        backbone = SplitBatchNorm3d.convert_split_batchnorm(backbone)

    # load data
    if config.data_type == 'mri':
        PROCESSOR = MRIProcessor
        DATAMoCo = MRIMoCo
        DATA = MRI
    elif config.data_type == 'pet':
        PROCESSOR = PETProcessor
        DATAMoCo = PETMoCo
        DATA = PET
    else:
        raise NotImplementedError

    data_processor = PROCESSOR(root=config.root,
                               data_info=config.data_info,
                               mci_only=config.mci_only,
                               segment=config.segment,
                               random_state=config.random_state)

    datasets = data_processor.process(train_size=config.train_size)

    if config.intensity == 'normalize':
        mean_std = compute_statistics(DATA=DATA, normalize_set=datasets['train'])
    else:
        mean_std = (None, None)

    train_transform, test_transform = make_transforms(image_size=config.image_size,
                                                      intensity=config.intensity,
                                                      mean_std=mean_std,
                                                      rotate=config.rotate,
                                                      flip=config.flip,
                                                      zoom=config.zoom,
                                                      blur=config.blur,
                                                      blur_std=config.blur_std,
                                                      prob=config.prob)

    train_set = {'path': datasets['train']['path'] + datasets['u_train']['path'],
                 'y': np.concatenate([datasets['train']['y'], datasets['u_train']['y']])}
    train_set = DATAMoCo(dataset=train_set, pin_memory=config.pin_memory,
                         query_transform=train_transform, key_transform=train_transform)
    eval_set = DATA(dataset=datasets['train'], pin_memory=config.pin_memory, transform=test_transform)
    test_set = DATA(dataset=datasets['test'], pin_memory=config.pin_memory, transform=test_transform)

    # Model (Task)
    model = SupMoCo(backbone=backbone,
                    head=projector,
                    queue=MemoryQueue(size=(config.projector_dim, config.num_negatives), device=local_rank),
                    loss_function=SupMoCoLoss(temperature=config.temperature))
    model.prepare(
        checkpoint_dir=config.checkpoint_dir,
        optimizer=config.optimizer,
        learning_rate=config.learning_rate,
        weight_decay=config.weight_decay,
        cosine_warmup=config.cosine_warmup,
        cosine_cycles=config.cosine_cycles,
        cosine_min_lr=config.cosine_min_lr,
        epochs=config.epochs,
        batch_size=config.batch_size,
        num_workers=config.num_workers,
        key_momentum=config.key_momentum,
        distributed=config.distributed,
        local_rank=local_rank,
        mixed_precision=config.mixed_precision,
        enable_wandb=config.enable_wandb,
        alphas=config.alphas,
        alphas_min=config.alphas_min,
        alphas_decay_end=config.alphas_decay_end
    )

    # Train & evaluate
    start = time.time()
    model.run(
        dataset=train_set,
        memory_set=eval_set,
        query_set=test_set,
        save_every=config.save_every,
        logger=logger,
        knn_k=config.knn_k
    )
    elapsed_sec = time.time() - start

    if logger is not None:
        elapsed_mins = elapsed_sec / 60
        logger.info(f'Total training time: {elapsed_mins:,.2f} minutes.')
        logger.handlers.clear()


if __name__ == '__main__':

    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)