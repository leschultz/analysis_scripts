from PyQt5 import QtGui  # Added to be able to import ovito

from matplotlib import pyplot as pl

from scipy.stats import linregress, sem, ttest_ind
from functools import reduce

import pymatgen as mg
import pandas as pd
import scipy as sc
import numpy as np

import ast
import os

from ovito.modifiers import CalculateDisplacementsModifier
from ovito.modifiers import VoronoiAnalysisModifier
from ovito.modifiers import PythonScriptModifier
from ovito.io import import_file

import traj
import test
import dep


def autocovariance(x, n, mean, k, bias=0):
    '''
    Compute the autocovariance of a set.

    inputs:
            x = the list of data
            n = the size of data
            mean = the mean of the x-data
            k = the k-lag between values
            bias = adjust the bias calculation

    outputs:
            autocov = the autocovariance at a k-lag
    '''

    autocov = 0.0
    for i in np.arange(0, n-k):
        autocov += (x[i+k]-mean)*(x[i]-mean)

    autocov /= n-bias

    return autocov


def autocorrelation(x):
    '''
    Compute the autocorrelation for all possible k-lags.

    inputs:
            x = the data
    outputs:
            r = the autocorrelation at a k-lag
    '''

    n = len(x)  # Number of values
    mean = np.mean(x)  # Mean values
    denominator = autocovariance(x, n, mean, 0)  # Normalization factor
    k = np.arange(0, n+1)  # k-lag

    r = list(map(lambda lag: autocovariance(x, n, mean, lag)/denominator, k))

    return r


def batch_means(x, k):
    '''
    Divide data into bins to calculate error from batch means.

    inputs:
        x = data
        k = the correlation length
    outputs:
        e = the error
    '''

    bins = len(x)//k  # Approximate the number of bins with lenght k
    splits = np.array_split(x, bins)  # Split into bins
    means = list(map(np.mean, splits))  # Average each of the bins
    e = (np.var(means, ddof=1)/bins)**0.5  # Error

    return e


def settle_test(x, alpha=0.05):
    '''
    Calculate data that is outside a distribution with respect to the last bin.
    Return the cut index where settled data beggins.

    inputs:
        x = data
        alpha = the significance level
    outputs:
        xcut = index where data after is settled
    '''

    r = autocorrelation(x)
    k = np.argmax(np.array(r) <= 0)  # First value <= 0

    bins = len(x)//k  # Approximate the number of bins with length k
    splits = np.array_split(x, bins)  # Split into bins

    null = splits[-1]  # Should approximate the settled behavior
    p = list(map(lambda i: ttest_ind(i, null, equal_var=False)[1], splits))
    p = np.array(p)

    locs = np.where(p >= alpha)

    # Combine all bins where the p-value >= alpha
    xcut = []
    for loc in locs[0]:
        xcut += list(splits[loc])

    return xcut


def self_diffusion(x, y):
    '''
    Calculate self diffusion from MSD curve.

    inputs:
        x = time
        y = MSD
    outputs:
        d = diffusion coefficient [*10^-4 cm^2 s^-1]
    '''

    m, _, _, _, _ = linregress(x, y)  # Fit linear line
    d = m/6.0  # Divide by degrees of freedom

    return d


def msdmodify(frame, data):
    '''
    Access the per-particle displacement magnitudes computed by an existing
    Displacement Vectors modifier that precedes this custom modifier in the
    data pipeline. This loops over for all and each particle type.

    inputs:
        frame = the frame for trajectories considered
        data = a pipeline variable for data
    '''

    elements = [i.id for i in data.particles['Particle Type'].types]

    # Calculate diplacements
    dispmag = data.particles['Displacement Magnitude'].array

    # Compute MSD for all atoms
    msd = {'all': np.sum(dispmag**2)/len(dispmag)}

    # Compute MSD for a type of atom
    for item in elements:
        index = (data.particles['Particle Type'] == item)
        msd[item] = np.sum(dispmag[index]**2)/len(dispmag[index])

    # Export msd
    data.attributes['msd'] = msd


def gather_msd(node, start, stop):
    '''
    Calculate MSD for all and each element type.

    inputs:
        node = the loaded trajectories from Ovito
        start = the starting frame
        stop = the stopping frame
    outputs:
        dfmsd = dataframe for msd
    '''

    # The variables where data will be held
    msd = []

    # Compute the MSD for each frame of interest
    for frame in range(start, stop+1):

        out = node.compute(frame)
        msd.append(ast.literal_eval(out.attributes['msd']))

    dfmsd = pd.DataFrame(msd)

    return dfmsd


class job:
    '''
    Setup all the data per job for analysis.
    '''

    def __init__(self, path, export, data_path=False, plot_path=False):
        '''
        Create all the paths needed to save analysis data.

        inputs:
            self = The object reference
            path = The path to the data
            export = The path to export analysis
            data_path = The name of the folder to save analysis data
            plot_path = The name off the folder to save analysis plots
        '''

        # The location of the job
        self.path = path

        # Save paths
        export_path = os.path.join(export, path.strip('../'))

        if data_path:
            self.datapath = os.path.join(export_path, data_path)
            if not os.path.exists(self.datapath):
                os.makedirs(self.datapath)

        if plot_path:
            self.plotpath = os.path.join(export_path, plot_path)
            if not os.path.exists(self.plotpath):
                os.makedirs(self.plotpath)

        print('Analysis for: '+path)

    def input_file(self, depdotin):
        '''
        Gather parameters from the input file.

        inputs:
            self = The object reference
            depdotin = The name of the input file
        '''

        file_dep = os.path.join(self.path, depdotin)  # Input file
        self.file_dep = file_dep

        # Important paramters from the input file
        depparams = dep.info(file_dep)

        self.trajdumprate = depparams['trajdumprate']
        self.timestep = depparams['timestep']
        self.runsteps = depparams['runsteps']
        self.hold1 = depparams['hold1']
        self.hold2 = depparams['hold2']
        self.hold3 = depparams['hold3']
        self.increment = depparams['increment']
        self.elements = depparams['elements']

    def sys(self, testdotout):
        '''
        Gather thermodynamic data from the out file.

        inputs:
            self = The object reference
            testdotout = The name of the out file
        '''

        try:
            self.file_dep
        except Exception:

            message = 'Need to specify input file first.'
            raise ValueError(message)

        file_system = os.path.join(self.path, testdotout)  # Output file
        self.file_system = file_system

        # Thermodynamic data from test.out file
        self.dfsys = test.info(file_system)

        self.dfsys['time'] = self.dfsys['Step']*self.timestep

        # Use data at and after start of cooling
        condition = self.dfsys['Step'] >= self.hold1
        dfcool = self.dfsys[condition]

        return dfcool

    def box(self, trajdotlammpstrj):
        '''
        Gather trajectories from the trajectory file.

        inputs:
            self = The object reference
            trajdotlammpstrj = The name of the input file
        '''

        try:
            self.file_dep

        except Exception:
            message = 'Need to specify input file.'
            raise ValueError(message)

        file_trajs = os.path.join(self.path, trajdotlammpstrj)  # Trajectories
        self.file_trajs = file_trajs

        # Information from traj.lammpstrj file
        self.dftraj, counts = traj.info(file_trajs)

        self.dftraj['time'] = self.dftraj['Step']*self.timestep

        # The number of frames where temperatures where recorded
        frames = list(range(self.dftraj.shape[0]))
        self.dftraj['frame'] = np.array(frames)+1

    def msd(self, write=True, plot=True, verbose=True):
        '''
        Calculate MSD.

        inputs:
            self = The object reference
            write = Whether or not to save the fractions and temperatures
            plot = Whether or not to plot the fractions and temperatures
            verbose = Wheter or not to print calculation status

        outputs:
            dfmsd = Dataframe containing msd information
        '''

        if verbose:
            print('Calculating MSD')

        # Find the interval for the isothermal hold
        cutoff = sum(self.runsteps[:5])
        condition = (self.dftraj['Step'] >= cutoff)

        # Grab trajectory information from interval
        df = self.dftraj[condition]
        df = df.reset_index(drop=True)

        # The beggining frame
        frames = df['frame'].values
        start = frames[0]
        stop = frames[-1]

        dfmsd = gather_msd(self.file_trajs, start, stop)

        cols = [list(dfmsd.columns)[0]]+self.elements
        cols = cols[:len(dfmsd.columns)]
        dfmsd.columns = cols

        dfmsd['time'] = df['time']-df['time'][0]

        if write:
            msd_path = os.path.join(self.datapath, 'msd.txt')
            dfmsd.to_csv(msd_path, index=False)

        if plot:

            fig, ax = pl.subplots()

            plotcols = list(dfmsd.columns.difference(['time']))

            x = dfmsd['time'].values
            for col in plotcols:
                y = dfmsd[col].values

                ax.plot(
                        x,
                        y,
                        linestyle='none',
                        marker='.',
                        label='element: '+col
                        )

            ax.grid()
            ax.legend()

            ax.set_xlabel('Time [ps]')
            ax.set_ylabel(r'MSD $[A^{2}]$')

            fig.tight_layout()
            fig.savefig(os.path.join(self.plotpath, 'msd.png'))

            pl.close('all')

        return dfmsd

    def diffusion(self, alpha=0.05, write=True, plot=True, verbose=True):
        '''
        Calculate diffusion from multiple time origins (MTO).

        inputs:
            self = The object reference
            write = Whether or not to save the fractions and temperatures
            plot = Whether or not to plot the fractions and temperatures
            verbose = Wheter or not to print calculation status

        outputs:
            diff = The mean diffusion
            dfdiff = The MTO diffusion dataframe
        '''

        if verbose:
            print('Calculating MTO diffusion')

        # Find the interval for the isothermal hold
        cutoff = sum(self.runsteps[:5])
        condition = (self.dftraj['Step'] >= cutoff)

        # Grab trajectory information from interval
        df = self.dftraj[condition]
        df = df.reset_index(drop=True)

        # Reset time
        df['time'] = df['time']-df['time'][0]

        frames = df['frame'].values

        # Split data in half
        cut = frames.shape[0]//2
        split1 = frames[:cut]

        number = split1.shape[0]
        split2 = frames[cut:cut+number]

        # Each of the time origins
        time_origins = df['time'].values[:cut]
        time_endings = df['time'].values[cut:cut+number]

        # Load input data and create an ObjectNode with a data pipeline.
        node = import_file(self.file_trajs, multiple_frames=True)

        # Calculate per-particle displacements with respect to a start
        modifier = CalculateDisplacementsModifier()
        modifier.assume_unwrapped_coordinates = True
        modifier.reference.load(self.file_trajs)
        node.modifiers.append(modifier)

        # Insert custom modifier into the data pipeline.
        node.modifiers.append(PythonScriptModifier(function=msdmodify))

        # Collect diffusion coefficients
        data = []

        count = 1
        for start, stop in zip(split1, split2):

            if verbose:
                print(
                      'Calculating diffusion from time origin: ' +
                      str(count) +
                      '/'+str(number)
                      )

            # Change the reference frame for MSD
            node.modifiers[0].reference_frame = start
            dfmsd = gather_msd(node, start, stop)

            cols = [list(dfmsd.columns)[0]]+self.elements
            cols = cols[:len(dfmsd.columns)]
            dfmsd.columns = cols

            # Remove first value which is always zero
            dfmsd = dfmsd.loc[1:, :]

            # Calculate diffusion for each element and all
            d = dfmsd.apply(lambda i: self_diffusion(time_origins, i))
            data.append(d)

            count += 1

        dfdif = pd.DataFrame(data)

        # Truncate data based on two sided t-test
        settled_dif = dfdif.apply(lambda i: settle_test(i, alpha))

        # Determine autocorrelation of settled data
        auto = settled_dif.apply(autocorrelation)

        # Determine the first zero or negative autocorrelation value k-lag
        autocut = auto.apply(lambda i: np.argmax(np.array(i) <= 0))

        # Calculate the error from batch means and SEM
        difsemerr = settled_dif.apply(sem)  # SEM ddof=1
        cuts = iter(autocut.values)  # Iterate through correlation lengths
        difbatcherr = settled_dif.apply(lambda i: batch_means(i, next(cuts)))

        # Calculate diffusion
        diffusion = settled_dif.apply(np.mean)
        diffusion = [diffusion, difsemerr, difbatcherr]
        diffusion = pd.DataFrame(diffusion).T
        diffusion.columns = ['diffusion', 'sem', 'batch']
        diffusion['element'] = diffusion.index

        # Add the interval for MTO diffusion
        dfdif['start'] = time_origins
        dfdif['stop'] = time_endings

        # Convert settled data into data frame
        settled_dif = pd.DataFrame.from_dict(
                                             dict(settled_dif),
                                             orient='index'
                                             ).T

        if write:
            dfdif.to_csv(
                         os.path.join(self.datapath, 'diffusion_mto.txt'),
                         index=False
                         )

            name = os.path.join(self.datapath, 'diffusion_mto_settled.txt')
            settled_dif.to_csv(
                               name,
                               index=False
                               )

            name = os.path.join(self.datapath, 'diffusion_settled.txt')
            diffusion.to_csv(
                             name,
                             index=False
                             )

        if plot:

            # Plot MTO diffusion
            fig, ax = pl.subplots(2)

            plotcols = list(dfdif.columns.difference(['start', 'stop']))

            x = dfdif['start'].values
            for col in plotcols:
                y = dfdif[col].values
                ysettled = settled_dif[col].values

                ax[0].plot(
                           x,
                           y,
                           linestyle='none',
                           marker='.',
                           label='element: '+col
                           )

                color = ax[0].get_lines()[-1].get_color()

                ax[1].plot(
                           ysettled,
                           linestyle='none',
                           marker='.',
                           color=color,
                           label='element: '+col
                           )

            ax[0].grid()
            ax[0].legend()

            ax[0].set_xlabel('Time Origin from '+str(time_endings[-1])+' [ps]')
            ax[0].set_ylabel(r'Diffusion $[10^{-4} cm^{2} s^{-1}]$')

            ax[1].grid()
            ax[1].legend()

            ax[1].set_ylabel(r'Diffusion $[10^{-4} cm^{2} s^{-1}]$')
            ax[1].set_xlabel(r'Settled Data Index $(\alpha='+str(alpha)+')$')

            fig.tight_layout()

            fig.savefig(os.path.join(self.plotpath, 'diffusion_mto.png'))

            # Plot autocorrelation functions
            fig, ax = pl.subplots()

            zipitem = zip(auto.iteritems(), autocut.iteritems())
            for autoitems, cutitems in zipitem:

                ax.plot(
                        autoitems[1],
                        marker='.',
                        label='element: '+autoitems[0]
                        )

                color = ax.get_lines()[-1].get_color()

                vlinelabel = (
                              cutitems[0] +
                              ' correlation length: ' +
                              str(cutitems[1])
                              )

                ax.axvline(
                           cutitems[1],
                           linestyle=':',
                           color=color,
                           label=vlinelabel
                           )

            ax.grid()
            ax.legend()

            ax.set_xlabel('k-lag [-]')
            ax.set_ylabel('Autocorrelation [-]')

            fig.tight_layout()
            fig.savefig(os.path.join(
                                     self.plotpath,
                                     'mto_settled_autocorrelation.png'
                                     ))

            pl.close('all')

        return dfdif

    def ico(
            self,
            edges,
            faces,
            threshold=0.1,
            write=True,
            verbose=True
            ):
        '''
        Compute the ICO fraction at Tg.

        inputs:
            self = the object reference
            edges = the number of VP edges
            faces = the number of minimum faces for the specified edges
            threshold = the maximum length for a VP edge
            write = whether or not to save the fractions and temperatures
            verbose = Wheter or not to print calculation status

        outputs:
            fraction = the ICO fraction at Tg
        '''

        if verbose:
            print('Calculating ICO fraction near Tg')

        edges -= 1  # Compensate for indexing

        # Find the interval for the isothermal hold
        cutoff = sum(self.runsteps[:5])
        condition = (self.dftraj['Step'] >= cutoff)

        # Grab trajectory information from interval
        df = self.dftraj[condition]
        df = df.reset_index(drop=True)

        # Reset time
        df['time'] = df['time']-df['time'][0]

        # Load input data and create an ObjectNode with a data pipeline.
        node = import_file(self.file_trajs, multiple_frames=True)

        voro = VoronoiAnalysisModifier(
                                       compute_indices=True,
                                       use_radii=False,
                                       edge_threshold=threshold
                                       )

        node.modifiers.append(voro)

        vp_indexes = []
        for frame in df['frame'][:3]:
            out = node.compute(frame)

            indexes = out.particle_properties['Voronoi Index'].array
            vp_indexes.append(indexes)

        # Combine all the frames
        vp_indexes = [pd.DataFrame(i) for i in vp_indexes]
        dfindexes = pd.concat(vp_indexes)
        dfindexes = dfindexes.fillna(0)  # Replace na with zero
        dfindexes = dfindexes.astype(int)  # Make sure all counts are integers
        dfindexes = dfindexes.reset_index(drop=True)

        indexes = dfindexes.values
        indexes = indexes[:, edges]  # Gather edge bin

        count = sum(indexes >= faces)  # Count condition
        fraction = count/indexes.shape[0]  # Calculate fraction

        if write:

            write_name = os.path.join(self.datapath, 'ico_at_tg.txt')
            with open(write_name, 'w+') as outfile:
                outfile.write(str(fraction))

        return fraction
