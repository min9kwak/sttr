echo "Experiments Started"
SERVER=dgx
GPUS=0

DATA_TYPE=mri
RANDOM_STATE=2021

INTENSITY=scale

BATCH_SIZE=16
EPOCHS=100

BACKBONE_TYPE=densenet
INIT_FEATURES=32
GROWTH_RATE=32
BLOCK_CONFIG="6,12,24,16"

PROJECTOR_DIM=128
NUM_NEGATIVES=1024

for EPOCHS in 100
do
	for LEARNING_RATE in 0.01
	do
		python ./run_moco.py \
		--gpus $GPUS \
		--server $SERVER \
		--data_type $DATA_TYPE \
		--root /raidWorkspace/mingu/Data/ADNI \
		--data_info labels/data_info.csv \
		--train_size 0.9 \
		--image_size 96 \
		--random_state $RANDOM_STATE \
		--intensity $INTENSITY \
		--rotate \
		--flip \
		--blur \
		--blur_std 0.1 \
		--backbone_type $BACKBONE_TYPE \
		--init_features $INIT_FEATURES \
		--growth_rate $GROWTH_RATE \
		--block_config=$BLOCK_CONFIG \
		--bn_size 4 \
		--dropout_rate 0.0 \
		--epochs $EPOCHS \
		--batch_size $BATCH_SIZE \
		--optimizer sgd \
		--learning_rate $LEARNING_RATE \
		--weight_decay 0.0001 \
		--cosine_warmup 0 \
		--cosine_cycles 1 \
		--cosine_min_lr 0.0 \
		--save_every 20 \
		--enable_wandb \
		--projector_dim $PROJECTOR_DIM \
		--num_negatives $NUM_NEGATIVES \
		--split_bn
	done
done
echo "Finished."