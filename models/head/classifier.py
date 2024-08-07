import collections

import torch
import torch.nn as nn

from models.head.base import HeadBase
from utils.initialization import initialize_weights


class LinearClassifier(HeadBase):
    def __init__(self,
                 in_channels: int,
                 num_classes: int,
                 activation: bool = True,
                 dropout: float = 0.0):
        """
        Arguments:
            in_channels: int, number of input feature maps.
            num_classes: int, number of output features.
        """
        super(LinearClassifier, self).__init__()

        self.in_channels = in_channels
        self.num_classes = num_classes
        self.activation = activation
        self.dropout = dropout
        self.layers = self.make_layers(
            in_channels=self.in_channels,
            num_classes=self.num_classes,
            activation=self.activation,
            dropout=self.dropout,
        )
        initialize_weights(self.layers)

    @staticmethod
    def make_layers(in_channels: int, num_classes: int, activation: bool = False, dropout: float = 0.0):
        layers = [
            ('gap', nn.AdaptiveAvgPool3d(1)),
            ('flatten', nn.Flatten(1)),
            ('dropout', nn.Dropout(p=dropout)),
            ('linear', nn.Linear(in_channels, num_classes))
        ]
        if activation:
            layers.insert(0, ('relu', nn.ReLU(inplace=True)))
        layers = nn.Sequential(collections.OrderedDict(layers))

        return layers

    def forward(self, x: torch.Tensor):
        return self.layers(x)

    @property
    def num_parameters(self):
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


class LinearDemoClassifier(HeadBase):
    def __init__(self,
                 image_dims: int,
                 demo_dims: int,
                 num_classes: int,
                 activation: bool = True,
                 dropout: float = 0.0):
        super(LinearDemoClassifier, self).__init__()

        self.image_dims = image_dims
        self.demo_dims = demo_dims
        self.num_classes = num_classes
        self.activation = activation
        self.dropout = dropout

        self.image_layer, self.classifier = self.make_layers(
            image_dims=self.image_dims,
            demo_dims=self.demo_dims,
            num_classes=self.num_classes,
            activation=self.activation,
            dropout=self.dropout
        )
        initialize_weights(self.image_layer)
        initialize_weights(self.classifier)

    @staticmethod
    def make_layers(image_dims: int, demo_dims: int, num_classes: int, activation: bool = False, dropout: float = 0.0):

        image_layer = [
            ('gap', nn.AdaptiveAvgPool3d(1)),
            ('flatten', nn.Flatten(1)),
        ]
        if activation:
            image_layer.insert(0, ('relu', nn.ReLU(inplace=True)))
        image_layer = nn.Sequential(collections.OrderedDict(image_layer))

        classifier = nn.Sequential(collections.OrderedDict([
            ('dropout', nn.Dropout(p=dropout)),
            ('linear', nn.Linear(image_dims + demo_dims, num_classes))
        ]))
        return image_layer, classifier

    def forward(self, image: torch.Tensor, demo: torch.Tensor):
        h1 = self.image_layer(image)
        h = torch.concat([h1, demo], dim=1)
        logit = self.classifier(h)
        return logit

    @property
    def num_parameters(self):
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


class MLPClassifier(HeadBase):
    def __init__(self,
                 in_channels: int,
                 num_classes: int,
                 activation: bool = True,
                 dropout: float = 0.0):
        """
        Arguments:
            in_channels: int, number of input feature maps.
            num_classes: int, number of output features.
        """
        super(MLPClassifier, self).__init__()

        self.in_channels = in_channels
        self.num_classes = num_classes
        self.activation = activation
        self.dropout = dropout
        self.layers = self.make_layers(
            in_channels=self.in_channels,
            num_classes=self.num_classes,
            activation=self.activation,
            dropout=self.dropout,
        )
        initialize_weights(self.layers)

    @staticmethod
    def make_layers(in_channels: int, num_classes: int, activation: bool = False, dropout: float = 0.0):
        layers = [
            ('gap', nn.AdaptiveAvgPool3d(1)),
            ('flatten', nn.Flatten(1)),
            ('dropout', nn.Dropout(p=dropout)),
            ('linear1', nn.Linear(in_channels, in_channels)),
            ('relu1', nn.ReLU(inplace=True)),
            ('linear2', nn.Linear(in_channels, num_classes)),
        ]
        if activation:
            layers.insert(0, ('relu', nn.ReLU(inplace=True)))
        layers = nn.Sequential(collections.OrderedDict(layers))

        return layers

    def forward(self, x: torch.Tensor):
        return self.layers(x)

    @property
    def num_parameters(self):
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


if __name__ == '__main__':

    from models.backbone.base import calculate_out_features
    from models.backbone.resnet import build_resnet_backbone
    from models.backbone.densenet import DenseNetBackbone

    arr = torch.randn(size=(5, 1, 96, 96, 96))

    backbone = build_resnet_backbone(arch=50, no_max_pool=False, in_channels=1, semi=False)
    dims = calculate_out_features(backbone, 1, 96)
    resnet_classifier = LinearClassifier(in_channels=dims, num_classes=2, activation=False, dropout=0.0)
    logits = resnet_classifier(backbone(arr))

    backbone = DenseNetBackbone(in_channels=1)
    dims = calculate_out_features(backbone, 1, 96)
    densenet_classifier = LinearClassifier(in_channels=dims, num_classes=2, activation=False, dropout=0.0)
    logits = densenet_classifier(backbone(arr))
