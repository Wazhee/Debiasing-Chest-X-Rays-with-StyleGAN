# Download the model
import argparse
import numpy as np
import PIL.Image
import dnnlib
import re
import sys
from io import BytesIO
import IPython.display
import numpy as np
from math import ceil
from PIL import Image, ImageDraw
import imageio
import legacy
import cv2
import torch
from tqdm import tqdm
import pandas as pd
import os
import matplotlib.pyplot as plt
import random
from sklearn.svm import LinearSVC
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import shutil


device = torch.device('cuda')

# Choose between these pretrained models
# https://nvlabs-fi-cdn.nvidia.com/stylegan2-ada-pytorch/pretrained/afhqcat.pkl
# https://nvlabs-fi-cdn.nvidia.com/stylegan2-ada-pytorch/pretrained/afhqdog.pkl
# https://nvlabs-fi-cdn.nvidia.com/stylegan2-ada-pytorch/pretrained/afhqwild.pkl
# https://nvlabs-fi-cdn.nvidia.com/stylegan2-ada-pytorch/pretrained/brecahad.pkl
# https://nvlabs-fi-cdn.nvidia.com/stylegan2-ada-pytorch/pretrained/cifar10.pkl
# https://nvlabs-fi-cdn.nvidia.com/stylegan2-ada-pytorch/pretrained/ffhq.pkl
# https://nvlabs-fi-cdn.nvidia.com/stylegan2-ada-pytorch/pretrained/metfaces.pkl
save_dir = "../datasets/augmented_age/"
if not os.path.exists(save_dir):
    os.makedirs(save_dir)
path2latents = '../synthetic_images/latents/'
network_pkl = "../stylegan3/results/00022-stylegan3-t-nih_chexpert-gpus4-batch16-gamma1/network-snapshot-004000.pkl"
# If downloads fails, you can try downloading manually and uploading to the session directly 
# network_pkl = "/content/ffhq.pkl"

print('Loading networks from "%s"...' % network_pkl)
with dnnlib.util.open_url(network_pkl) as f:
  G = legacy.load_network_pkl(f)['G_ema'].to(device) # type: ignore

n_samples = 5e5
def get_mean_latent():
  z = torch.randn((int(n_samples), 512), device=device)
  batch_size = int(1e5)

  w_mean = torch.zeros((1,16,512),requires_grad=True,device=device)
  for i in range(int(n_samples/batch_size)):
    w = G.mapping(z[i*batch_size:(i+1)*batch_size,:], None)
    w = torch.sum(w, dim = 0).unsqueeze(0)
    w_mean = w_mean + w

  w_mean = w_mean / n_samples

  return w_mean.clone().detach().requires_grad_(True)

# Useful utility functions...

# Generates an image from a style vector.
def generate_image_from_style(dlatent, noise_mode='none'):
    if len(dlatent.shape) == 1: 
        dlatent = dlatent.unsqueeze(0)

    row_images = G.synthesis(dlatent, noise_mode=noise_mode)
    row_images = (row_images.permute(0, 2, 3, 1) * 127.5 + 128).clamp(0, 255).to(torch.uint8)
    return row_images[0].cpu().numpy()    

# Converts a noise vector z to a style vector w.
def convert_z_to_w(latent, truncation_psi=0.7, truncation_cutoff=9, class_idx=None):
    label = torch.zeros([1, G.c_dim], device=device)   
    if G.c_dim != 0:
        if class_idx is None:
            RuntimeError('Must specify class label with class_idx when using a conditional network')
        label[:, class_idx] = 1
    else:
        if class_idx is not None:
            print(f'warning: class_idx={class_idx} ignored when running on an unconditional network')
    return G.mapping(latent, label, truncation_psi=truncation_psi, truncation_cutoff=truncation_cutoff)

def load_data(df):
    y_column = 'Patient Age'
    tmp = df.copy()
    for idx in range(len(tmp)):
        if tmp.iloc[idx][y_column] > 0 and tmp.iloc[idx][y_column] <= 20:
            tmp.at[idx, y_column] = 0
        elif tmp.iloc[idx][y_column] > 20 and tmp.iloc[idx][y_column] <= 40:
            tmp.at[idx, y_column] = 1
        elif tmp.iloc[idx][y_column] >= 40 and tmp.iloc[idx][y_column] <= 60:
            tmp.at[idx, y_column] = 2
        elif tmp.iloc[idx][y_column] >= 60 and tmp.iloc[idx][y_column] <= 80:
            tmp.at[idx, y_column] = 3
        elif tmp.iloc[idx][y_column] > 80:
            tmp.at[idx, y_column] = 4
    classes =  tmp[y_column].unique()
    print(f'classes: {classes}, n_classes: {len(classes)}')
    return tmp

def load_csv():
    # load RSNA csv
    rsna_df = pd.read_csv('../datasets/rsna_patients.csv')
    rsna_df = rsna_df.drop_duplicates(subset='Image Index', keep="last") # Clean the data
    len(rsna_df)
    classes = [2149, 8008, 13118, 6454, 271]
    percentages = [round(classes[0]/sum(classes), 2), round(classes[1]/sum(classes), 2),
                   round(classes[2]/sum(classes), 2), round(classes[3]/sum(classes), 2),
                   round(classes[4]/sum(classes), 2)]
    labels = ['0-20', '20-40', '40-60', '60-80', '80+']
#     # Create pie chart
#     plt.pie(percentages, labels=labels)
#     plt.title('RSNA Data Distribution') # Add title
#     plt.savefig('Figures/RSNA Distribution.png') # Save the figure

    df = load_data(rsna_df)
    class0 = df[df["Patient Age"] == 0]
    class1 = df[df["Patient Age"] == 1]
    class2 = df[df["Patient Age"] == 2]
    class3 = df[df["Patient Age"] == 3]
    class4 = df[df["Patient Age"] == 4]
    return class0, class1, class2, class3, class4

# load extreme latent vectors '0-20' & '80+' subgroups
def load_age_dataset(class0, class1):
    X1, y1, X2, y2 = [], [], [], []
    for i in tqdm(range(len(class0))):
        x1_path = os.path.join(path2latents, class0.iloc[i]['Image Index'].split('.')[0] + '.npz')
        X1.append(np.load(x1_path)['100'])
        y1.append(class0.iloc[i]['Patient Age'])
    for i in tqdm(range(len(class1))):
        x2_path = os.path.join(path2latents, class1.iloc[i]['Image Index'].split('.')[0] + '.npz')
        X2.append(np.load(x1_path)['100'])
        y2.append(class1.iloc[i]['Patient Age'])
    return X1,X2,y1,y2

def sample_subgroups(class0, class1, class2, class3, class4):
    idx = random.randint(0, len(class4))
    img_cls0 = cv2.imread("../datasets/rsna/" + class0.iloc[idx]["Image Index"])
    img_cls1 = cv2.imread("../datasets/rsna/" + class1.iloc[idx]["Image Index"])
    img_cls2 = cv2.imread("../datasets/rsna/" + class2.iloc[idx]["Image Index"])
    img_cls3 = cv2.imread("../datasets/rsna/" + class3.iloc[idx]["Image Index"])
    img_cls4 = cv2.imread("../datasets/rsna/" + class4.iloc[idx]["Image Index"])

    plt.figure(figsize=(15,15))
    plt.subplot(551);plt.imshow(img_cls0);plt.axis(False);plt.title("0-20 Years")
    plt.subplot(552);plt.imshow(img_cls1);plt.axis(False);plt.title("20-40 Years")
    plt.subplot(553);plt.imshow(img_cls2);plt.axis(False);plt.title("40-60 Years")
    plt.subplot(554);plt.imshow(img_cls3);plt.axis(False);plt.title("60-80 Years")
    plt.subplot(555);plt.imshow(img_cls4);plt.axis(False);plt.title("80+ Years")
    plt.savefig("Figures/age_groups.png")

def add_original_dataset():
    src_dir = '../datasets/rsna/'
    rsna_df = pd.read_csv('../datasets/rsna_patients.csv') # load RSNA csv
    rsna_df = rsna_df.drop_duplicates(subset='Image Index', keep="last") # Clean the data
    print("\nAdding original images...")
    for i in tqdm(range(len(rsna_df))):
        shutil.copy("../datasets/rsna/" + rsna_df.iloc[i]["Image Index"], save_dir + rsna_df.iloc[i]["Image Index"])
        
def train_svm(styles):
    print("\nNow training linear SVM...")
    wX = []
    # styles, genders = list(df['style']), list(df['gender'])
    for idx in tqdm(range(len(styles))):
        wX.append(styles[idx].reshape((styles[0].shape[0]*styles[0].shape[1]*styles[0].shape[2])))
    clf = make_pipeline(LinearSVC(random_state=0, tol=1e-5))
    clf.fit(wX, ages)
    return clf, wX

def forward_interpolation(X):
    alpha, images = 0, []
    old_w = X; v = clf.named_steps['linearsvc'].coef_[0].reshape((X[0].shape))
    for idx in range(5):
        new_w = old_w + alpha * v
        images.append(generate_image_from_style(torch.from_numpy(new_w).to('cuda')))
        alpha += 40
    return images

def backward_interpolation(X):
    alpha, images = 0, []
    old_w = X; v = clf.named_steps['linearsvc'].coef_[0].reshape((X[0].shape))
    for idx in range(5):
        new_w = old_w + alpha * v
        images.append(generate_image_from_style(torch.from_numpy(new_w).to('cuda')))
        alpha -= 40
    return images

def save_images(images, filename):
    for idx in range(len(images)):
        cv2.imwrite(save_dir+f'{idx}_{filename}', images[idx])

def create_augmented_dataset(groups):
    for i in range(len(groups)):
        for j in tqdm(range(len(groups[i]))):
            x_path = os.path.join(path2latents, groups[i].iloc[j]['Image Index'].split('.')[0] + '.npz')
            X, y = np.load(x_path)['100'], groups[i].iloc[j]['Patient Age']
            if i == 0:
                images = forward_interpolation(X)
                save_images(images, groups[i].iloc[j]['Image Index'])
            elif i == 1:
                images = backward_interpolation(X)[1] + forward_interpolation(X)[:4]
                save_images(images, groups[i].iloc[j]['Image Index'])
            elif i == 2:
                images = backward_interpolation(X)[1:3] + forward_interpolation(X)[:3]
                save_images(images, groups[i].iloc[j]['Image Index'])
            elif i == 3:
                images = backward_interpolation(X)[1:4] + forward_interpolation(X)[:2]
                save_images(images, groups[i].iloc[j]['Image Index'])
            else:
                images = backward_interpolation(X)
                save_images(images, groups[i].iloc[j]['Image Index'])

if __name__ == "__main__":
    class0, class1, class2, class3, class4 = load_csv()
    # sample_subgroups(class0, class1, class2, class3, class4) # save figure of subgroups
    X1,X2,y1,y2 = load_age_dataset(class0, class4) # load samples from '0-20' & '80+' subgroups
    styles, ages = X1+X2, y1+y2
     # clear from memory
    clf, wX = train_svm(styles) # train linear SVM
    del styles  # clear from memory
    del ages
    del X1
    del X2
    del y1
    del y2 
    create_augmented_dataset([class0, class1, class2, class3, class4]) # save augmented dataset
    add_original_dataset() # add original RSNA data
    

         








    
    
