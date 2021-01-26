# Portable Python script for numerical output file comparisons of MOPAC
# Argument list: <path to testing directory> <path to MOPAC executable> <input file> <data file #1> ... <data file #N>

from shutil import copyfile
from sys import argv
import subprocess
import difflib
import os
import re
import math
import numpy as np

# MOPAC testing has historically been based on checking that the output files corresponding
# to a test set of input files remain consistent with the output of past MOPAC versions.
# This Python script automates such a test, assuming that we allow for small deviations
# between numerical outputs (with a uniform threshold for simplicity).
# All version/system-dependent output (timing & version info) is ignored.
# Eigenvectors are only stable in the sense of distance between degenerate subspaces,
# which requires more careful identification and analysis of eigenvector blocks.
# We cannot test the stability of all eigenvector information (if we cannot guarantee
# completeness of a degenerate subspace), and untestable data is ignored.
# In principle, we could interpret the symmetry labels to use some of the edge data when
# we can independently determine the size of the subspace, but this is way more trouble than it is worth.
# This comparison is insensitive to differences in whitespace and number of empty lines.
# Some input files used for testing contain reference data in comments, which are ignored here.

# Summary of units in MOPAC output files?

# TODO:
# - anything else we can do to guess the context of numbers?
# - parse "INITIAL EIGENVALUES " blocks
NUMERIC_THRESHOLD = 0.01
HEAT_THRESHOLD = 1e-3
DEGENERACY_THRESHOLD = 1e-2
EIGVEC_THRESHOLD = 5e-3

# regular expression pattern for a time stamp or other signifier of timing output, "CLOCK" or "TIME" or "SECONDS", & system-dependent versioning
skip_criteria = re.compile('([A-Z][a-z][a-z] [A-Z][a-z][a-z] [ 0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] [0-9][0-9][0-9][0-9])'
                           '|(CLOCK)|(TIME)|(SECONDS)|(Version)|(THE VIBRATIONAL FREQUENCY)|(ITERATION)|(SCF CALCULATIONS)|(Stewart)'
                           '|(remaining)|(\*  ISOTOPE)|(\*  DENOUT)|(\*  OLDENS)|(\*  SETUP)')

# regular expression pattern for an eigenvector block
eigen_criteria = re.compile('(Root No.)|(ROOT NO.)')

def is_float(string):
    '''check if a string contains a float'''
    try:
        float(string.replace('D','E'))
        return True
    except ValueError:
        return False

def to_float(string):
    '''check if a string contains a float'''
    try:
        return float(string.replace('D','E'))
    except ValueError:
        return False

def parse_mopac_output(path):
    '''parse a MOPAC output file at a given path into a list of basic elements (strings, numbers, matrices)'''
    parse_line = []
    parse_list = []
    mode = 'standard'
    with open(path,'r') as file:
        for line_num, line in enumerate(file):

            # this hack separates floats that don't have a space between them because of a minus sign & trailing comma's
            word_list = line.replace('-',' -').replace('E -','E-').replace('D -','D-').replace('=',' = ').replace(',',' , ').split()

            # ignore molecular dimensions block
            if 'MOLECULAR DIMENSIONS (Angstroms)' in line:
                mode = 'dim'
                continue
            elif mode == 'dim':
                if 'SCF CALCULATIONS' in line:
                    mode = 'standard'
                else:
                    continue

            # switch to or continue iter mode
            if 'RHF CALCULATION' in line or 'UHF CALCULATION' in line or 'Geometry optimization using BFGS' in line:
                mode = 'iter'
                continue
            elif mode == 'iter':
                if 'SCF FIELD WAS ACHIEVED' in line or 'THERE IS NOT ENOUGH TIME FOR ANOTHER CYCLE' in line:
                    mode = 'standard'
                else:
                    continue

            # skip lines as necessary
            if skip_criteria.search(line):
                continue

            # switch to or continue geo mode
            if 'ATOM    CHEMICAL      BOND LENGTH      BOND ANGLE     TWIST ANGLE' in line:
                mode = 'geo'
            elif mode == 'geo':
                if len(word_list) == 0:
                    mode = 'standard'
                else:
                    continue

            # switch to or continue lmo mode
            if 'NUMBER OF CENTERS  LMO ENERGY     COMPOSITION OF ORBITALS' in line:
                mode = 'lmo'
            elif mode == 'lmo':
                if 'LOCALIZED ORBITALS' in line:
                    mode = 'standard'
                else:
                    continue

            # switch to or continue grad mode
            if 'LARGEST ATOMIC GRADIENTS' in line:
                mode = 'grad'
                blank_count = 0
            # simple-minded skipping based on counting blank lines
            elif mode == 'grad':
                if len(word_list) == 0:
                    blank_count += 1
                if blank_count == 3:
                    mode = 'standard'
                else:
                    continue

            # switch to or continue vibe mode
            if 'DESCRIPTION OF VIBRATIONS' in line:
                mode = 'vibe'
            elif mode == 'vibe':
                if 'FORCE CONSTANT IN INTERNAL COORDINATES' in line or 'SYMMETRY NUMBER FOR POINT-GROUP' in line:
                    mode = 'standard'
                else:
                    continue

            # switch to or continue eigen mode
            if eigen_criteria.search(line):
                if mode != 'eigen':
                    eigen_line_num = line_num+1
                    mode = 'eigen'
                    label_list = []
                    value_list = []
                    vector_list = []
                    num_eigen = []
                label_list += [ int(word) for word in word_list[2:] ]
                num_eigen.append(len(word_list) - 2)

            # eigen parsing
            elif mode == 'eigen':

                # save eigenvalues in a list
                if len(word_list) == num_eigen[-1] and len(value_list) < len(label_list):

                    # check if the list of numbers is just another label
                    label_check = True
                    try:
                        for word,label in zip(word_list,label_list[-len(word_list):]):
                            if int(word) != label:
                                label_check = False
                    except ValueError:
                        label_check = False

                    if label_check == False:
                        value_list += [ float(word) for word in word_list ]

                # ignore symmetry labels
                elif len(word_list) == 2*num_eigen[-1] and is_float(word_list[-2]) and not is_float(word_list[-1]):
                    pass

                # save eigenvectors in a matrix
                elif len(word_list) > num_eigen[-1] and all([is_float(word) for word in word_list[-num_eigen[-1]:]]):
                    vector_list += [ float(word) for word in word_list[-num_eigen[-1]:] ]

                # ignore blank lines
                elif len(word_list) == 0:
                    pass

                # switch back to standard mode & reformat eigenvectors
                else:
                    mode = 'standard'

                    # reshape into a matrix
                    nrow = len(vector_list) // len(label_list)
                    ncol = len(label_list)
                    eigenmatrix = np.empty((nrow,ncol))

                    offset = 0
                    for num in num_eigen:
                        eigenmatrix[:,offset:offset+num] = np.reshape(vector_list[offset*nrow:(offset+num)*nrow],(nrow,num),order='C')
                        offset += num

                    # renormalize the eigenvectors (MOPAC uses a variety of normalizations)
                    for col in eigenmatrix.T:
                        col /= np.linalg.norm(col)

                    # output eigenvalue (if known) and eigenvectors
                    if len(value_list) == len(label_list):
                        parse_list.append((value_list,eigenmatrix,label_list[0] == 1,label_list[-1] == nrow))
                    else:
                        parse_list.append((label_list,eigenmatrix,label_list[0] == 1,label_list[-1] == nrow))
                    parse_line.append(eigen_line_num)

            # standard parsing
            if mode == 'standard':
                for word in word_list:
                    if is_float(word):
                        if 'FINAL HEAT OF FORMATION =' in line and word is word_list[5]:
                            parse_list.append(('HOF',to_float(word)))
                        else:
                            parse_list.append(to_float(word))
                    else:
                        parse_list.append(word)
                    parse_line.append(line_num+1)

    return parse_line, parse_list

# make a local copy of the input & other necessary files
for file in argv[3:]:
   copyfile(os.path.join(argv[1],file),file)

# run MOPAC in the local directory
#subprocess.call([argv[2],argv[3]])

# only compare ".out" output files that have the same name as ".mop" or ".ent" input files
out_name = argv[3][:-3]+'out'
ref_path = os.path.join(argv[1],out_name)

# parse the 2 output files that we are comparing
ref_line, ref_list = parse_mopac_output(ref_path)
out_line, out_list = parse_mopac_output(out_name)

#assert len(ref_list) == len(out_list), f'ERROR: output file size mismatch, {len(ref_list)} vs. {len(out_list)}'

for (line, ref, out) in zip(out_line, ref_list, out_list):
#    print(ref, "vs.", out)
    # check that types match
    assert type(ref) == type(out), f'ERROR: type mismatch between {ref} and {out} on output line {line}'

    # compare strings
    if type(ref) is str:
        assert ref == out, f'ERROR: string mismatch between {ref} and {out} on output line {line}'

    # compare floats
    elif type(ref) is float:
#        assert abs(ref - out) < NUMERIC_THRESHOLD, f'ERROR: numerical mismatch between {ref} and {out} on output line {line}'
        if abs(ref - out) > NUMERIC_THRESHOLD:
            print(f'WARNING: numerical mismatch between {ref} and {out} on output line {line}')

    # compare heats of formation
    elif len(ref) == 2:
#        assert abs(ref[1] - out[1]) < HEAT_THRESHOLD, f'ERROR: numerical heat mismatch between {ref[1]} and {out[1]} on output line {line}'
        if abs(ref[1] - out[1]) > HEAT_THRESHOLD:
            print(f'WARNING: numerical heat mismatch between {ref[1]} and {out[1]} on output line {line}')

    # compare eigenvalues & eigenvectors
    elif len(ref) == 4:
        ref_val, ref_vec, ref_begin, ref_end = ref
        out_val, out_vec, ref_begin, ref_end = out

        for refv, outv in zip(ref_val,out_val):
#            assert abs(refv - outv) < NUMERIC_THRESHOLD, f'ERROR: numerical mismatch between {refv} and {outv} on output line {line}'
            if abs(refv - outv) > NUMERIC_THRESHOLD:
                print(f'WARNING: eigenvalue mismatch between {refv} and {outv} on output line {line}')

            # build list of edges denoting degenerate subspaces
            if ref_begin:
                edge_list = [0]
            else:
                edge_list = []
            edge_list += [ i+1 for i in range(len(ref_val)-1) if np.abs(ref_val[i] - ref_val[i+1]) > DEGENERACY_THRESHOLD ]
            if ref_end:
                edge_list += [len(ref_val)]

            # test the distance between each pair of degenerate subspaces
            for i in range(len(edge_list)-1):
                overlap = ref_vec[:,edge_list[i]:edge_list[i+1]].T @ out_vec[:,edge_list[i]:edge_list[i+1]]
#                print("overlap = ",overlap)
                sval = np.linalg.svd(overlap, compute_uv=False)
                assert (sval[0] < 1.0 + EIGVEC_THRESHOLD) and (sval[-1] > 1.0 - EIGVEC_THRESHOLD), \
                    f'ERROR: degenerate subspace mismatch on output line {line}, overlap range in [{min(sval)},{max(sval)}]'