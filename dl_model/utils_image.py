## PIL only support limited amount of image type and don't support mupti-channel image
## This file rewrites useful image utils with skimage instead of PIL
## function can be used to create torchvision.Compose
## Currently all functions are designed for channel_last image

import os
import re
import sys
import cv2
import math
import numbers
import numpy as np
import scipy
import skimage
import skimage.io
import skimage.transform
import skimage.morphology
import skimage.restoration
import skimage.segmentation
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Wedge, Polygon, Rectangle
from matplotlib.collections import PatchCollection
import matplotlib.colors
import seaborn as sns

from PIL import Image
from skimage.color import rgb2hsv, hsv2rgb, hed2rgb, rgb2hed, gray2rgb
from collections import defaultdict
# from pycocotools import mask as mask_utils


# IMAGE_NET_MEAN_TF = np.array([123.68, 116.779, 103.939])
# IMAGE_NET_STD_TF = 1.0
# IMAGE_NET_MEAN_TORCH = np.array([0.485, 0.456, 0.406])
# IMAGE_NET_STD_TORCH = np.array([0.225, 0.224, 0.229])
CHANNEL_AXIS = -1
SKIMAGE_VERSION = skimage.__version__


class Processor(object):
    """ An image processor class.
    Args:
        transforms (list of functions): list of functions to process the image/images.
    Example:
        >>> Processor([
        >>>     resize(10),
        >>>     transforms.ToTensor(),
        >>> ])
    """

    def __init__(self, transforms):
        self.transforms = transforms

    def __call__(self, img):
        for t in self.transforms:
            img = t(img)
        return img

    def __repr__(self):
        format_string = self.__class__.__name__ + '('
        for t in self.transforms:
            format_string += '\n'
            format_string += '    {0}'.format(t)
        format_string += '\n)'
        return format_string

    
def resize(img, size, order=1, pkg='skimage', **kwargs):
    """ Resize the input numpy array image to the given size.
    
        Notes and bugs about resize:
        PIL resize (scipy.misc) don't use affine transformation and is much faster than skimage.
        But PIL don't support 3d image, and contains lots of unexpected behaviors and bugs 
        (see resize_2d_pil for details). So it's deprected though it's very fast.
            Current benchmark on resize a 500*500 rgb into 600*800, (100 runs): 
                resize_2d_pil: ~3s, resize_nd_skimage: ~6s, resize_2d_ndimage: ~6s, resize_nd_ndimage: ~10s
        Default use pkg='skimage', scipy.ndimage.zoom don't perserve range, and don't have anti-aliasing.
    Args:
        img (numpy array): Image to be resized.
        size (tuple): Desired output size. 
        order (int, optional): Desired interpolation. Default is 1
        pkg (str, optional): which inner function to call, default is skimage
        kwargs: other parameters for skimage.transform.resize (and PIL.resize).
    Returns:
        numpy array: Resized image.
    """
    if pkg == 'skimage':
        ## resize_nd_skimage will meet problem for some binary masks.
        return resize_nd_skimage(img, size, order, **kwargs)
    elif pkg == 'scipy':
        if len(size) == 2:
            ## resize_2d_ndimage don't perserve range, and don't have anti-aliasing
            return resize_2d_ndimage(img, size, order, **kwargs)
        else:
            return resize_nd_ndimage(img, size, order, **kwargs)
    else:
        raise ValueError("resize with package {} is not supported yet! ".format(pkg))
        

## This function is fast but have lots of unexpected behaviros and bugs.
## 1). Wrong transformation for binary image.
## 2). NLSI0000294_2_6.png, transfer color [  0, 148, 225] to [  0, 255, 225]. While all other images are normal.
## 3). resize_2d_pil will rescale normalized image back to 0~1
def resize_2d_pil(img, size, order=1, **kwargs):
    """ Resize the input numpy array image to the given size.
        scipy.misc.toimage will be deprected in future. PIL Image.fromarray has problem
        with binary inputs, see: 
        https://stackoverflow.com/questions/50134468/convert-boolean-numpy-array-to-pillow-image
        scipy.misc.toimage source code:
        https://github.com/scipy/scipy/blob/v0.18.1/scipy/misc/pilutil.py#L258-L369
    Args:
        img (numpy array): Image to be resized.
        size (tuple): Desired output size. 
        order (int, optional): Desired interpolation. Default is 1
        kwargs: other parameters for skimage.transform.resize
    Returns:
        numpy array: Resized image.
    """
    import scipy.misc
    channel_axis = CHANNEL_AXIS if img.ndim > 2 else None
    dtype = img.dtype
    
    ## parameters for Image.resize
    mode = kwargs.setdefault('pil_mode', None)
    ## pil takes size = (ncol, nrow) instead of (nrow, ncol)
    size = (size[1], size[0])
    
    def f(x):
        # x1 = Image.fromarray(x, mode=mode)
        # x1 = np.array(x1.resize(size, resample=order))
        x = scipy.misc.toimage(x, mode=mode)
        x = np.array(x.resize(size, resample=order))
        return x
    
    return apply_to_channel(img, f, channel_axis, in_dtype='float', out_dtype='image')


def resize_2d_ndimage(img, size, order=1, **kwargs):
    channel_axis = CHANNEL_AXIS if img.ndim > 2 else None
    dtype = img.dtype
    
    def f(x):
        zoom = [1.0 * o/i for o, i in zip(size, x.shape)]
        return scipy.ndimage.zoom(x, zoom, order=order, **kwargs)
    return apply_to_channel(img, f, channel_axis, in_dtype='float', out_dtype='image')


def resize_nd_ndimage(img, size, order=1, **kwargs):
    size = size + img.shape[len(size):]
    zoom = [1.0 * o/i for i, o in zip(img.shape, size)]
    return scipy.ndimage.zoom(img, zoom, order=order, **kwargs)


def resize_nd_skimage(img, size, order=1, **kwargs):
    """ Resize the input numpy array image to the given size.
    Args:
        img (numpy array): Image to be resized.
        size (tuple): Desired output size. 
        order (int, optional): Desired interpolation. Default is 1
        kwargs: other parameters for skimage.transform.resize
    Returns:
        numpy array: Resized image.
    """
    if not isinstance(img, np.ndarray):
        raise TypeError('img should be numpy array. Got {}'.format(type(img)))
    
    mode = kwargs.setdefault('mode', 'reflect')
    cval = kwargs.setdefault('cval', 0.)
    clip = kwargs.setdefault('clip', True)
    preserve_range = kwargs.setdefault('preserve_range', False)
    args = ({'anti_aliasing': kwargs.setdefault('anti_aliasing', True),
             'anti_aliasing_sigma': kwargs.setdefault('anti_aliasing_sigma', None)} 
            if SKIMAGE_VERSION > '0.14' else {})
    return skimage.transform.resize(img, output_shape=size, order=order, mode=mode, cval=cval, 
                                    clip=clip, preserve_range=preserve_range, **args)


class Resize(object):
    def __init__(self, size, order=1, **kwargs):
        self.size = size
        self.order = order
        self.kwargs = kwargs
    
    def __call__(self, images, kwargs=None):
        return [resize(_, self.size, self.order, pkg='skimage', **self.kwargs)
                if _ is not None else None
                for _ in images]
        ## split images into list of channels
        ## The following if else is duplicated with function resize_pil_2d, 
        ## but this can run around 50% faster, no idea why. 
#         def f(img):
#             channel_axis = CHANNEL_AXIS if img.ndim > 2 else None
#             return apply_to_channel(img, resize, channel_axis, in_dtype='image', out_dtype='image',
#                                     args=[self.size, self.order], kwargs=self.kwargs)
        
#         return [f(img) if img is not None else None for img in images]
    
    def __repr__(self):
        return self.__class__.__name__ + '(size={0}, order={1})'.format(self.size, self.order)


def get_pad_width(input_size, output_size, pos='center', stride=1):
    output_size = output_size + input_size[len(output_size):]
    output_size = np.maximum(input_size, output_size)
    if pos == 'center':
        l = np.floor_divide(output_size - input_size, 2)
    elif pos == 'random':
        # l = [np.random.randint(0, _ + 1) for _ in output_size - input_size]
        l = [np.random.randint(0, _ + 1) * stride for _ in (output_size - input_size)//stride]
    return list(zip(l, output_size - input_size - l))


def pad(img, size=None, pad_width=None, pos='center', mode='constant', **kwargs):
    """ Pad the input numpy array image with pad_width and to given size.
    Args:
        img (numpy array): Image to be resized.
        size (tuple): Desired output size. 
        pad_width (list of tuples): Desired pad_width. 
        pos: one of {'center, 'random'}, default is 'center'. if given
             size, the parameter will decide whether to put original 
             image in the center or a random location.
        mode: supported mode in skimage.util.pad
        kwargs: other parameters in skimage.util.pad
    
    pad_width and size can have same length as img, or 1d less than img.
    pad_width and size cannot be both None. If size = None, function will
    image with return img_size + pad_width. If pad_width = None, function 
    will return image with size. If both size and pad_width is not None,
    function will pad with pad_width first, then will try to meet size. 
    Function don't do any resize, rescale, crop process. Return img size 
    will be max(img.size+pad_width, size). 
    Returns:
        numpy array: Resized image.
    """
    if mode == 'constant':
        pars = {'constant_values': kwargs.setdefault('cval', 0.0)}
    elif mode == 'linear_ramp':
        pars = {'end_values': kwargs.setdefault('end_values', 0.0)}
    elif mode == 'reflect' or mode == 'symmetric':
        pars = {'reflect_type': kwargs.setdefault('reflect_type', 'even')}
    else:
        pars = {'stat_length': kwargs.setdefault('stat_length', None)}
    
    if pad_width is not None:
        pad_width = pad_width + [(0, 0)] * (img.ndim - len(pad_width))
        # img = skimage.util.pad(img, pad_width[:img.ndim], mode=mode, **pars)
        img = np.pad(img, pad_width[:img.ndim], mode=mode, **pars)
    
    if size is not None:
        pad_var = get_pad_width(img.shape, output_size=size, pos=pos)
        # img = skimage.util.pad(img, pad_var, mode=mode, **pars)
        img = np.pad(img, pad_var, mode=mode, **pars)
    
    return img


class Pad(object):
    def __init__(self, size=None, pad_width=None, pos='center', mode='constant', **kwargs):
        if isinstance(size, numbers.Number):
            size = (int(size), int(size))
        else:
            if size is not None:
                assert len(size) == 2, "Please provide only two dimensions (h, w) for size."
        
        self.size = size
        self.pad_width = pad_width
        self.pos = pos
        self.mode = mode
        self.kwargs = kwargs
    
    def __call__(self, images, kwargs=None):
        ## Add support to kwargs, let kwargs over-write self.kwargs for different inputs.
        ## like different cvals for different type of images.
        if kwargs is None:
            kwargs = [{}] * len(images)
        if self.pad_width is not None:
            images = [pad(img, size=None, pad_width=self.pad_width, 
                          mode=self.mode, **{**self.kwargs, **args}) 
                      if img is not None else None
                      for img, args in zip(images, kwargs)]
        if self.size is not None:
            pad_width = get_pad_width(images[0].shape, output_size=self.size, pos=self.pos)
            images = [pad(img, size=None, pad_width=pad_width, 
                          mode=self.mode, **{**self.kwargs, **args}) 
                      if img is not None else None
                      for img, args in zip(images, kwargs)]
        return images
    
    def __repr__(self):
        return self.__class__.__name__ + '(size={0}, pad_width={1}, pos={2}, mode={3})'.\
            format(self.size, self.pad_width, self.pos, self.mode)


def get_crop_width(input_size, output_size, pos='center'):
    output_size = output_size + input_size[len(output_size):]
    output_size = np.minimum(input_size, output_size)
    if pos == 'center':
        l = np.floor_divide(input_size - output_size, 2)
    elif pos == 'random':
        l = [np.random.randint(0, _ + 1) for _ in input_size - output_size]        
    return list(zip(l, input_size - output_size - l))


def crop(img, size=None, crop_width=None, pos='center', **kwargs):
    """ Crop the input numpy array image with crop_width and to given size.
    Args:
        img (numpy array): Image to be resized.
        size (tuple): Desired output size. 
        crop_width (list of tuples): Desired crop_width. 
        pos: one of {'center, 'random'}, default is 'center'. if given
             size, the parameter will decide whether to put original 
             image in the center or a random location.
        kwargs: other parameters in skimage.util.crop, use default just fine.
    
    crop_width and size can have same length as img, or 1d less than img.
    crop_width and size cannot be both None. If size = None, function will
    return image with img_size - crop_width. If crop_width = None, function 
    will return image with size. If both size and crop_width is not None,
    function will crop with crop_width first, then will try to meet size. 
    Function don't do any resize, rescale and pad process. Return img size 
    will be min(img.size-pad_width, size). 
    Returns:
        numpy array: Resized image.
    """
    copy = kwargs.setdefault('copy', False)
    order = kwargs.setdefault('order', 'K')
    
    if crop_width is not None:
        crop_width = crop_width + [(0, 0)] * (img.ndim - len(crop_width))
        img = skimage.util.crop(img, crop_width[:img.ndim], copy=copy, order=order)
    
    if size is not None:
        crop_var = get_crop_width(img.shape, output_size=size, pos=pos)
        img = skimage.util.crop(img, crop_var, copy=copy, order=order)
    return img


class Crop(object):
    def __init__(self, size=None, crop_width=None, pos='center', **kwargs):
        if size is not None:
            if isinstance(size, numbers.Number):
                size = (int(size), int(size))
            else:
                assert len(size) == 2, "Please provide only two dimensions (h, w) for size."
        
        self.size = size
        self.crop_width = crop_width
        self.pos = pos
        self.kwargs = kwargs
    
    def __call__(self, images, kwargs=None):
        if kwargs is None:
            kwargs = [{}] * len(images)
        if self.crop_width is not None:
            images = [crop(img, size=None, crop_width=self.crop_width, **{**self.kwargs, **args}) 
                      if img is not None else None
                      for img, args in zip(images, kwargs)]
        if self.size is not None:
            crop_width = get_crop_width(images[0].shape, output_size=self.size, pos=self.pos)
            images = [crop(img, size=None, crop_width=crop_width, **{**self.kwargs, **args}) 
                      if img is not None else None
                      for img, args in zip(images, kwargs)]
        return images
    
    def __repr__(self):
        return self.__class__.__name__ + '(size={0}, crop_width={1}, pos={2})'.\
            format(self.size, self.crop_width, self.pos)


def center_crop(img, size):
    return crop(img, size=size, crop_width=None, pos='center')


class CenterCrop(object):
    def __init__(self, size):
        self.size = size
    
    def __call__(self, images):
        return [center_crop(img, size=self.size) for img in images]
    
    def __repr__(self):
        return self.__class__.__name__ + '(size={0})'.format(self.size)


def five_crop(img, size):
    """Crop the given PIL Image into four corners and the central crop.
    .. Note::
        This transform returns a tuple of images and there may be a
        mismatch in the number of inputs and targets your ``Dataset`` returns.
    Args:
       size (sequence or int): Desired output size of the crop. If size is an
           int instead of sequence like (h, w), a square crop (size, size) is
           made.
    Returns:
       tuple: tuple (tl, tr, bl, br, center)
                Corresponding top left, top right, bottom left, bottom right and center crop.
    """
    if isinstance(size, numbers.Number):
        size = (int(size), int(size))
    else:
        assert len(size) == 2, "Please provide only two dimensions (h, w) for size."

    w, h = img.size
    crop_h, crop_w = size
    if crop_w > w or crop_h > h:
        raise ValueError("Requested crop size {} is bigger than input size {}".format(size,
                                                                                      (h, w)))
    tl = crop(img, crop_width=[(0, h - crop_h), (0, w - crop_w)])
    tr = crop(img, crop_width=[(0, h - crop_h), (w - crop_w, 0)])
    bl = crop(img, crop_width=[(h - crop_h, 0), (0, w - crop_w)])
    br = crop(img, crop_width=[(h - crop_h, 0), (w - crop_w, 0)])
    center = center_crop(img, size)
    return (tl, tr, bl, br, center)
    

def hflip(img):
    return img[:, ::-1, ...]


class RandomHorizontalFlip(object):
    def __init__(self, p=0.5):
        self.p = p

    def __call__(self, images):
        if np.random.random() < self.p:
            return [hflip(img) if img is not None else None for img in images]
        return images

    def __repr__(self):
        return self.__class__.__name__ + '(p={})'.format(self.p)


def vflip(img):
    return img[::-1, ...]


class RandomVerticalFlip(object):
    def __init__(self, p=0.5):
        self.p = p

    def __call__(self, images):
        if np.random.random() < self.p:
            return [vflip(img) if img is not None else None for img in images]
        return images

    def __repr__(self):
        return self.__class__.__name__ + '(p={})'.format(self.p)


def random_transform_pars(N, rotation=0., translate_x=0., translate_y=0., 
                          scale_x=0., scale_r=0., shear=0., 
                          projection_g=0., projection_h=0., p=0.5, seed=None):
    """ Randomly generate parameters for image transformation.
        If a scalar value is provided, the function will
        randomly generate N parameters inside the range
        If a list/array is provided, the function will use
        all combination of these values.
        
        # Returns
        A dictionary contains args for random transformation.
        Use get_transform_matrix to generate a transform matrix.
        And use transform to do affine/projective transformation.
    """
    if seed is not None:
        np.random.seed(seed)
    
    pars = dict()
    
    # rotation
    rotation = (-rotation, rotation) if np.isscalar(rotation) else rotation
    r = np.random.uniform(rotation[0], rotation[1], N) * (np.random.random(N) < p)
    pars['rotation'] = r.tolist()
    
    # translation
    translate_x = (-translate_x, translate_x) if np.isscalar(translate_x) else translate_x
    tx = np.random.uniform(translate_x[0], translate_x[1], N) * (np.random.random(N) < p)
    translate_y = (-translate_y, translate_y) if np.isscalar(translate_y) else translate_y
    ty = np.random.uniform(translate_y[0], translate_y[1], N) * (np.random.random(N) < p)
    pars['translate'] = np.stack([tx, ty], axis=-1).tolist()
    
    # shear
    shear = (-shear, shear) if np.isscalar(shear) else shear
    s = np.random.uniform(shear[0], shear[1], N) * (np.random.random(N) < p)
    pars['shear'] = s.tolist()
    
    # scale
    scale_x = (1.*(1-scale_x), 1./(1-scale_x)) if np.isscalar(scale_x) else scale_x
    zx = np.random.uniform(np.log(scale_x[0]), np.log(scale_x[1]), N) * (np.random.random(N) < p)
    scale_r = (1.*(1-scale_r), 1./(1-scale_r)) if np.isscalar(scale_r) else scale_r
    zy = zx + (np.random.uniform(np.log(scale_r[0]), np.log(scale_r[1]), N) * (np.random.random(N) < p))
    pars['scale'] = np.stack([np.exp(zx), np.exp(zy)], axis=-1).tolist()
    
    # projection
    projection_g = (- projection_g, projection_g) if np.isscalar(projection_g) else projection_g
    pg = np.random.uniform(projection_g[0], projection_g[1], N) * (np.random.random(N) < p)
    projection_h = (- projection_h, projection_h) if np.isscalar(projection_h) else projection_h
    ph = np.random.uniform(projection_h[0], projection_h[1], N) * (np.random.random(N) < p)
    pars['projection'] = np.stack([pg, ph], axis=-1).tolist()
    
    return unpack_dict(pars, N)


def get_transform_matrix(rotation, translate, scale, shear, projection=(0, 0), center=(0, 0), inverse=True):
    """ Compute (inverse) matrix for affine/projective transformation
    .. Note::
        Affine transformation matrix is calculated as: M = T * C * RSS * C^-1
        T is translation matrix after rotation: [[1, 0, tx], [0, 1, ty], [0, 0, 1]]
        C is translation matrix to keep center: [[1, 0, cx], [0, 1, cy], [0, 0, 1]]
        RSS is rotation with scale and shear matrix
        RSS(a, scale, shear) = [[cos(a)*scale_height, -sin(a + shear)*scale_height, 0],
                                [sin(a)*scale_width, cos(a + shear)*scale_width, 0],
                                [0, 0, 1]]
        The inverse matrix is M^-1 = C * RSS^-1 * C^-1 * T^-1
        Projective transformation: [[a, b, c], [d, e, f], [g, h, 1]], where g, h != 0
    Args:
        rotation (float or int): rotation angle in degrees between -180 and 180.
        translate (list or tuple of integers): horizontal and vertical translations (post-rotation translation)
        scale (list or tuple of floats): height_scale and width_scale
        shear (float): shear angle value in degrees between -180 to 180.
        center (tuple, optional): center offset in translation matrix
        projection (list or tuple of floats, optional): the projective transformation
        inverse (bool): apply inverse matrix (clockwise) or original matrix (anti-cloakwise)
    Returns:
        a 3*3 (inverse) matrix for affine/projective transformation
    """
    assert isinstance(translate, (tuple, list)) and len(translate) == 2, \
        "Argument translate should be a list or tuple of length 2"
    assert isinstance(scale, (tuple, list)) and len(scale) == 2, \
        "Argument scale should be a list or tuple of length 2"
    assert isinstance(projection, (tuple, list)) and len(projection) == 2, \
        "Argument projection should be a list or tuple of length 2"
    assert isinstance(center, (tuple, list)) and len(center) == 2, \
        "Argument center should be a list or tuple of length 2"    
    
    rotation = math.radians(rotation)
    shear = math.radians(shear)
    
    if inverse:
        # Inverted rotation matrix with scale and shear
        d = math.cos(rotation + shear) * math.cos(rotation) + math.sin(rotation + shear) * math.sin(rotation)
        matrix = np.array([[math.cos(rotation + shear) / scale[1] / d, math.sin(rotation + shear) / scale[1] / d, 0],
                           [-math.sin(rotation) / scale[0] / d, math.cos(rotation) / scale[0] / d, 0], [0, 0, 1]])
    else:
        matrix = np.array([[math.cos(rotation + shear) * scale[0], -math.sin(rotation + shear) * scale[0], 0],
                           [math.sin(rotation) * scale[1],  math.cos(rotation) * scale[1], 0],
                           [0, 0, 1]])

    ## Offset center and apply translation: C * RSS^-1 * C^-1 * T^-1
    matrix[0, 2] = center[1] + matrix[0, 0] * (-center[1] - translate[1]) + matrix[0, 1] * (-center[0] - translate[0])
    matrix[1, 2] = center[0] + matrix[1, 0] * (-center[1] - translate[1]) + matrix[1, 1] * (-center[0] - translate[0])
    
    ## Add projection
    matrix[2, 0] = projection[0]
    matrix[2, 1] = projection[1]
        
    return matrix


def translate_offset_center(translate, input_size, output_size):  
    # offset matrix to the center of image
    center = (input_size[0] * 0.5 + 0.5, input_size[1] * 0.5 + 0.5)
    offset = (output_size[0] * 0.5 + 0.5, output_size[1] * 0.5 + 0.5)
    translate = (translate[0] + offset[0] - center[0], 
                 translate[1] + offset[1] - center[1])
    return center, offset, translate
    

def transform(img, matrix, size=None, out_dtype='image', **kwargs):
    """Apply affine/projective transformation on the image. 
    .. Note::
        image is centered under new size after affine transformation.
    Args:
        img (numpy array): input image.
        matrix (3*3 numpy array or a dictionary): provide either a transform matrix or pars to generate matrix.
        size (tuple, optional): the output image size.
        kwargs: parameters for get_transform_matrix and skimage.transform.warp. 
            get_transform_matrix args: [rotation, translate, scale, shear, projection, inverse]
            skimage.transform.warp functions:
            order: use order = 0 for mask to keep labels. This will avoid unnecessary post-treatment.
            mode and cval: fill area outside the transform with specific padding method/color.
            preserve_range: use preserve_range=True for higher order
    Return:
        images after transformation
    """
    out_dtype = img.dtype if out_dtype == 'image' else out_dtype
    if size is None:
        size = img.shape[:2]
    
    ## if no transform matrix is given, use default setting
    if matrix is None:
        matrix = {}
    if isinstance(matrix, dict):
        rotation = matrix.setdefault('rotation', 0.)
        translate = matrix.setdefault('translate', (0., 0.))
        scale = matrix.setdefault('scale', (0., 0.))
        shear = matrix.setdefault('shear', 0.)
        projection = matrix.setdefault('projection', (0., 0.))
        inverse = matrix.setdefault('inverse', True)
        
        # offset matrix to center
        center, _, translate = translate_offset_center(translate, input_size=img.shape[:2], output_size=size)
        # center = (img.shape[0] * 0.5 + 0.5, img.shape[1] * 0.5 + 0.5)
        # offset = (size[0] * 0.5 + 0.5, size[1] * 0.5 + 0.5)
        # translate = (translate[0] + offset[0] - center[0], translate[1] + offset[1] - center[1])
        matrix = get_transform_matrix(rotation, translate, scale, shear, 
                                      projection=projection, center=center, inverse=inverse)
    
    assert isinstance(matrix, np.ndarray) and matrix.shape == (3, 3), \
        "Invalid transform matrix"
    
    if not np.allclose(matrix, np.eye(3)):
        order = kwargs.setdefault('order', 1)
        mode = kwargs.setdefault('mode', 'constant')
        cval = kwargs.setdefault('cval', 0.0)
        clip = kwargs.setdefault('clip', True)
        preserve_range = kwargs.setdefault('preserve_range', False)
        
        if np.any(matrix[-1, :-1]):
            tform = skimage.transform.ProjectiveTransform(matrix=matrix)
        else:
            tform = skimage.transform.AffineTransform(matrix=matrix)
        img = skimage.transform.warp(img, tform, output_shape=size, order=order, 
                                     mode=mode, cval=cval, clip=clip, 
                                     preserve_range=preserve_range)
    return img_as(out_dtype)(img)


class RandomTransform(object):
    """ Random Transformation. 
    Argument:
        size: output image size
        rotation: float (degree)
        shear: float(degree)
        translate: tuple(x, y) 
        scale: tuple(zoom, h/w ratio)
        projection: tuple(x, y)
        inverse: use inverse transform or not
        p: probability for each transform
    """
    def __init__(self, size=None, rotation=0., translate=(0., 0.), 
                 scale=(0., 0.), shear=0., projection=(0., 0.), 
                 inverse=True, p=0.5, **kwargs):
        self.size = size
        self.rotation = rotation if rotation is not None else 0.
        self.shear = shear if shear is not None else 0.
        
        if translate is not None:
            assert isinstance(translate, (tuple, list)) and len(translate) == 2, \
                "translate should be a list or tuple and it must be of length 2."
        else:
            translate = (0., 0.)
        self.translate = translate

        if scale is not None:
            assert isinstance(scale, (tuple, list)) and len(scale) == 2, \
                "scale should be a list or tuple and it must be of length 2."
        else:
            scale = (0., 0.)
        self.scale = scale
        
        if projection is not None:
            assert isinstance(projection, (tuple, list)) and len(projection) == 2, \
                "scale should be a list or tuple and it must be of length 2."
        else:
            projection = (0., 0.)
        self.projection = projection        

        self.inverse = inverse
        self.p = p
        self.kwargs = kwargs
    
    def get_params(self, input_size, output_size):
        pars = random_transform_pars(N=1, rotation=self.rotation, 
                                     translate_x=self.translate[0], translate_y=self.translate[1], 
                                     scale_x=self.scale[0], scale_r=self.scale[1], shear=self.shear, 
                                     projection_g=self.projection[0], projection_h=self.projection[1], 
                                     p=self.p, seed=None)[0]
        center, _, translate = translate_offset_center(pars['translate'], input_size, output_size)
        pars.update({'center': center, 'translate': translate, 'inverse': self.inverse})
        matrix = get_transform_matrix(**pars)
        return matrix, pars
    
    def __call__(self, images, kwargs=None):
        input_size = images[0].shape[:2]
        output_size = input_size if self.size is None else self.size
        if isinstance(output_size, numbers.Number):
            output_size = (int(output_size), int(output_size))
        matrix, pars = self.get_params(input_size, output_size)
        
        if kwargs is None:
            kwargs = [{}] * len(images)
        
        def f(img, args={}):
            if img.ndim > 2:
                res = np.rollaxis(img, CHANNEL_AXIS)
                res = np.stack([transform(res[i], matrix, size=output_size, **{**self.kwargs, **args})
                                for i in range(len(res))], axis=CHANNEL_AXIS)
            else:
                res = transform(img, matrix, size=output_size, **{**self.kwargs, **args})
            return res
        return [f(img, args) if img is not None else None for img, args in zip(images, kwargs)]
        # return [transform(x, matrix, size=output_size, **self.kwargs) for x in images]
    
    def __repr__(self):
        s = '{name}(rotation={rotation}, translate={translate}, scale={scale}, shear={shear}, projection={projection})'
        return s.format(name=self.__class__.__name__, **dict(self.__dict__))


def normalize(img, mean=0., std=1., dtype=None):
    if dtype is None:
        dtype = img.dtype
    return ((img - mean)/std).astype(dtype)


class Normalize(object):
    def __init__(self, mean=0., std=1., dtype=None):
        self.mean = mean
        self.std = std
        self.dtype = dtype

    def get_params(self, x):
        ndim = x.ndim
        if isinstance(self.mean, str) and self.mean == 'sample':
            mean = np.mean(x, axis=tuple(range(x.ndim-1)))
        else:
            mean = np.array(self.mean)
        if isinstance(self.std, str) and self.std == 'sample':
            std = np.std(x, axis=tuple(range(x.ndim-1)))
        else:
            std = np.array(self.std)
        dtype = x.dtype if self.dtype is None else self.dtype
        return {'mean': mean, 'std': std, 'dtype': dtype}

    def __call__(self, images):
        return [normalize(img, **self.get_params(img)) if img is not None else None for img in images]

    def __repr__(self):
        return self.__class__.__name__ + '(mean={0}, std={1})'.format(self.mean, self.std)


def rescale_intensity(img, rescale_method, out_range='dtype', **kwargs):
    """ Rescale image channel with stretch, hist or adaptive. 
    Args:
        img (numpy array): image (will be transfer to float64 between 0~1)
        rescale_method: one of {'stretch', 'hist', 'adaptive'}.
        out_range (optional): default will use skimage.dtype_limits(img.dtype).
    Return:
        rescaled image in out_range. 
        Function will first stretch img into [0, 1]. Than rescale back to 
        the out_range and keep the input img.dtype
    """
    if out_range == 'dtype':
        out_range = skimage.dtype_limits(img)
    
    if rescale_method == 'stretch':
        # Contrast stretching: (in_range[0], in_range[1]) -> (out_range[0], out_range[1])
        in_range = kwargs.setdefault('in_range', 'image')  # default in_range = (img.min(), img.max())
        return skimage.exposure.rescale_intensity(img, in_range=in_range, out_range=out_range)
    elif rescale_method == 'hist':
        # Equalization, equalize_hist will balance everythin fullfill "mask" into (0, 1)
        nbins = kwargs.setdefault('nbins', 256)
        mask = kwargs.setdefault('mask', None)
        x = skimage.exposure.equalize_hist(img, nbins=nbins, mask=mask)
        # result after equalize_hist is always in range (0. 1.)
        return skimage.exposure.rescale_intensity(x, in_range=(0., 1.), out_range=out_range).astype(img.dtype)
    elif rescale_method == 'adaptive':
        # Adaptive Equalization
        kernel_size = kwargs.setdefault('kernel_size', None)
        clip_limit = kwargs.setdefault('clip_limit', 0.01)
        nbins = kwargs.setdefault('nbins', 256)
        x = skimage.exposure.equalize_adapthist(img, kernel_size=kernel_size, clip_limit=clip_limit, nbins=nbins)
        # result after equalize_adapthist is always in range (0. 1.)
        return skimage.exposure.rescale_intensity(x, in_range=(0., 1.), out_range=out_range).astype(img.dtype)


class RescaleChannelIntensity(object):
    def __init__(self, rescale_method, out_range='dtype', **kwargs):
        self.rescale_method = rescale_method
        self.out_range = out_range
        self.kwargs = kwargs
    
    def __call__(self, images):
        def f(img):
            if img.ndim > 2:
                res = np.rollaxis(img, CHANNEL_AXIS)
                res = np.stack([rescale_intensity(res[i], self.rescale_method, self.out_range, **self.kwargs)
                                for i in range(len(res))], axis=CHANNEL_AXIS)
            else:
                res = rescale_intensity(img, self.rescale_method, self.out_range, **self.kwargs)
            return res
        
        return [f(img) if img is not None else None for img in images]
    
    def __repr__(self):
        return self.__class__.__name__ + '(size={0}, crop_width={1}, pos={2}, order={3})'.\
            format(self.size, self.crop_width, self.pos, self.order)


def get_gaussian_kernel(size, sigma):
    if isinstance(size, numbers.Number):
        size = (int(size), int(size))
    if isinstance(sigma, numbers.Number):
        sigma = (sigma, sigma)
    x = cv2.getGaussianKernel(size[0]*2+1, sigma[0])
    y = cv2.getGaussianKernel(size[1]*2+1, sigma[1])
    kernel = np.dot(x, y.T)
    
    return kernel/np.sum(kernel)


def blur_image(x, method='gaussian', out_dtype='image', *args, **kwargs):
    assert method in ['gaussian', 'mean', 'median'], "%s bluring is not supported" % method
    out_dtype = x.dtype if out_dtype == 'image' else out_dtype
    
    fn = {'gaussian': skimage.filters.gaussian,
          'median': skimage.filters.rank.median, 
          'mean': skimage.filters.rank.mean}[method]
    if x.ndim < 3:
        res = fn(x, *args, **kwargs)
    else:
        if method == 'gaussian':
            res = fn(x, *args, **kwargs)
        else:
            ## transfer rgb 2 hsv for median and mean filter
            x = np.moveaxis(rgb2hsv(x), -1, 0)
            x = np.stack([fn(_, *args, **kwargs) for _ in x], axis=-1)
            res = hsv2rgb(x)
    return img_as(out_dtype)(res)


def random_blur_whole_image(img, kernel=None):
    filters = [
        {'method': 'gaussian', 'sigma': np.random.uniform(low=1, high=8)}, 
        {'method': 'median', 'selem': skimage.morphology.disk(np.random.randint(4)*2+1)}, 
        {'method': 'mean', 'selem': skimage.morphology.disk(np.random.randint(4)*2+1)}
    ]
    pars = filters[np.random.randint(0, len(filters))]
    
    return blur_image(img, **pars)


def random_blur_local_region(x, kernels=None, masks=None, out_dtype='image'):
    """ This function is in beta. Current very slow and only support gaussian kernel. """
    out_dtype = x.dtype if out_dtype == 'image' else out_dtype
    ## build a gaussian kernel
    if kernels is None:
        kernels = [get_gaussian_kernel(np.random.randint(2, 6), np.random.uniform(low=8, high=16))]
    ## Calculate max pad_width for kernel
    p0, p1 = np.amax([k.shape for k in kernels], axis=0) // 2
    padded_x = pad(x, pad_width=[(p0, p0), (p1, p1)])
    h, w = x.shape[0], x.shape[1]
    
    ## generate a masks
    if masks is None:
        masks = {'max_shapes': 10, 'min_shapes': 3, 'min_size': 96, 'max_size': 256, 'random_seed': None}
    if isinstance(masks, dict):
        masks, labels = skimage.draw.random_shapes((h, w), **masks)
        masks, = RandomTransform(size=(h, w), rotation=45, translate=(int(h/10), int(w/10)), 
                                 scale=(0.2, 0.3), shear=20, projection=(0.0006, 0.0006), 
                                 order=0, p=0.7)([masks])
        masks, = RandomHorizontalFlip(0.5)([masks])
        masks, = RandomVerticalFlip(0.5)([masks])
        masks = np.mean(masks, axis=-1) < 255
    
    ## Assign blurring in mask region
    res = np.copy(x)
    for idx, prop in enumerate(skimage.measure.regionprops(skimage.measure.label(masks))):
        x0, y0 = prop.coords[:,0], prop.coords[:,1]
        kernel = kernels[idx % len(kernels)]
        kernel = np.expand_dims(kernel/np.sum(kernel), axis=-1)
        i_s, i_e = p0-kernel.shape[0]//2, p0+(kernel.shape[0]+1)//2
        j_s, j_e = p1-kernel.shape[1]//2, p1+(kernel.shape[1]+1)//2
        res[x0, y0, ...] = np.array([
            np.sum(padded_x[i+i_s:i+i_e, j+j_s:j+j_e] * kernel, axis=(0,1)) for i, j in zip(x0, y0)
        ])
    
    return img_as(out_dtype)(res)


def shift_invariant_denoising(x, max_shifts, func='denoise_wavelet', out_dtype='image', **kwargs):
    """ A wrapper of the skimage.restoration module. 
        Unfinished, only support denoise_wavelet now.
    """
    import skimage.restoration
    if not callable(func):
        if isinstance(func, str):
            func = getattr(skimage.restoration, func)
    
    if not kwargs:              
        kwargs = dict(multichannel=True, convert2ycbcr=True, wavelet='db1')
        if SKIMAGE_VERSION >= '0.16':
            kwargs['rescale_sigma'] = True
        
    
    out_dtype = x.dtype if out_dtype == 'image' else out_dtype
    x = img_as('float')(x)
    res = skimage.restoration.cycle_spin(
        x, func=func, max_shifts=max_shifts,
        func_kw=kwargs, multichannel=True)
    
    return img_as(out_dtype)(res)


def random_adjust_color(img, global_mean=0.01, channel_mean=0.01, channel_sigma=0.2):
    """ A simple and effective random color augmentation (from Shidan). 
        The function treat last dimension as channel.
        global_mean: the relative mean add to all channel.
        channel_mean: the relative mean add to each channel.
        channel_sigma: the relative variace add to each channel.
    """
    dtype = img.dtype
    img = img_as('uint8')(img) * 1.0
    n_channel = img.shape[-1]
    # 1) add global mean and channel mean
    img += np.random.randn() * global_mean
    # 2) add a shifting & variance for each channel
    channel_means = np.random.randn(n_channel) * channel_mean
    channel_vars = np.clip(np.random.randn(n_channel) * channel_sigma, -channel_sigma, channel_sigma)
    img += img * channel_vars + channel_means
    # print(image_stats(img))
    
    return img_as(dtype)(np.clip(img/255., 0., 1.))


class ColorDodge(object):
    """ Randomly color augmentation with mean and std.
    Args:
        global_mean: the relative mean add to all channel.
        channel_mean: the relative mean add to each channel.
        channel_sigma: the relative variace add to each channel.
    """
    def __init__(self, global_mean=0.01, channel_mean=0.01, channel_sigma=0.2, p=0.5):
        self.global_mean = global_mean
        self.channel_mean = channel_mean
        self.channel_sigma = channel_sigma
        self.p = p
    
    def __call__(self, images, kwargs=None):
        return [random_adjust_color(img, self.global_mean, self.channel_mean, self.channel_sigma)
                if img is not None and np.random.random() < self.p else img
                for img in images]

    def __repr__(self):
        format_string = self.__class__.__name__ + '('
        format_string += 'global_mean={0}'.format(self.global_mean)
        format_string += ', channel_mean={0}'.format(self.channel_mean)
        format_string += ', channel_sigma={0}'.format(self.channel_sigma)
        return format_string


def adjust_brightness(img, brightness_factor):
    """ Adjust brightness of an Image. """
    min_val, max_val = skimage.dtype_limits(img)
    return np.clip(img * brightness_factor, min_val, max_val).astype(img.dtype)


def adjust_contrast(img, contrast_factor):
    """ Adjust contrast of an Image. """
    min_val, max_val = skimage.dtype_limits(img)
    degenerate = np.mean(rgb2gray(img))
    res = degenerate * (1-contrast_factor) + img * contrast_factor
    return np.clip(res, min_val, max_val).astype(img.dtype)


def adjust_saturation(img, saturation_factor):
    """ Adjust color saturation of an image (PIL ImageEnhance.Color). """
    min_val, max_val = skimage.dtype_limits(img)
    degenerate = rgb2gray(img, 1)
    res = degenerate * (1-saturation_factor) + img * saturation_factor
    return np.clip(res, min_val, max_val).astype(img.dtype)


def adjust_hue(img, hue_factor):
    """ Adjust hue of an image.
        hue_factor is the amount of shift in H channel [-0.5, 0.5].
    """
    if not(-0.5 <= hue_factor <= 0.5):
        raise ValueError('hue_factor is not in [-0.5, 0.5].'.format(hue_factor))
    
    hsv = rgb2hsv(img)
    hsv[..., 0] *= (1 + hue_factor)
    res = hsv2rgb(np.clip(hsv, 0.0, 1.0)) # float image
    return img_as(img.dtype)(res)


def adjust_gamma(img, gamma=1, gain=1):
    """ Perform gamma correction (Power Law Transform) on an image. """
    return skimage.exposure.adjust_gamma(img, gamma=gamma, gain=gain)


def random_color_jitter(img, factors):
    func_list = {'brightness': adjust_brightness, 'contrast': adjust_contrast,
                 'saturation': adjust_saturation, 'hue': adjust_hue}
    for key, val in factors:
        img = func_list[key](img, val)
    return img


## Copy from torch.vision
class ColorJitter(object):
    """ Randomly change the brightness, contrast and saturation of an image.
    Args:
        brightness (float or tuple of float (min, max)): How much to jitter brightness.
            brightness_factor is chosen uniformly from [max(0, 1 - brightness), 1 + brightness]
            or the given [min, max]. Should be non negative numbers.
        contrast (float or tuple of float (min, max)): How much to jitter contrast.
            contrast_factor is chosen uniformly from [max(0, 1 - contrast), 1 + contrast]
            or the given [min, max]. Should be non negative numbers.
        saturation (float or tuple of float (min, max)): How much to jitter saturation.
            saturation_factor is chosen uniformly from [max(0, 1 - saturation), 1 + saturation]
            or the given [min, max]. Should be non negative numbers.
        hue (float or tuple of float (min, max)): How much to jitter hue.
            hue_factor is chosen uniformly from [-hue, hue] or the given [min, max].
            Should have 0 <= hue <= 0.5 or -0.5 <= min <= max <= 0.5.
    """
    def __init__(self, brightness=0, contrast=0, saturation=0, hue=0, p=0.5):
        self.brightness = self._check_input(brightness, 'brightness')
        self.contrast = self._check_input(contrast, 'contrast')
        self.saturation = self._check_input(saturation, 'saturation')
        self.hue = self._check_input(hue, 'hue', center=0, bound=(-0.5, 0.5), clip_first_on_zero=False)
        self.p = p

    def _check_input(self, value, name, center=1, bound=(0, float('inf')), clip_first_on_zero=True):
        if isinstance(value, numbers.Number):
            if value < 0:
                raise ValueError("If {} is a single number, it must be non negative.".format(name))
            value = [center - value, center + value]
            if clip_first_on_zero:
                value[0] = max(value[0], 0)
        elif isinstance(value, (tuple, list)) and len(value) == 2:
            if not bound[0] <= value[0] <= value[1] <= bound[1]:
                raise ValueError("{} values should be between {}".format(name, bound))
        else:
            raise TypeError("{} should be a single number or a list/tuple with lenght 2.".format(name))

        # if value is 0 or (1., 1.) for brightness/contrast/saturation
        # or (0., 0.) for hue, do nothing
        if value[0] == value[1] == center:
            value = None
        return value

    def get_params(self):
        """Get a randomized transform to be applied on image.
        Arguments are same as that of __init__.
        Returns:
            Transform which randomly adjusts brightness, contrast and
            saturation in a random order.
        """
        pars = []
        
        if self.brightness is not None:
            pars.append(('brightness', np.random.uniform(self.brightness[0], self.brightness[1])))
        
        if self.contrast is not None:
            pars.append(('contrast', np.random.uniform(self.contrast[0], self.contrast[1])))

        if self.saturation is not None:
            pars.append(('saturation', np.random.uniform(self.saturation[0], self.saturation[1])))

        if self.hue is not None:
            pars.append(('hue', np.random.uniform(self.hue[0], self.hue[1])))
        
        np.random.shuffle(pars)
        return pars

    def __call__(self, images, kwargs=None):
        """
        Args:
            img (PIL Image): Input image.
        Returns:
            PIL Image: Color jittered image.
        """
        pars = self.get_params()
        return [random_color_jitter(img, pars)
                if img is not None and np.random.random() < self.p else img
                for img in images]

    def __repr__(self):
        format_string = self.__class__.__name__ + '('
        format_string += 'brightness={0}'.format(self.brightness)
        format_string += ', contrast={0}'.format(self.contrast)
        format_string += ', saturation={0}'.format(self.saturation)
        format_string += ', hue={0})'.format(self.hue)
        return format_string


class ToGray(object):
    def __init__(self, p=0.5):
        self.p = p

    def __call__(self, images):
        if np.random.random() < self.p:
            return [rgb2gray(img, num_output_channels=None if len(img.shape) < 3 else img.shape[-1])
                    if img is not None else None for img in images]
        return images

    def __repr__(self):
        return self.__class__.__name__ + '(p={})'.format(self.p)


def rgb2gray_old(img, num_output_channels=1):
    """ Convert image to grayscale image.
    Args:
        img (numpy array): Image to be converted to grayscale.
        num_output_channels (int or None): 
            if num_output_channels is None: returned image has same amount of channel as image
            else use given num_output_channels
    """
    if num_output_channels is None:
        num_output_channels = img.shape[CHANNEL_AXIS]
    img = skimage.color.rgb2gray(img)
    if num_output_channels > 1:
        img = np.stack([img] * num_output_channels, axis=-1)
    return img


def rgb2gray(x, num_output_channels=None):
    """ Convert image to grayscale image.
    Args:
        x (numpy array): Image to be converted to grayscale.
        num_output_channels (int or None): 
            if num_output_channels is None: return 2d image.
            else return a image with num_output_channels.
    Notes:
        The behavior of rgb2gray will change in scikit-image 0.19. Currently, 
        rgb2gray allows 2D grayscale image to be passed as inputs and leaves them 
        unmodified as outputs. Starting from version 0.19, 2D arrays will be treated 
        as 1D images with 3 channels.
    """
    if x.ndim == 3:
        n_c = x.shape[-1]
        if n_c == 1:
            x = x[..., 0]
        elif n_c == 3 or n_c == 4:
            x = skimage.color.rgb2gray(x)
        else:
            raise ValueError("Invalid no. of channels: {}".format(n_c))
    elif x.ndim == 2:
        x = x
    else:
        raise ValueError("Invalid shapes: {}".format(x.shape))
    
    if num_output_channels:
        x = np.stack([x] * num_output_channels, axis=-1)
    
    return x


def rgba2rgb(img, background=(0, 0, 0), binary_alpha=False):
    """ Remove alpha channel through alpha blending. 
        Equivalent but faster than skimage.color.rgba2rgb(img, background=(0, 0, 0))
    """
    if img.ndim < 3 or img.shape[-1] < 4:
        return img
    alpha_channel = img[..., -1:]
    if binary_alpha:
        res = img[..., :-1] & alpha_channel
    else:
        res = img[..., :-1] * (alpha_channel/255)
    if background is not None:
        res = np.where(alpha_channel, res, background)
    return res.astype(img.dtype)


def snmf(im_sda, w_init, beta=0.2):
    from nimfa import Snmf
    
    m = im_sda if im_sda.ndim == 2 else im_sda.reshape((-1, im_sda.shape[-1])).T
    m = m[:, np.isfinite(m).all(axis=0)]
    # m = convert_image_to_matrix(im_sda)
    mnf = Snmf(m, rank=m.shape[0] if w_init is None else w_init.shape[1],
               W=w_init, H=None if w_init is None else np.linalg.pinv(w_init).dot(m), 
               beta=beta)
    mnf.factorize()
    w = np.array(mnf.W)
    return w / np.sqrt((w ** 2).sum(0))


def estimate_stain_matrix(x, w_init, beta=0.2, I_0=255, plot=False):
    # import histomicstk as htk
    
    im_sda = rgb_to_sda(x, I_0=I_0)
    w_est = snmf(im_sda, w_init[:2].T, beta=0.2)
    # w_est = complement_stain_matrix(np.vstack([w_est.T, [0,0,0]]))
    w_est = np.vstack([w_est.T, [0,0,0]])
    
    if plot:
        # perform sparse color deconvolution
        im_deconv = color_deconvolution(x, w_est, I_0=I_0)
        
        print("Deconvolution with SNMF matrix: {}".format(w_est))
        fig, axes = plt.subplots(2, 2, figsize=(24, 24), sharex=True, sharey=True)
        ax = axes.ravel()
        ax[0].imshow(x)
        ax[0].set_title("Original image")
        ax[1].imshow(im_deconv[:, :, 0], cmap="gray")
        ax[2].imshow(im_deconv[:, :, 1], cmap="gray")
        ax[3].imshow(im_deconv[:, :, 2], cmap="gray")
        # ax[4].imshow(img_restore)
        # ax[4].set_title("Restore image")
    
    return w_est


def estimate_stain_matrix_htk(x, w_init, beta=0.2, I_0=255, plot=False):
    import histomicstk as htk
    
    im_sda = htk.preprocessing.color_conversion.rgb_to_sda(x, I_0=I_0)
    w_est = htk.preprocessing.color_deconvolution.separate_stains_xu_snmf(im_sda, w_init[:2].T, beta=beta)
    # w_est = complement_stain_matrix(np.vstack([w_est.T, [0,0,0]]))
    w_est = np.vstack([w_est.T, [0,0,0]])
    
    if plot:
        # perform sparse color deconvolution
        imDeconvolved = htk.preprocessing.color_deconvolution.color_deconvolution(x, w_est.T, I_0=I_0)
        print(image_stats(imDeconvolved.Stains[:, :, 2]) + [np.mean(imDeconvolved.Stains[:, :, 2]), np.std(imDeconvolved.Stains[:, :, 2])])
        # img_restore = htk.preprocessing.color_deconvolution.color_convolution(imDeconvolved.Stains, w_est.T, I_0=I_0)
        
        print("Deconvolution with SNMF matrix: {}".format(w_est))
        fig, axes = plt.subplots(2, 2, figsize=(24, 24), sharex=True, sharey=True)
        ax = axes.ravel()
        ax[0].imshow(x)
        ax[0].set_title("Original image")
        ax[1].imshow(imDeconvolved.Stains[:, :, 0], cmap="gray")
        ax[2].imshow(imDeconvolved.Stains[:, :, 1], cmap="gray")
        ax[3].imshow(imDeconvolved.Stains[:, :, 2], cmap="gray")
        # ax[4].imshow(img_restore)
        # ax[4].set_title("Restore image")
    
    return w_est


def estimate_stain_matrix_he(
    x="https://upload.wikimedia.org/wikipedia/commons/8/86/Emphysema_H_and_E.jpg",
    # w_init=np.array([[0.550,0.758,0.351],[0.398,0.634,0.600],[0.754,0.077,0.652]]), 
    w_init=np.array([[0.644211, 0.716556, 0.266844], [0.092789, 0.964111, 0.283111]]),
    beta=0.2, I_0=255, plot=False):
    
    # estimate he stain matrix
    if isinstance(x, str):
        x = rgba2rgb(skimage.io.imread(x))
    
    w = estimate_stain_matrix(x, w_init=w_init, beta=beta, I_0=I_0, plot=plot)
    # w_inv = np.linalg.inv(complement_stain_matrix(w))
    # print("Restore convolution matrix: {}".format(w))
    # print("Restore convolution matrix inverse: {}".format(w_inv))
    
    return w


def complement_stain_matrix(w):
    if np.linalg.norm(w[2]) <= 1e-16:
        w = np.array([w[0], w[1], np.cross(w[0], w[1])])
    return w / np.linalg.norm(w, axis=1, keepdims=True)


def rgb_to_sda(x, I_0=1.0, clip=True, epsilon=0.001):
    if clip:
        x = np.minimum(x, I_0)
    return -np.log(x+epsilon)/np.log(I_0+epsilon) + 1


def sda_to_rgb(x, I_0=1.0, clip=True, epsilon=0.001):
    x = I_0 ** (1 - x) - epsilon
    if clip:
        x = np.minimum(x, I_0)
    return x


def color_deconvolution(x, w, I_0=255, epsilon=1, rgb=False):
    # complement stain matrix if needed
    x = img_as('float')(x) * 255
    wc = complement_stain_matrix(w)
    
    x = np.minimum(x, I_0)
    x = -np.log(x + epsilon)/np.log(I_0 + epsilon) + 1
    x = np.dot(x, np.linalg.inv(wc))
    if rgb:
        x = sda_to_rgb(x, I_0=I_0, clip=True, epsilon=epsilon)
    
    return x


def color_convolution(x, w, I_0=255, epsilon=1, rgb=False, out_dtype='float'):
    if rgb:
        x = rgb_to_sda(x, I_0=I_0, clip=True, epsilon=epsilon)
    x = I_0 ** (1 - np.dot(x, w)) - epsilon
    x = np.minimum(x, I_0)
    
    return img_as(out_dtype)(x / 255)


def apply_to_channel(x, func, channel_axis=None, in_dtype='image', out_dtype='image', args=(), kwargs=dict()):
    """ Apply function to each channel. 
        Use channel_axis=None for data without channel layer,
        like 2D gray image.
    """
    out_dtype = x.dtype if out_dtype == 'image' else out_dtype
    if in_dtype != 'image':
        x = img_as(in_dtype)(x)
    if channel_axis is None:
        res = func(x, *args, **kwargs)
    else:
        if isinstance(x, np.ndarray):
            # channel_axis = channel_axis % x.ndim
            x = np.rollaxis(x, channel_axis, 0)
        res = np.stack([func(_, *args, **kwargs) for _ in x], axis=channel_axis)
        # x = np.rollaxis(x, 0, channel_axis + 1)
    return img_as(out_dtype)(res)
    

def label_masks(img, val_to_label, axis=CHANNEL_AXIS, dtype=None):
    """ Label an image based on given labels. """
    if img.ndim > 2:
        res = np.sum(np.all(img == val, axis=axis) * label 
                     for val, label in val_to_label.items())
    else:
        res = np.sum((img == val) * label 
                     for val, label in val_to_label.items())
    
    if dtype is not None:
        res = res.astype(dtype)
    
    return res
    

def flatten_masks(x, labels=None, label_to_val=None):
    """ deprecte in future. """
    return merge_masks(x, labels, label_to_val)


def merge_masks(x, labels=None, label_to_val=None, dtype=None):
    """ Transfer [masks(h, w)](N) + [labels](N) + value_map into image. 
        labels = None: function will treat each N mask with 
        a unique label in 1~N. 
        labels = integer: function will repeat the same value N times.
        label_to_val: a dictionary with each unique value in labels as
        key and the value_map[key] is used for pixels with label=key.
    """
    if isinstance(x, list):
        x = np.stack(x, axis=-1)
    n_channel = x.shape[-1]
    if labels is None:
        labels = np.arange(n_channel) + 1
    elif isinstance(labels, int):
        labels = np.ones(n_channel) * labels
    
    if n_channel > 0 and label_to_val is not None:
        labels = np.stack([label_to_val[x] for x in labels])
    dtype = labels.dtype if dtype is None else dtype
    
    return np.dot(x, labels).astype(dtype)


def stack_masks(img, val_to_label=None, criteria=lambda x: x.area > 1, channel_axis=-1):
    """ Transfer a mask img to a stacked mask and labels"""
    res = split_masks(img, val_to_label, criteria=criteria, channel_axis=channel_axis, 
                      mode='mask', bbox_mode='xyxy', dtype=bool)
    masks = [x['mask'] for x in res]
    labels = [x['label'] for x in res]
    
    masks = np.stack(masks, axis=channel_axis) if len(masks) else np.zeros(shape=img.shape + (0,), dtype=bool)
    labels = np.array(labels)
    return masks, labels


## regionprops name changed: https://scikit-image.org/docs/0.19.x/release_notes.html?highlight=regionprops
def split_masks(img, val_to_label, mode='instance', bbox_mode='xyxy', mask_mode='mask',
                channel_axis=-1, criteria=lambda x: x.area > 1, filled=False, **kwargs):
    """ Transfer a mask image to masks， bboxes and labels. 
    Arguments:
        img: the mask img
        val_to_label: transfer pixel value into label
        mode: the format of outputs, one of ['instance', 'semantic', 'coco', 'yolo']
            'instance': each object has a box, mask, label.
            'semantic': merge objects with same label into one big maks.
            'coco': return a coco annotation.
            'yolo': not implemented yet.
        bbox_mode: the bounding box format, one of ['xyxy', 'yxyx', 'xywh', 'cxcywh']
            (See https://pytorch.org/vision/stable/ops.html#torchvision.ops.box_convert)
            'xyxy': used in pytorch object detection. (default)
            'yxyx': is consistent with numpy array
            'xywh': used for coco and matplotlib.patch
            'cxcywh': used for yolo (not normalized), voc
        mask_mode: the format of output masks, one of ['mask', 'rle', 'polygons'].
            'mask': return mask images with the same size as img.
            'rle':  return compressed rles for transposed image. (See rle_encode and rle_decode.)
            'polygons': return a list of polygons. (See binary_mask_to_polygons.)
        channel_axis: default -1.
        criteria: a function takes a regionprop as input, filter unused object.
            default x.area > 1 to get rid of single point labels.
            (polygons will meet problem when single dot occurs on 4 corners.)
        kwargs: other parameters to decide the formats of mask and bbox
            dtype=bool, when mask_mode='mask', see img_as()
            compress=True (not used), when mask_mode='rle', see rle_encode
            flatten=False, when mask_mode='polygons', see binary_mask_to_polygons
    Return:
        list of objects: {'mask', 'bbox', 'label', 'area'}
    """
    assert mode in ['instance', 'semantic', 'coco', 'yolo'], f"Unsupported mode: {mode}."
    assert bbox_mode in ['xyxy', 'yxyx', 'xywh', 'cxcywh'], f"Unsupported bbox_mode: {bbox_mode}."
    assert mask_mode in ['mask', 'masks', 'rle', 'rles', 'polygons', 'poly'], f"Unsupported mask_mode: {mask_mode}."
    
    if channel_axis is not None:
        channel_axis = channel_axis % img.ndim
        h, w = [img.shape[_] for _ in range(img.ndim) if _ != channel_axis]
    else:
        h, w = img.shape
    res = []
    
    for val, label in val_to_label.items():
        if channel_axis is not None:
            img_label = np.all(img == val, axis=channel_axis)
        else:
            img_label = (img == val)
        if mode == 'semantic':
            res.append({'mask': img_label, 'label': label})
        else:
            img_label = skimage.measure.label(img_label, connectivity=1)
            for prop in skimage.measure.regionprops(img_label, intensity_image=img_label):
                ## force area > 1 to avoid polygon problem and remove empty mask
                if criteria(prop) and (prop['area'] > 1):
                    ## raw binary mask
                    x = np.zeros(img_label.shape, dtype=bool)
                    # x[prop.coords[:,0], prop.coords[:,1], ...] = True
                    y0, x0, y1, x1 = prop.bbox
                    x[y0:y1, x0:x1, ...] = prop.filled_image if filled else prop.image
                    if mode == 'coco':
                        iscrowd = kwargs.setdefault('iscrowd', False)
                        obj = binary_mask_to_coco_annotation(x, label=label, iscrowd=iscrowd)
                    else:
                        area = prop.filled_area if filled else prop.area
                        obj = {'mask': x, 'bbox': [x0, y0, x1, y1], 'area': area, 'label': label, 'size': [h, w]}
                    res.append(obj)
    
    ## merge duplicate labels for semantic, add bg layer, label = 0
    if mode == 'semantic':
        memo = {label: np.zeros((h, w), dtype=bool) for _, label in val_to_label.items()}
        not_annotated = np.ones((h, w), dtype=bool)
        
        for obj in res:
            memo[obj['label']] = np.logical_or(memo[obj['label']], obj['mask'])
            not_annotated = np.logical_and(not_annotated, ~obj['mask'])
        res = [{'mask': v, 'bbox': [0, 0, w, h], 'area': v.sum(), 'label': k, 'size': [h, w]} for k, v in memo.items()]
        if 0 not in memo:
            res.append({'mask': not_annotated, 'bbox': [0, 0, w, h], 
                        'area': not_annotated.sum(), 'label': 0, 'size': [h, w]})
    
    ## reformat boxes, masks if not coco
    if mode != 'coco':
        for _ in res:
            x0, y0, x1, y1 = _['bbox']
            if bbox_mode == 'yxyx':
                _['bbox'] = [y0, x0, y1, x1]
            elif bbox_mode == 'xyxy':
                _['bbox'] = [x0, y0, x1, y1]
            elif bbox_mode == 'xywh':
                _['bbox'] = [x0, y0, x1-x0, y1-y0]
            elif bbox_mode == 'cxcywh':
                _['bbox'] = [(x0+x1)/2, (y0+y1)/2, x1-x0, y1-y0]
            else:
                raise ValueError(f"bbox_mode: {bbox_mode} is not supported!")
            
            ## extract masks
            if mask_mode.startswith('rle'):
                # compress = kwargs.setdefault('compress', True)
                # x = binary_mask_to_rle(x, compress)
                _['mask'] = rle_encode(_['mask'])['counts']  # new approach use transposed rle
            elif mask_mode.startswith('poly'):
                flatten = kwargs.get('flatten', False)
                # poly_mode = 'yx' if bbox_mode == 'yxyx' else 'xy'
                _['mask'] = binary_mask_to_polygon(_['mask'], flatten=flatten, mode='xy')
            elif mask_mode.startswith('mask'):
                dtype = kwargs.get('dtype', bool)
                _['mask'] = img_as(dtype)(_['mask'])

    return res


def get_mask_bbox(mask):
    pos = np.where(mask > 0)
    if len(pos[0]):
        return [np.min(pos[1]), np.min(pos[0]), np.max(pos[1]), np.max(pos[0])]
    else:
        return None


def get_mask_area(mask):
    ## boolean mask, rle, polygon
    if isinstance(mask, np.ndarray):  # boolean mask
        return np.sum(mask > 0)
    elif isinstance(mask, dict):  # rle
        from pycocotools import mask as mask_utils
        return mask_utils.area(mask)
    else:  ## polygon
        return np.sum(np.ceil(polygon_areas(mask)))


def binary_mask_to_rle(x, compress=True):
    """ transfer a binary mask to rles. 
        compress = True will return compressed rle by pycocotools
        compress = False will return uncompressed rle
    """
    if compress:
        from pycocotools import mask as mask_utils
        return mask_utils.encode(np.asfortranarray(x.astype(np.uint8)))
    rle = {'counts': [], 'size': list(x.shape)}
    counts = rle.get('counts')
    for i, (value, elements) in enumerate(groupby(x.ravel(order='F'))):
        if i == 0 and value == 1:
            counts.append(0)
        counts.append(len(list(elements)))
    return rle

## paired function for contiguous. The rle is actually transposed.
def rle_encode(x):
    from pycocotools import mask as mask_utils
    
    assert x.data.contiguous, f"input tensor need to be contiguous."
    return mask_utils.encode(x.astype(np.uint8).T)


def rle_decode(x, size):
    from pycocotools import mask as mask_utils
    m = mask_utils.decode({'size': size, 'counts': x}).T
    # m is a uint8 image with [0, 1], that's confusing.
    return m.astype(bool)


def binary_mask_to_polygon(x, level=0.5, flatten=False, mode='xy', scale=1.0):
    """ Transfer a binary mask to polygons. 
        flatten = False will return original result from skimage.measure.find_contours
        flatten = True will convert result to "coco" polygons
        skimage.measure.find_contours gives open contours when meet edge and corners.
        This will cause trouble when revert back, so always pad image by 1 pixel.
    """
    h, w = x.shape[0], x.shape[1]
    x_pad = pad(x, pad_width=[(1, 1), (1, 1)], mode='constant', cval=False)
    polygons = skimage.measure.find_contours(x_pad, level)  # old scrip use 0.5 for binary
    polygons = [np.stack([np.clip(p[:,0]-1, 0, h-1), 
                          np.clip(p[:,1]-1, 0, w-1)], axis=-1) 
                for p in polygons]
    
    if not mode.startswith('yx'):
        polygons = [p[..., -1::-1] for p in polygons]
    
    if scale != 1.0:
        polygons = [p * scale for p in polygons]
        
    if flatten == True:
        return [np.flip(_, axis=1).ravel().tolist() for _ in polygons]
    else:
        return polygons


def polygon_to_binary_mask(x, size, mode='xy'):
    masks = []
    for p in x:
        p = np.array(p) if mode.startswith('yx') else np.array(p)[:,::-1]
        if SKIMAGE_VERSION >= '0.16':  ## a function in scikit-learn 0.16
            from skimage.draw import polygon2mask
            mask = polygon2mask(image_shape=size, polygon=p)
        else:
            image_shape = size
            vertex_row_coords, vertex_col_coords = p.T
            fill_row_coords, fill_col_coords = skimage.draw.polygon(
                vertex_row_coords, vertex_col_coords, image_shape)
            mask = np.zeros(image_shape, dtype=np.bool)
            mask[fill_row_coords, fill_col_coords] = True
        masks.append(mask)
    
    return np.stack(masks).any(0)


def polygon_to_binary_mask_v2(x, size, mode='xy'):
    res = np.zeros(size)
    x = [_.round().astype(int) for _ in x]
    cv2.fillPoly(res, pts=x, color=1)
    
    return res > 0


class Mask(object):
    def __init__(self, x, size, mode, clip=True):
        assert mode in ['mask', 'masks', 'rle', 'rles', 'polygons', 'poly'], f"Unsupported mask_mode: {mode}."
        if mode.startswith('poly') and clip:
            x = [np.clip(_, 0, [size[1], size[0]]) for _ in x]
        self.m = x
        self.size = [size[0], size[1]]
        self.mode = mode
    
    def convert(self, mode=None, dtype=None):
        if mode is None:
            return self
        elif mode.startswith('poly'):
            return self.poly()
        elif mode.startswith('mask'):
            return self.mask(dtype)
        elif model.startswith('rle'):
            return self.rle()
        else:
            raise ValueError(f"{mode} is not supported.")
    
    def poly(self):
        if self.mode.startswith('poly'):
            m = self.m
        elif self.mode.startswith('rle'):
            m = rle_decode(self.m, self.size)
            m = binary_mask_to_polygon(m)
        elif self.mode.startswith('mask'):
            m = binary_mask_to_polygon(self.m)

        return Mask(m, self.size, 'poly', clip=False)
    
    def mask(self, dtype=None):
        if self.mode.startswith('poly'):
            m = polygon_to_binary_mask_v2(self.m, self.size)
        elif self.mode.startswith('rle'):
            m = rle_decode(self.m, self.size)
        elif self.mode.startswith('mask'):
            m = self.m

        if dtype is not None:
            m = img_as(dtype)(m)

        return Mask(m, self.size, 'mask')

    def rle(self):
        if self.mode.startswith('poly'):
            m = rle_encode(polygon_to_binary_mask_v2(self.m, self.size))
        elif self.mode.startswith('rle'):
            m = self.m
        elif self.mode.startswith('mask'):
            m = rle_encode(self.m)
        
        return Mask(m, self.size, 'rle')
    
    def box(self):
        if self.mode.startswith('poly'):
            x, y = np.concatenate(self.m, axis=0).T
            return np.array([x.min(), y.min(), x.max(), y.max()]) if any(x) else np.zeros((4,))  # xyxy
        elif self.mode.startswith('rle'):
            m = rle_decode(self.m, self.size)
            r, c = np.where(m > 0)
            return np.array([c.min(), r.min(), c.max(), r.max()])
        elif self.mode.startswith('mask'):
            r, c = np.where(self.m > 0)
            return np.array([c.min(), r.min(), c.max(), r.max()])
    
    def hflip(self):
        if self.mode.startswith('poly'):
            m = [np.abs(_ - [self.size[1], 0]) for _ in self.m]
        elif self.model.startswith('rle'):
            m = rle_decode(self.m, self.size)
            m = rle_encode(m[:, ::-1, ...])
        elif self.mode.startswith('mask'):
            m = self.m[:, ::-1, ...]
        
        return Mask(m, self.size, self.mode, clip=False)

    def vflip(self):
        if self.mode.startswith('poly'):
            m = [np.abs(_ - [0, self.size[0]]) for _ in self.m]
        elif self.model.startswith('rle'):
            m = rle_decode(self.m, self.size)
            m = rle_encode(m[::-1, ...])
        elif self.mode.startswith('mask'):
            m = self.m[::-1, ...]
        
        return Mask(m, self.size, self.mode, clip=False)

    def t(self):
        if self.mode.startswith('poly'):
            m = [_[:, [1,0]] for _ in self.m]
        elif self.model.startswith('rle'):
            m = rle_decode(self.m, self.size)
            m = rle_encode(m.T)
        elif self.mode.startswith('mask'):
            m = self.m.T
        
        return Mask(m, [self.size[1], self.size[0]], self.mode, clip=False)

    def __bool__(self):
        if self.mode.startswith('poly'):
            return len(self.m) > 0
        elif self.mode.startswith('rle'):
            return len(self.m) > 0
        elif self.mode.startswith('mask'):
            return m.sum() > 0


class Box(object):
    def __init__(self, x, size, mode):
        assert mode in ['xyxy', 'yxyx', 'xywh', 'cxcywh'], f"Unsupported bbox_mode: {mode}."
        self.mask = x
        self.size = size
        self.mode = mode
    

def binary_mask_to_coco_annotation(x, mask_id=None, image_id=None, 
                                   label=None, iscrowd=False):
    """ Convert a binary mask to coco annotation. 
        Only mask (x) is required parameters, mask_id, image_id, label are
        set as None by default, don't forget to change it in result.
    """
    from pycocotools import mask as mask_utils
    
    height, width = x.shape
    rle = mask_utils.encode(np.asfortranarray(x.astype(np.uint8)))
    bbox = mask_utils.toBbox(rle).astype(np.int).tolist()
    area = 1.0 * mask_utils.area(rle)
    if iscrowd:
        mask = binary_mask_to_rle(x, compress=False)
    else:
        mask = binary_mask_to_polygon(x, flatten=True, mode='xy')
        if len(mask[0]) == 4:
            raise ValueError("polygon has item with len=4 (consider remove it), probably a dot at image corner.")
    return {"id" : mask_id, 
            "image_id" : image_id,
            "category_id": label, 
            "segmentation": mask, 
            "area": area, 
            "bbox": bbox, 
            "iscrowd": int(iscrowd),
           }


def decode_annotations(annotations, height=None, width=None, dtype='uint8'):
    """ Decode a coco annotations. 
        code will prioritize 'height', 'width' coded in annotations.
        If the above slots are not provided, the default height, width will be used.
    """
    mask_utils
    masks, labels, bboxes = [], [], []
    for obj in annotations:
        h = obj['height'] if 'height' in obj else height
        w = obj['width'] if 'width' in obj else width
        rles = mask_utils.frPyObjects(obj['segmentation'], h, w)
        if isinstance(rles, list):
            rles = mask_utils.merge(rles)
        bbox = mask_utils.toBbox(rles)
        mask = mask_utils.decode(rles)
        masks.append(img_as(dtype)(mask))
        labels.append(obj['category_id'])
        bboxes.append(bbox)
    return masks, labels, bboxes


def polygon_areas(p):
    """ Calculate the areas of a batch of polygons. (N) * k * 2
        https://stackoverflow.com/questions/24467972/calculate-area-of-polygon-given-x-y-coordinates
    """
    ## a batch of polygons with shape N * k * 2
    if isinstance(p, np.ndarray) and p.ndim > 2:
        x, y = p[...,0], p[...,1]
        a = np.einsum('ij,ij->i', x, np.roll(y, 1, axis=-1))
        b = np.einsum('ij,ij->i', y, np.roll(x, 1, axis=-1))
        return 0.5 * np.abs(a - b)
    else:
        def _area(p):
            p = np.array(p)
            x, y = p[...,0], p[...,1]
            a = np.dot(x, np.roll(y, 1))
            b = np.dot(y, np.roll(x, 1))
            return 0.5 * np.abs(a - b)
        
        if isinstance(p, list): ## list of polygons
            return [_area(_) for _ in p]
        else: ## single polygon
            return _area(p)


def polygon_triangulation(p):
    from scipy.spatial import Delaunay
    from matplotlib.path import Path
    
    p = np.array(p)
    tri = p[Delaunay(p).simplices] # N * 3 * 2
    ## remove triangles outside of polygons
    return tri[Path(p).contains_points(np.mean(tri, axis=1))]
        
        
def random_sampling_in_polygons(polygons, N, plot=False, seed=None):
    """ Randomly sampling points in polygons. 
        Codes are derived and optimized from 
        https://codereview.stackexchange.com/questions/69833/generate-sample-coordinates-inside-a-polygon?rq=1
    Sample code:
        polygon_1 = np.array([[22, 2], [0, 1], [2, 16], [11, 18], 
                             [12, 15], [8, 12], [10, 4], [20, 6], [22, 2]])
        polygon_2 = 30 - polygon_1
        res_1 = random_sampling_in_polygons([polygon_1], N=500, plot=True)
        res_2 = random_sampling_in_polygons([polygon_2, polygon_1], N=500, plot=True)
    """
    ## Set up random seed
    np.random.seed(seed)
    
    ## polygon triangulation
    tri_v, indices = [], []
    for i, _ in enumerate(polygons):
        tri = polygon_triangulation(_)
        tri_v.append(tri)
        indices.append((i, len(tri)))
    indices = np.repeat(*zip(*indices))
    tri_v = np.vstack(tri_v)
    
    ## randomly generate N pars for affine transformation
    a, b = np.random.uniform(-1.0, 1.0, size=(2, N))
    points = (((a+b>0) - 0.5) * np.array([a+b, -a, -b])).T + np.array([0, 0.5, 0.5])
    
    ## randomly sampling points based on triangle areas
    areas = polygon_areas(tri_v)
    alloc = np.random.choice(len(tri_v), size=N, p=areas/np.sum(areas))
    
    ## map pars into each triangle
    # res = np.array([np.dot(p, tri_v[i]) for p, i in zip(points, alloc)])
    res = np.einsum('ij,ijk->ik', points, tri_v[alloc])
    indices = indices[alloc]
    
    if plot:
        ## ploting
        patches = [Polygon(_) for _ in tri_v]
        patch_colors = np.array(100*np.random.rand(len(patches)))
        point_colors = patch_colors[alloc]

        fig, ax = plt.subplots()
        pc = PatchCollection(patches, alpha=0.4)
        pc.set_array(patch_colors)
        ax.add_collection(pc)
        ax.scatter(res[:,0], res[:,1], s=10/np.log(N), c=point_colors, alpha=0.5)
        plt.show()
    
    return res, indices


def to_categorical(y, num_classes=None):
    """ same function as utils_keras.to_categorical. """
    y = np.array(y, dtype='int')
    input_shape = y.shape
    y = y.ravel()
    if not num_classes:
        num_classes = np.max(y) + 1
    n = y.shape[0]
    categorical = np.zeros((n, num_classes), dtype=np.float32)
    categorical[np.arange(n), y] = 1
    output_shape = input_shape + (num_classes,)
    categorical = np.reshape(categorical, output_shape)
    
    return categorical


def iou_coef(y_true, y_pred, N_classes, mode='iou', batch_dim=None, binary=False, axis=-1, epsilon=1e-8):
    """ Calculate (soft) iou/dice coefficient for y_true ad y_pred
        target: [(batch_size), h, w, N_classes]
        output: [(batch_size), h, w, N_classes]
        
        Return: dice/iou coefficient for each classes. [(batch_size), N_classes]
                Apply weight to each classes, use 0 to ignor background
                dice_coef *= weights / tf.reduce_sum(weights)
                if a class is not exist in a sample, will return 0/0 = nan.
                So use funcitons that ignore nan (np.nan_mean) to summarize results.
    """    
    if binary:
        y_true = to_categorical(np.argmax(y_true, axis=axis), N_classes)
        y_pred = to_categorical(np.argmax(y_pred, axis=axis), N_classes)
    
    sum_axis = list(range(y_pred.ndim))
    del sum_axis[axis]
    if batch_dim is not None:
        del sum_axis[batch_dim]
    sum_axis = tuple(sum_axis)
    
    intersect = np.sum(y_true * y_pred, axis=sum_axis)
    union = np.sum(y_true + y_pred, axis=sum_axis) - intersect
    
    if mode == 'dice':
        res = 2.0 * intersect/(union + intersect) # + epsilon)
    elif mode == 'iou':
        res = 1.0 * intersect/(union) # + epsilon)
    else:
        raise ValueError(f"mode: {mode} is not supported.")
    
    return res


def unique_colors(x, channel_axis=None):
    if not channel_axis:
        return np.unique(x)
    else:
        return np.unique(x.reshape(channel_axis, x.shape[channel_axis]), axis=0)


def image_stats(x, channel_axis=None):
    if x is None:
        return None
    stats = [x.min(), x.max(), len(unique_colors(x, channel_axis))] if min(x.shape) > 0 else [None, None, None]
    return [x.shape, x.dtype] + stats


def display_image(x, title, mean=0., std=1., in_range=None, cmap=None, channel_axis=None):
    if x is None:
        return x, None, title, cmap
    
#     if channel_axis is not None and channel_axis != -1:
#         x = np.moveaxis(x, channel_axis, -1)
#         channel_axis = -1
    
    stats = image_stats(x, channel_axis)
    if in_range is None:
        in_range = skimage.dtype_limits(x)
    x = ((1.0 * x * std + mean) - in_range[0]) / in_range[1]
    ## a special case for 1 channel image
    if x.ndim == 3 and x.shape[-1] == 1:
        x = x[..., 0]
    return x, stats, title, cmap

## TODO: support inputs of [(mask, bbox, label)]
def display_masks(x, title, labels=None, label_to_val=None, cmap=plt.cm.nipy_spectral):
    if x is None or len(x) == 0:
        return x, None, title, cmap
    
    if not isinstance(x, np.ndarray):
        x = np.stack(x, axis=-1)
    stats = image_stats(x, channel_axis=None)
    x = merge_masks(x, labels=labels, label_to_val=label_to_val)
    return x, stats, title, cmap


def img_as(dtype):
    """ Convert images between different data types. 
        (Note that: skimage.convert is not a public function. )
        If input image has the same dtype and range, function will do nothing.
        (This check is included in skimage.convert, so no need to implement it here. )
        https://github.com/scikit-image/scikit-image/blob/master/skimage/util/dtype.py
        dtype: a string or a python dtype or numpy.dtype: 
               'float', 'float32', 'float64', 'uint8', 'int32', 'int64', 'bool', 
               float, uint8, bool, int,
               np.floating, np.float32, np.uint8, np.int, np.bool, etc
    """
    dtype = np.dtype(dtype)
    # return lambda x: skimage.convert(x, dtype, force_copy=False)
    dtype_name = dtype.name
    if dtype_name.startswith('float'):
        # convert(image, np.floating, force_copy=False)
        if dtype_name == 'float32':
            return skimage.img_as_float32
        elif dtype_name == 'float64':
            return skimage.img_as_float64
        else:
            return skimage.img_as_float
    elif dtype_name == 'uint8':
        # convert(image, np.uint8, force_copy=False)
        return skimage.img_as_ubyte
    elif dtype_name.startswith('uint'):
        # convert(image, np.uint16, force_copy=False)
        return skimage.img_as_uint
    elif dtype_name.startswith('int'):
        # convert(image, np.int16, force_copy=False)
        return skimage.img_as_int
    elif dtype_name == 'bool':
        # convert(image, np.bool_, force_copy)
        return skimage.img_as_bool
    else:
        raise ValueError(f"{dtype_name} is not a supported data type in skimage.")


def unpack_dict(kwargs, N):
    """ Unpack a dictionary of values into a list (N) of dictionaries. """
    return [dict((k, v[i]) for k, v in kwargs.items()) for i in range(N)]

