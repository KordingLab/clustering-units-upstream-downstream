import torch
import argparse
import pytorch_lightning as pl
from pytorch_lightning.loggers import TensorBoardLogger
from models import LitWrapper
from torch.utils.data import DataLoader
from pathlib import Path
from sys import exit


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    # Trainer config
    parser.add_argument('--device', metavar='DEV', default='auto')
    parser.add_argument('--devices', metavar='CUDA_IDS', default='0123')
    parser.add_argument('--train-val-split', default=0.9, type=float)
    parser.add_argument('--workers', default=2, type=int)
    # Model config
    parser.add_argument('--dataset', metavar='DAT', type=str)
    parser.add_argument('--task', metavar='TSK', type=str)
    parser.add_argument('--l2', default=1e-5, type=float)
    parser.add_argument('--l1', default=0.0, type=float)
    parser.add_argument('--drop', default=0.0, type=float)
    parser.add_argument('--run', default=0, type=int)
    parser.add_argument('--max-epochs', default=500, type=int)
    parser.add_argument('--seed', default=None)
    parser.add_argument('--model-args', default=None, type=str)
    # Environment config
    parser.add_argument('--save-dir', metavar='DIR', required=True, type=Path)
    parser.add_argument('--data-dir', default='data', type=Path)
    parser.add_argument('--batch-size', default=200, type=int)
    args = parser.parse_args()

    if args.model_args is not None:
        try:
            args.model_args = eval(args.model_args)
        except:
            raise ValueError(f"Failed to parse extra args for the model: {args.model_args}")
    else:
        args.model_args = {}

    # Create Pytorch-Lightning wrapper object, which contains logic for managing hyperparameters, datasets, models, etc
    pl_model = LitWrapper(**vars(args))

    # TODO - verify that checkpoint-loading respects RNG state. Otherwise never resume!
    weights_dir = Path(args.save_dir) / pl_model.get_uid() / 'weights'
    print(f"Checkpoints will be stored in {weights_dir}")
    the_checkpoint = weights_dir / 'last.ckpt'
    if not the_checkpoint.exists():
        the_checkpoint = None
    else:
        info = torch.load(the_checkpoint)
        if info['epoch'] >= args.max_epochs:
            print(f"Nothing to do – model is trained up to {args.max_epochs} epochs already!")
            exit(0)

    the_gpu = None
    if args.device == 'auto' and torch.cuda.is_available():
        avail_gpus = [int(d) for d in args.devices]
        the_gpu = [avail_gpus[args.run % len(avail_gpus)]]
        print("AUTOMATICALLY SELECTING GPU:", the_gpu)
    elif args.device in '0123456789':
        the_gpu = [int(args.device)]

    # Get dataset split, and construct Trainer and logger. Note: the train/val split is randomized according to pl_model.hparams.seed
    train, val, test = pl_model.get_dataset(args.data_dir)
    callbacks = [
        pl.callbacks.EarlyStopping(monitor='val_loss', patience=6, mode='min'),
        pl.callbacks.ModelCheckpoint(dirpath=weights_dir, monitor='val_loss', save_last=True, save_top_k=3),
        pl.callbacks.LearningRateMonitor(logging_interval='epoch')
    ]
    tblogger = TensorBoardLogger(args.save_dir, name=pl_model.get_uid(), version=0)
    # Debug - log info to ensure the train/val/test splits are identical for a given run
    tblogger.experiment.add_image('train_0', train[0][0])
    tblogger.experiment.add_image('val_0', val[0][0])
    tblogger.experiment.add_image('test_0', test[0][0])

    # Actually initialize the NN to be trained. Note: this makes use of pl_model.hparams.seed, which by default changes
    # depending on args.run but constant for all other parameters
    pl_model.init_model(set_seed=True, **args.model_args)

    trainer = pl.Trainer(logger=tblogger, callbacks=callbacks, deterministic=True,
                         default_root_dir=args.save_dir, gpus=the_gpu, auto_select_gpus=False,
                         max_epochs=args.max_epochs)
    # TODO - how do we manage seeds here when resuming from checkpoints? Do we need generator=?
    trainer.fit(pl_model,
                train_dataloaders=DataLoader(train, batch_size=args.batch_size, shuffle=True,
                                             pin_memory=True, num_workers=args.workers),
                val_dataloaders=DataLoader(val, batch_size=args.batch_size,
                                           pin_memory=True, num_workers=args.workers))
