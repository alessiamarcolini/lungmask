import logging
import sys
import warnings

import numpy as np
import SimpleITK as sitk
import torch
from tqdm import tqdm

from . import utils
from .resunet import UNet

logging.basicConfig(stream=sys.stdout, level=logging.INFO)
warnings.filterwarnings("ignore", category=UserWarning)

# stores urls and number of classes of the models
model_urls = {
    ('unet', 'R231'): (
        'https://github.com/JoHof/lungmask/releases/download/v0.0/unet_r231-d5d2fc3d.pth',
        3,
    ),
    ('unet', 'LTRCLobes'): (
        'https://github.com/JoHof/lungmask/releases/download/v0.0/unet_ltrclobes-3a07043d.pth',
        6,
    ),
}


def apply(
    image,
    model=None,
    force_cpu=False,
    batch_size=20,
    volume_postprocessing=True,
    show_process=True,
):

    if model is None:
        model = get_model('unet', 'R231')

    voxvol = np.prod(image.GetSpacing())
    inimg_raw = sitk.GetArrayFromImage(image)
    del image

    if force_cpu:
        device = torch.device('cpu')
    else:
        if torch.cuda.is_available():
            device = torch.device('cuda')
        else:
            logging.info(
                "No GPU support available, will use CPU. Note, that this is significantly slower!"
            )
            batch_size = 1
            device = torch.device('cpu')
    model.to(device)

    tvolslices, xnew_box = utils.preprocess(inimg_raw, resolution=[256, 256])
    tvolslices[tvolslices > 600] = 600
    tvolslices = np.divide((tvolslices + 1024), 1624)
    torch_ds_val = utils.LungLabelsDS_inf(tvolslices)
    dataloader_val = torch.utils.data.DataLoader(
        torch_ds_val,
        batch_size=batch_size,
        shuffle=False,
        num_workers=1,
        pin_memory=False,
    )

    timage_res = np.empty((np.append(0, tvolslices[0].shape)), dtype=np.uint8)
    timage_res_pred = np.empty(
        (0, model.n_classes, tvolslices[0].shape[0], tvolslices[0].shape[1]),
        dtype=np.float64,
    )

    with torch.no_grad():
        for X in tqdm(dataloader_val):
            X = X.float().to(device)
            prediction = model(X)
            pls = torch.max(prediction, 1)[1].detach().cpu().numpy().astype(np.uint8)
            timage_res = np.vstack((timage_res, pls))
            timage_res_pred = np.vstack(
                (timage_res_pred, prediction.detach().cpu().numpy())
            )

    # postprocessing includes removal of small connected components, hole filling and mapping of small components to
    # neighbors
    if volume_postprocessing:
        outmask = utils.postrocessing(timage_res, 25000 / voxvol)
    else:
        outmask = timage_res

    outmask = np.asarray(
        [
            utils.reshape_mask(outmask[i], xnew_box[i], inimg_raw.shape[1:])
            for i in range(outmask.shape[0])
        ],
        dtype=np.uint8,
    )

    return outmask, timage_res_pred


def get_model(modeltype, modelname):
    model_url, n_classes = model_urls[(modeltype, modelname)]
    state_dict = torch.hub.load_state_dict_from_url(
        model_url, progress=True, map_location=torch.device('cpu')
    )
    if modeltype == 'unet':
        model = UNet(
            n_classes=n_classes,
            padding=True,
            depth=5,
            up_mode='upsample',
            batch_norm=True,
            residual=False,
        )
    elif modeltype == 'resunet':
        model = UNet(
            n_classes=n_classes,
            padding=True,
            depth=5,
            up_mode='upsample',
            batch_norm=True,
            residual=True,
        )
    else:
        logging.exception(f"Model {modelname} not known")
    model.load_state_dict(state_dict)
    model.eval()
    return model
