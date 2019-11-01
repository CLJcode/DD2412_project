import argparse
import logging
import os

import numpy as np
import torch
import torch.nn as nn
from skimage import img_as_ubyte
from torch import optim
from torch.autograd import Variable
from torch.optim.lr_scheduler import MultiStepLR
from torch.utils.data import DataLoader

import Loss as loss_func
import generate_data as gd
from Networks.VDN import VDN_NET
from Networks.VDN_conf1 import VDN_NET as VDN_NET_one
from tools import calc_MSE, calculate_ssim, psnr

"""
Training Simulated,  Model VDN


"""


def training_model_VDN(arg, milestones):
    logging.basicConfig(filename=arg.log_path, level=logging.INFO)  # log file
    logging.info('Started')
    if not os.path.exists(arg.model_path):
        os.makedirs(arg.model_path)
    # load training data
    train_sets = {"BSDS500": "*.jpg", "Waterloo": "*.bmp", "ImageNet": "*.JPEG"}
    if arg.confnet == 1:
        model = VDN_NET_one(in_channels=arg.channels, depth_snet=arg.snet)
    else:
        model = VDN_NET(in_channels=arg.channels, depth_snet=arg.snet)
    model = model.float()
    train_paths = np.array(sorted(gd.load_data("datasets", train_sets)))
    ran = bool(arguments.randomize)
    noise = bool(arguments.noise)
    clipping = bool(arguments.clipping)
    data_obj = gd.TrainDataset(train_paths, randomize=ran, extra_noise=noise, patch_size=arg.patch)
    if torch.cuda.is_available():
        model.cuda()
        torch.backends.cudnn.benchmark = True
        data = DataLoader(dataset=data_obj, drop_last=True, batch_size=arg.batch, shuffle=True, num_workers=arg.workers)
    else:
        data = DataLoader(dataset=data_obj, drop_last=True, batch_size=arg.batch, shuffle=True)

    # network parameters
    epsilon = np.sqrt(5.0e-6)
    p_window = 7
    optimizer = optim.Adam(model.parameters(), lr=2e-4)
    scheduler = MultiStepLR(optimizer, milestones=milestones, gamma=arg.gamma)
    if clipping:
        gadient_clip_Dnet = 1000.0
        gadient_clip_Snet = 50.0
        Dnet_parameters = [x for name, x in model.named_parameters() if 'dnet' in name.lower()]
        Snet_parameters = [x for name, x in model.named_parameters() if 'snet' in name.lower()]

    print("Training model simulation now!")

    for epoch in range(arg.epochs):
        if clipping:
            grad_D = 0.0
            grad_S = 0.0
        epoch_avg_loss = 0.0
        mse_avg = 0.0
        psnr_avg = 0.0
        ssim_avg = 0.0
        lr = optimizer.param_groups[0]['lr']
        if lr < arg.learning:
            print("reach min learning rate at epoch" + str(epoch))
        for i, batch_data in enumerate(data):
            if torch.cuda.is_available():
                x_batch, y_batch, sigma_arr = Variable(batch_data[0]).cuda(), Variable(batch_data[1]).cuda(), Variable(
                    batch_data[2]).cuda()
            else:
                x_batch, y_batch, sigma_arr = batch_data[0], batch_data[1], batch_data[2]
            optimizer.zero_grad()
            out_D, out_s = model(y_batch)
            loss, loglikelihood, kl_z, kl_sigma = loss_func.get_loss(x_batch, y_batch, sigma_arr, p_window,
                                                                     out_D[:, :arg.channels, :, :],
                                                                     out_D[:, arg.channels:, :, :],
                                                                     out_s[:, :arg.channels, :, :],
                                                                     out_s[:, arg.channels:, :, :], epsilon)
            loss.backward()
            if clipping:
                full_grad_D = nn.utils.clip_grad_norm_(Dnet_parameters, gadient_clip_Dnet)
                full_grad_S = nn.utils.clip_grad_norm_(Snet_parameters, gadient_clip_Snet)
                grad_D = (grad_D * (i / (i + 1)) + full_grad_D / (i + 1))
                grad_S = (grad_S * (i / (i + 1)) + full_grad_S / (i + 1))
            optimizer.step()
            epoch_avg_loss += loss.detach().item()
            predicted_image = y_batch - out_D[:, :arg.channels, :, :].detach().data
            predicted_image = predicted_image.clamp(0, 1)
            mse = calc_MSE(predicted_image, x_batch)
            mse_avg += mse
            psnr_avg += psnr(predicted_image * 255, x_batch * 255)
            ssim_avg += calculate_ssim(img_as_ubyte(predicted_image.permute(2, 3, 1, 0).cpu().numpy()),
                                       img_as_ubyte(x_batch.permute(2, 3, 1, 0).cpu().numpy()), multichannel=True)
            if i == 0:
                print("First ForwardPAss\n Loss: {}, MSE: {}".format(loss.detach().item(), mse))
            if (i + 1) % 100 == 0:
                print("{} - Loss: {}, MSE:{}, epoch:{}".format(i + 1, loss.item(), mse, epoch + 1))
            if i >= 5000:
                break
        if clipping:
            gadient_clip_Dnet = min(gadient_clip_Dnet, grad_D)
            gadient_clip_Dnet = min(gadient_clip_Dnet, grad_S)
        print("----------------------------------------------------------")
        print("Epoch: {},  Avg MSE:{},  Avg Epoch Loss:{},  Avg PSNR:{}, Avg SSIM : {}, LR:{}".format(epoch + 1,
                                                                                                      mse_avg / (i + 1),
                                                                                                      epoch_avg_loss / (
                                                                                                              i + 1),
                                                                                                      psnr_avg / (
                                                                                                              i + 1),
                                                                                                      ssim_avg / (
                                                                                                              i + 1),
                                                                                                      lr))
        logging.info("av loss: {}, epoch: {}".format(epoch_avg_loss / (i + 1), epoch + 1))
        scheduler.step()
        if epoch % arguments.epoch_save == 0:
            torch.save(model.state_dict(), os.path.join(arg.model_path, "model_" + str(epoch) + "_epochs.pth"))
            print("saved model as" + arg.model_path)
    print("Finished Training...\n Saving model now.....\n")
    torch.save(model.state_dict(), os.path.join(arg.model_path, "final_model.pth"))
    print("saved model as" + os.path.join(arg.model_path, "final_model.pth"))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Script to train VDN')
    parser.add_argument('--epochs', '-e', type=int, default=60, help='total epochs')
    parser.add_argument('--channels', '-chn', type=int, default=3, help='Number of channels for an image')
    parser.add_argument('--learning', '-lr', type=float, default=1e-6, help='min learning rate')
    parser.add_argument('--model_path', '-path', type=str, default="trained_models",
                        help='Folder to save trained models')
    parser.add_argument('--randomize', '-ran', type=int, default=1, help='use 1 for true, 0 for false')
    parser.add_argument('--noise', '-noise', type=int, default=1, help='extra noise. use 1 for true, 0 for false')
    parser.add_argument('--log_path', '-log', type=str, default='model_training.log',
                        help='path of the log file from model loss')
    parser.add_argument('--snet', '-snet', type=int, default=5, help='Depth of SNet')
    parser.add_argument('--batch', '-bch', type=int, default=64, help='Batch size')
    parser.add_argument('--patch', '-pch', type=int, default=128, help='Patch size')
    parser.add_argument('--gamma', '-gm', type=float, default=0.5, help='Gamma for learning rate')
    parser.add_argument('--clipping', '-clip', type=int, default=1, help='Gradient clipping, 0 for False, 1 for True')
    parser.add_argument('--epoch_save', '-svepoch', type=int, default=10,
                        help='Frequency of saving trained model according to the poch')
    parser.add_argument('--workers', '-wks', type=int, default=4, help='Number of workers for dataloader')
    parser.add_argument('--confnet', '-cfnet', type=int, default=0, help='if 1, use configuration 1')

    arguments = parser.parse_args()
    milestones = [10, 20, 25, 30, 35, 40, 45, 50]
    training_model_VDN(arguments, milestones)
