echo "Experiments Started"
SERVER=main
GPUS=0

DATA_TYPE=pet
SEGMENT=global
IMAGE_SIZE=98
RANDOM_STATE=2021

INTENSITY=scale

BATCH_SIZE=16
EPOCHS=100

BACKBONE_TYPE=resnet
ARCH=50


for RANDOM_STATE in 2021 2022 2023
do
	for LEARNING_RATE in 0.0001
	do
		python ../../run_classification.py \
		--gpus $GPUS \
		--server $SERVER \
		--data_type $DATA_TYPE \
		--root D:/data/ADNI \
		--data_info labels/data_info.csv \
		--mci_only \
		--train_size 0.9 \
		--segment $SEGMENT \
		--image_size $IMAGE_SIZE \
		--random_state $RANDOM_STATE \
		--intensity $INTENSITY \
		--rotate \
		--flip \
		--blur \
		--blur_std 0.1 \
		--prob 0.5 \
		--backbone_type $BACKBONE_TYPE \
		--arch $ARCH \
		--epochs $EPOCHS \
		--batch_size $BATCH_SIZE \
		--optimizer adamw \
		--learning_rate $LEARNING_RATE \
		--weight_decay 0.0001 \
		--cosine_warmup 0 \
		--cosine_cycles 1 \
		--cosine_min_lr 0.0 \
		--save_every 200 \
		--enable_wandb \
		--balance
	done
done
echo "Finished."
