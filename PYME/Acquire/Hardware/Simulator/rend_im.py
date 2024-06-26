#!/usr/bin/python

##################
# rend_im.py
#
# Copyright David Baddeley, 2009
# d.baddeley@auckland.ac.nz
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##################

from PYME.Analysis.PSFGen import *
from numpy.fft import ifftshift, fftn, ifftn
from . import fluor
from PYME.Analysis import MetaData
from PYME.localization import cInterp

try:
    import cPickle as pickle
except ImportError:
    import pickle
    
from scipy import ndimage
import numpy as np

from PYME.IO.FileUtils.nameUtils import getFullExistingFilename
import multiprocessing
import threading
from PYME.Deconv.wiener import resizePSF

#import threading
#tLock = threading.Lock()

def renderIm(X, Y, z, points, roiSize, A):
    #X = mgrid[xImSlice]
    #Y = mgrid[yImSlice]
    im = np.zeros((len(X), len(Y)), 'f')

    P = np.arange(0,1.01,.1)

    for (x0,y0,z0) in points:
        ix = abs(X - x0).argmin()
        iy = abs(Y - y0).argmin()

        imp =genWidefieldPSF(X[(ix - roiSize):(ix + roiSize + 1)], Y[(iy - roiSize):(iy + roiSize + 1)], z, P,A*1e3, x0,y0,z0,depthInSample=0)
        #print imp.shape
        im[(ix - roiSize):(ix + roiSize + 1), (iy - roiSize):(iy + roiSize + 1)] += imp[:,:,0]
    
    #print im.shape
    return im



IntXVals = None
IntYVals = None
IntZVals = None

#interpModel = None

interpModel_by_chan = [None, None, None, None]

def interpModel(chan=0):
    im =  interpModel_by_chan[chan]
    if im is None and not chan == 0:
        return interpModel_by_chan[0]
    else:
        return im
        

dx = None
dy = None
dz = None

mdh = MetaData.NestedClassMDHandler(MetaData.TIRFDefault)
mdh['voxelsize.z'] = 0.05 # set 50nm z spacing so we are not relying quite as much on interpolation for the z shape


def set_pixelsize_nm(pixelsize):
    mdh['voxelsize.x'] = 1e-3*pixelsize
    mdh['voxelsize.y'] = 1e-3 * pixelsize

def genTheoreticalModel(md, zernikes={}, **kwargs):
    from PYME.Analysis.PSFGen import fourierHNA
    global IntXVals, IntYVals, IntZVals, dx, dy, dz

    if True:#not dx == md.voxelsize.x*1e3 or not dy == md.voxelsize.y*1e3 or not dz == md.voxelsize.z*1e3:
    
        vs = md.voxelsize_nm
        IntXVals = vs.x * np.mgrid[-150:150]
        IntYVals = vs.y * np.mgrid[-150:150]
        IntZVals = vs.z * np.mgrid[-30:30]

        dx, dy, dz = vs

        P = np.arange(0,1.01,.01)

        #interpModel = genWidefieldPSF(IntXVals, IntYVals, IntZVals, P ,1e3, 0, 0, 0, 2*pi/525, 1.47, 10e3).astype('f')
        im = fourierHNA.GenZernikeDPSF(IntZVals, zernikes, X=IntXVals, Y=IntYVals, dx=vs.x, **kwargs)
        
        #print('foo')
        #print((interpModel.strides, interpModel.shape))
        
        for i in range(1, len(interpModel_by_chan)):
            interpModel_by_chan[i] = None

        interpModel_by_chan[0] = np.maximum(im/im[:,:,int(len(IntZVals)/2)].sum(), 0) #normalise to 1 and clip
        
        
def genTheoreticalModel4Pi(md, zernikes=[{},{}], phases=[0, np.pi/2, np.pi, 3*np.pi/2], **kwargs):
    from PYME.Analysis.PSFGen import fourierHNA
    global IntXVals, IntYVals, IntZVals, dx, dy, dz
                                                                                                              
    if True:#not dx == md.voxelsize.x*1e3 or not dy == md.voxelsize.y*1e3 or not dz == md.voxelsize.z*1e3:
    
        vs = md.voxelsize_nm
        IntXVals = vs.x * np.mgrid[-150:150]
        IntYVals = vs.y * np.mgrid[-150:150]
        IntZVals = 20 * np.mgrid[-60:60]
                                                                                                              
        dx, dy = vs.x, vs.y
        dz = 20.#md.voxelsize.z*1e3
                                                                                                              
        for i, phase in enumerate(phases):
            print('Simulating 4Pi PSF for channel %d' % i)
            #interpModel = genWidefieldPSF(IntXVals, IntYVals, IntZVals, P ,1e3, 0, 0, 0, 2*pi/525, 1.47, 10e3).as
            im = fourierHNA.Gen4PiPSF(IntZVals, phi=phase, zernikeCoeffs=zernikes, X=IntXVals, Y=IntYVals, dx=vs.x, **kwargs)
                                                                                                                  
            zm =  int(len(IntZVals)/2)
            norm = im[:,:,(zm-10):(zm+10)].sum(1).sum(0).max() #due to interference we can have slices with really low sum
            interpModel_by_chan[i] = np.maximum(im/norm, 0) #normalise to 1 and clip

def get_psf():
    from PYME.IO.image import ImageStack
    from PYME.IO.MetaDataHandler import NestedClassMDHandler
    
    mdh = NestedClassMDHandler()
    mdh['ImageType'] = 'PSF'
    mdh['voxelsize.x'] = dx/1e3
    mdh['voxelsize.y'] = dy/1e3
    mdh['voxelsize.z'] = dz/1e3
    
    im = ImageStack(data=[c for c in interpModel_by_chan if not c is None], mdh=mdh, titleStub='Simulated PSF')
    
    return im


def setModel(modName, md):
    global IntXVals, IntYVals, IntZVals, dx, dy, dz
    from PYME.IO import load_psf
    
    mod, vs_nm = load_psf.load_psf(modName)
    mod = resizePSF(mod, interpModel().shape)

    IntXVals = vs_nm.x * np.mgrid[-(mod.shape[0]/2.):(mod.shape[0]/2.)]
    IntYVals = vs_nm.y * np.mgrid[-(mod.shape[1]/2.):(mod.shape[1]/2.)]
    IntZVals = vs_nm.z * np.mgrid[-(mod.shape[2]/2.):(mod.shape[2]/2.)]

    dx, dy, dz = vs_nm

    #interpModel = np.maximum(mod/mod.max(), 0) #normalise to 1
    interpModel_by_chan[0] = np.maximum(mod/mod[:,:,len(IntZVals)/2].sum(), 0) #normalise to 1 and clip

def interp(X, Y, Z):
    X = np.atleast_1d(X)
    Y = np.atleast_1d(Y)
    Z = np.atleast_1d(Z)

    ox = X[0]
    oy = Y[0]
    oz = Z[0]

    rx = (ox % dx)/dx
    ry = (oy % dy)/dy
    rz = (oz % dz)/dz

    fx = int(len(IntXVals)/2) + int(ox/dx)
    fy = int(len(IntYVals)/2) + int(oy/dy)
    fz = int(len(IntZVals)/2) + int(oz/dz)

    #print fx
    #print rx, ry, rz

    xl = len(X)
    yl = len(Y)
    zl = len(Z)

    #print xl
    im = interpModel()

    m000 = im[fx:(fx+xl),fy:(fy+yl),fz:(fz+zl)]
    m100 = im[(fx+1):(fx+xl+1),fy:(fy+yl),fz:(fz+zl)]
    m010 = im[fx:(fx+xl),(fy + 1):(fy+yl+1),fz:(fz+zl)]
    m110 = im[(fx+1):(fx+xl+1),(fy+1):(fy+yl+1),fz:(fz+zl)]

    m001 = im[fx:(fx+xl),fy:(fy+yl),(fz+1):(fz+zl+1)]
    m101 = im[(fx+1):(fx+xl+1),fy:(fy+yl),(fz+1):(fz+zl+1)]
    m011 = im[fx:(fx+xl),(fy + 1):(fy+yl+1),(fz+1):(fz+zl+1)]
    m111 = im[(fx+1):(fx+xl+1),(fy+1):(fy+yl+1),(fz+1):(fz+zl+1)]

    #print m000.shape

#    m = scipy.sum([((1-rx)*(1-ry)*(1-rz))*m000, ((rx)*(1-ry)*(1-rz))*m100, ((1-rx)*(ry)*(1-rz))*m010, ((rx)*(ry)*(1-rz))*m110,
#        ((1-rx)*(1-ry)*(rz))*m001, ((rx)*(1-ry)*(rz))*m101, ((1-rx)*(ry)*(rz))*m011, ((rx)*(ry)*(rz))*m111], 0)

    m = ((1-rx)*(1-ry)*(1-rz))*m000 + ((rx)*(1-ry)*(1-rz))*m100 + ((1-rx)*(ry)*(1-rz))*m010 + ((rx)*(ry)*(1-rz))*m110+((1-rx)*(1-ry)*(rz))*m001+ ((rx)*(1-ry)*(rz))*m101+ ((1-rx)*(ry)*(rz))*m011+ ((rx)*(ry)*(rz))*m111
    #print m.shape
    return m

def interp2(X, Y, Z):
    X = atleast_1d(X)
    Y = atleast_1d(Y)
    Z = atleast_1d(Z)

    ox = X[0]
    oy = Y[0]
    oz = Z[0]

    rx = (ox % dx)/dx
    ry = (oy % dy)/dy
    rz = (oz % dz)/dz

    fx = int(len(IntXVals)/2) + int(ox/dx)
    fy = int(len(IntYVals)/2) + int(oy/dy)
    fz = int(len(IntZVals)/2) + int(oz/dz)

    #print fx
    #print rx, ry, rz

    xl = len(X)
    yl = len(Y)
    zl = len(Z)

    #print xl
    
    im = interpModel()

    m000 = im[fx:(fx+xl),fy:(fy+yl),fz:(fz+zl)]
    m100 = im[(fx+1):(fx+xl+1),fy:(fy+yl),fz:(fz+zl)]
    m010 = im[fx:(fx+xl),(fy + 1):(fy+yl+1),fz:(fz+zl)]
    m110 = im[(fx+1):(fx+xl+1),(fy+1):(fy+yl+1),fz:(fz+zl)]

    m001 = im[fx:(fx+xl),fy:(fy+yl),(fz+1):(fz+zl+1)]
    m101 = im[(fx+1):(fx+xl+1),fy:(fy+yl),(fz+1):(fz+zl+1)]
    m011 = im[fx:(fx+xl),(fy + 1):(fy+yl+1),(fz+1):(fz+zl+1)]
    m111 = im[(fx+1):(fx+xl+1),(fy+1):(fy+yl+1),(fz+1):(fz+zl+1)]

    #print m000.shape

#    m = scipy.sum([((1-rx)*(1-ry)*(1-rz))*m000, ((rx)*(1-ry)*(1-rz))*m100, ((1-rx)*(ry)*(1-rz))*m010, ((rx)*(ry)*(1-rz))*m110,
#        ((1-rx)*(1-ry)*(rz))*m001, ((rx)*(1-ry)*(rz))*m101, ((1-rx)*(ry)*(rz))*m011, ((rx)*(ry)*(rz))*m111], 0)

    m = ((1-rx)*(1-ry)*(1-rz))*m000 + ((rx)*(1-ry)*(1-rz))*m100 + ((1-rx)*(ry)*(1-rz))*m010 + ((rx)*(ry)*(1-rz))*m110+((1-rx)*(1-ry)*(rz))*m001+ ((rx)*(1-ry)*(rz))*m101+ ((1-rx)*(ry)*(rz))*m011+ ((rx)*(ry)*(rz))*m111

    r000 = ((1-rx)*(1-ry)*(1-rz))
    r100 = ((rx)*(1-ry)*(1-rz))
    r010 = ((1-rx)*(ry)*(1-rz))
    r110 = ((rx)*(ry)*(1-rz))
    r001 = ((1-rx)*(1-ry)*(rz))
    r101 = ((1-rx)*(ry)*(rz))
    r011 = ((1-rx)*(ry)*(rz))
    r111 = ((rx)*(ry)*(rz))

    m = r000*m000
    m[:] = m[:] + r100*m100
    m[:] = m[:] + r010*m010
    m[:] = m[:] + r110*m110
    m[:] = m[:] + r001*m001
    m[:] = m[:] + r101*m101
    m[:] = m[:] + r011*m011
    m[:] = m[:] + r111*m111

    m = r000*m000 + r100*m100 + r010*m010 + r110*m110 + r001*m001 + r101*m101 + r011*m011 + r111*m111
    #print m.shape
    return m

def interp3(X, Y, Z):
    X = np.atleast_1d(X)
    Y = np.atleast_1d(Y)
    Z = np.atleast_1d(Z)

    ox = X[0]
    oy = Y[0]
    oz = Z[0]

    xl = len(X)
    yl = len(Y)
    zl = len(Z)
    
    return cInterp.Interpolate(interpModel(), ox,oy,oz,xl,yl,dx,dy,dz)[:,:,None]

@fluor.registerIllumFcn
def PSFIllumFunction(fluors, position):
    im = interpModel()
    xi = np.maximum(np.minimum(np.round_((fluors['x'] - position[0])/dx + im.shape[0]/2).astype('i'), im.shape[0]-1), 0)
    yi = np.maximum(np.minimum(np.round_((fluors['y'] - position[1])/dy + im.shape[1]/2).astype('i'), im.shape[1]-1), 0)
    zi = np.maximum(np.minimum(np.round_((fluors['z'] - position[2])/dz + im.shape[2]/2).astype('i'), im.shape[2]-1), 0)

    return im[xi, yi, zi]

illPattern = None
illZOffset = 0
illPCache = None
illPKey = None

def setIllumPattern(pattern, z0):
    global illPattern, illZOffset, illPCache
    sx, sy = pattern.shape
    im = interpModel()
    psx, psy, sz = im.shape
    
    
    
    il = np.zeros([sx,sy,sz], 'f')
    il[:,:,sz/2] = pattern
    ps = np.zeros_like(il)
    if sx > psx:
        ps[(sx/2-psx/2):(sx/2+psx/2), (sy/2-psy/2):(sy/2+psy/2), :] = im
    else:
        ps[:,:,:] = im[(psx/2-sx/2):(psx/2+sx/2), (psy/2-sy/2):(psy/2+sy/2), :]
    ps= ps/ps[:,:,sz/2].sum()
    
    illPattern = abs(ifftshift(ifftn(fftn(il)*fftn(ps)))).astype('f')
    
    illPCache = None

illum_roi_size=256

@fluor.registerIllumFcn
def ROIIllumFunction(fluors, position):
    '''
    Very crude ROI-based illumination. Assumes hard edges, no diffraction.
    '''
    
    xi = np.round_((fluors['x'] - position[0])/mdh.voxelsize_nm.x) 
    yi = np.round_((fluors['y'] - position[1])/mdh.voxelsize_nm.y) 
    #zi = np.round_((fluors['z'] - position[2])/dz)

    return (xi>0)*(xi<illum_roi_size)*(yi>0)*(yi<illum_roi_size)

    
    
@fluor.registerIllumFcn
def patternIllumFcn(fluors, position):
    global illPKey, illPCache
    key = hash((fluors[0]['x'], fluors[0]['y'], fluors[0]['z']))
    
    if not illPCache is None and illPKey == key:
        return illPCache
    else:
        illPKey = key
        x = fluors['x']/dx + illPattern.shape[0]/2
        y = fluors['y']/dy + illPattern.shape[1]/2
        z = (fluors['z'] - illZOffset)/dz + illPattern.shape[2]/2
        illPCache = ndimage.map_coordinates(illPattern, [x, y, z], order=1, mode='nearest')
        return illPCache

SIM_k = np.pi/180.
#SIM_ky = 0# 2*pi/180.
SIM_theta = 0
SIM_phi = 0

@fluor.registerIllumFcn
def SIMIllumFcn(fluors, postion):
    
    x = fluors['x']#/dx + illPattern.shape[0]/2
    y = fluors['y']#/dy + illPattern.shape[1]/2
    #z = (fluors['z'] - illZOffset)/dz + illPattern.shape[2]/2
    #return ndimage.map_coordinates(illPattern, [x, y, z], order=1, mode='nearest')
    
    kx = np.cos(SIM_theta)*SIM_k
    ky = np.sin(SIM_theta)*SIM_k
    
    return (1 + np.cos(x*kx + y*ky + SIM_phi))/2


    
def _rFluorSubset(im, fl, A, x0, y0, z, dx, dy, dz, maxz, ChanXOffsets=[0,], ChanZOffsets=[0,], ChanSpecs = None):
    if ChanSpecs is None:
        z_ = np.clip(z - fl['z'], -maxz, maxz).astype('f')
        roiSize = np.minimum(8 + np.abs(z_) * (2.5 / dx), 140).astype('i')
        cInterp.InterpolateInplaceM(interpModel(), im, (fl['x'] - x0), (fl['y'] - y0), z_, A, roiSize,dx,dy,dz)
    else:
        for x_offset, z_offset, spec_chan, chan in zip(ChanXOffsets, ChanZOffsets, ChanSpecs, range(len(ChanSpecs))):
            z_ = np.clip(z - fl['z'] + z_offset, -maxz, maxz).astype('f')
            roiSize = np.minimum(8 + np.abs(z_) * (2.5 / dx), 140).astype('i')
            cInterp.InterpolateInplaceM(interpModel(chan), im, (fl['x'] - x0 + x_offset), (fl['y'] - y0),
                                        z_, A * fl['spec'][:, spec_chan], roiSize, dx, dy, dz)


def simPalmImFI(X,Y, z, fluors, intTime=.1, numSubSteps=10, roiSize=100, laserPowers = [.1,1], position=[0,0,0], illuminationFunction='ConstIllum', ChanXOffsets=[0,], ChanZOffsets=[0,], ChanSpecs = None, im=None):
    if interpModel() is None:
        genTheoreticalModel(mdh)
        
    if im is None:
        im = np.zeros((len(X), len(Y)), 'f')
    
    if fluors is None:
        return im
    
    A = np.zeros(len(fluors.fl), 'f')
    
    for n  in range(numSubSteps):
        A += fluors.illuminate(laserPowers,intTime/numSubSteps, position=position, illuminationFunction=illuminationFunction)

    
    dx = X[1] - X[0]
    dy = Y[1] - Y[0]
    
    maxz = dz*(interpModel().shape[2]/2 - 1)
    
    x0 = X[0]
    y0 = Y[0]

    m = A > .1
    
    fl = fluors.fl[m]
    A2 = A[m]

    
    nCPUs = int(min(multiprocessing.cpu_count(), len(A2)))
    
    if nCPUs > 0:
        threads = [threading.Thread(target = _rFluorSubset, args=(im, fl[i::nCPUs], A2[i::nCPUs], x0, y0, z, dx, dy, dz, maxz, ChanXOffsets, ChanZOffsets, ChanSpecs)) for i in range(nCPUs)]
    
        for p in threads:
            p.start()
    
        for p in threads:
            p.join()

    return im



