from scipy import stats as st

from os.path import join

import pandas as pd
import numpy as np

import sys
import os

datadirname = 'analysis_data'
externaldirname = 'data_external'

apdfile = 'apd_single.txt'

cxpath = [sys.argv[1], externaldirname, 'All_jobs_with_crystal_info.csv']
dfcx = pd.read_csv(join(*cxpath))

# Modify Ben's columns for merging datasets
alloys = dfcx['alloy']
elements = [i.split('-')[1] for i in alloys]
comp = dfcx['Comp']
comp = ['{:.2f}'.format(np.round(i, 2)) for i in comp]
comp = [i+j for i, j in zip(elements, comp)]
dfcx['Comp'] = comp

dfcx = dfcx[['alloy', 'Comp', 'Thold', 'Job', 'Run Crystallized']]
dfcx.columns = [
                'System',
                'Composition [decimal]',
                'Steps [-]',
                'Job',
                'Crystallization'
                ]

# Build a dataframe for all collected Tg values
columns = [
           'System',
           'Composition [decimal]',
           'Steps [-]',
           'Job',
           'apd',
           ]

dfapd = pd.DataFrame(columns=columns)

count = 0
for item in os.walk(sys.argv[1]):

    if datadirname not in item[0]:
        continue

    if '-' not in item[0]:
        continue

    names = item[0].split('/')[-5:-1]

    apdpath = join(item[0], apdfile)
    if os.path.exists(apdpath):
        apd = np.loadtxt(apdpath, dtype=float)
    else:
        apd = np.nan

    row = names+[apd]

    dfapd.loc[count] = row  # Append a row to the dataframe

    count += 1

dfapd['Steps [-]'] = dfapd['Steps [-]'].apply(pd.to_numeric)

# The columns to merge crystallization and tg dataframes
mergecolumns = [
                'System',
                'Composition [decimal]',
                'Steps [-]',
                'Job',
                ]

df = pd.merge(dfapd, dfcx, on=mergecolumns)

# Alphabetically sort the dataframe
df = df.sort_values(
                    by=[
                        'System',
                        'Composition [decimal]',
                        'Steps [-]',
                        'Job'
                        ]
                    )

df = df.reset_index(drop=True)

# Create the path to work in
workdir = join(sys.argv[1], datadirname)
if not os.path.exists(workdir):
    os.makedirs(workdir)

df.to_html(join(*[sys.argv[1], datadirname, 'allapd.html']), index=False)
df.to_csv(join(*[sys.argv[1], datadirname, 'allapd.txt']), index=False)

# Filter by data that has not crystallized
df = df[df['Crystallization'] == False]
df = df[columns]  # Remove crystallization column
df = df.loc[:, df.columns != 'Job']  # Remove job column

dfmean = df.groupby(columns[:-2]).agg([np.average])
dfsem = df.groupby(columns[:-2]).agg([st.sem])

df = pd.merge(dfmean, dfsem, how='inner', on=mergecolumns[:-1])
df = pd.DataFrame(df.to_records())

df.to_html(join(*[sys.argv[1], datadirname, 'meanapd.html']), index=False)
df.to_csv(join(*[sys.argv[1], datadirname, 'meanapd.txt']), index=False)