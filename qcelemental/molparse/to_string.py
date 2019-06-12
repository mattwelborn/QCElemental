import collections
from typing import Dict

import numpy as np

from ..physical_constants import constants


def to_string(molrec: Dict,
              dtype: str,
              units: str = None,
              *,
              atom_format: str = None,
              ghost_format: str = None,
              width: int = 17,
              prec: int = 12,
              return_data: bool = False):
    """Format a string representation of QM molecule.

    Parameters
    ----------
    molrec : dict
        Psi4 json Molecule spec.
    dtype : {'xyz', 'cfour', 'nwchem', 'molpro'}
        Overall string format. Note that it's possible to request variations
        that don't fit the dtype spec so may not be re-readable (e.g., ghost
        and mass in nucleus label with ``'xyz'``).
        'cfour' forces nucleus label, ignoring atom_format, ghost_format
    units : str, optional
        Units in which to write string. Usually ``Angstrom`` or ``Bohr``
        but may be any length unit.  There is not an option to write in
        intrinsic/input units. For ``dtype='xyz', units='Bohr'`` where the
        format doesn't have a slot to specify units, "au" is added so that
        readable as ``dtype='xyz+'``.
    atom_format : str, optional
        General format is ``'{elem}'``. A format string that may contain fields
        'elea' (-1 will be ''), 'elez', 'elem', 'mass', 'elbl' in any
        arrangement. For example, if a format naturally uses element symbol
        and you want atomic number instead with mass info, too, pass
        ``'{elez}@{mass}'``. See `ghost_format` for handling field 'real'.
    ghost_format : str, optional
        General format is ``'@{elem}'``. Like `atom_format`, but this formatter
        is used when `real=False`. To suppress ghost atoms, use `ghost_format=''`.
    width : int, optional
        Field width for formatting coordinate float.
    prec : int, optional
        Number of decimal places for formatting coordinate float.
    return data : bool, optional
        Whether to return dictionary with additional info from the molrec that's
        not expressible in the string but may be of interest to the QC program.
        Note that field names are in QCSchema, not molrec, language.

    Returns
    -------
    str
        String representation of the molecule.
    str, dict
        When ``return_data=True`, return additionally a dictionary
            keywords: key, value pairs for processing molecule info into options
            fields: aspects of ``qcelemental.models.Molecule`` expressed into
                    string _or_ keywords.

    """

    #funits, fiutau = process_units(molrec)
    #molrec = self.to_dict(force_units=units, np_out=True)

    dtype = dtype.lower()

    default_units = {
        "xyz": "Angstrom",
        "cfour": "Bohr",
        "gamess": "Bohr",
        "molpro": "Bohr",
        "nwchem": "Bohr",
        "psi4": "Bohr",
        "terachem": "Bohr"
    }
    if dtype not in default_units:
        raise KeyError(f"dtype '{dtype}' not understood.")

    # Handle units
    if units is None:
        units = default_units[dtype]

    if molrec['units'] == 'Angstrom' and units.capitalize() == 'Angstrom':
        factor = 1.
    elif molrec['units'] == 'Angstrom' and units.capitalize() == 'Bohr':
        if 'input_units_to_au' in molrec:
            factor = molrec['input_units_to_au']
        else:
            factor = 1. / constants.bohr2angstroms
    elif molrec['units'] == 'Bohr' and units.capitalize() == 'Angstrom':
        factor = constants.bohr2angstroms
    elif molrec['units'] == 'Bohr' and units.capitalize() == 'Bohr':
        factor = 1.
    else:
        factor = constants.conversion_factor(molrec['units'], units)
    geom = np.array(molrec['geom']).reshape((-1, 3)) * factor

    name = molrec.get('name', formula_generator(molrec['elem']))
    tagline = """auto-generated by QCElemental from molecule {}""".format(name)
    data = {
        'fields': ['atomic_numbers', 'geometry', 'symbols'],
        'keywords': {},
    }

    if dtype == 'xyz':
        # Notes
        # * if units not in umap (e.g., nm), can't be read back in by from_string()

        atom_format = '{elem}' if atom_format is None else atom_format
        ghost_format = '@{elem}' if ghost_format is None else ghost_format
        umap = {'bohr': 'au', 'angstrom': ''}

        atoms = _atoms_formatter(molrec, geom, atom_format, ghost_format, width, prec, 2)
        nat = len(atoms)

        first_line = """{} {}""".format(str(nat), umap.get(units.lower(), units.lower()))
        smol = [first_line.rstrip(), name]
        smol.extend(atoms)

    elif dtype == 'cfour':
        # Notes
        # * losing identity of ghost atoms. picked up again in basis formatting
        # * casting 'molecular_charge' to int
        # * no spaces at the beginning of 1st/comment line is important

        atom_format = '{elem}'
        ghost_format = 'GH'
        # TODO handle which units valid
        umap = {'bohr': 'bohr', 'angstrom': 'angstrom'}

        atoms = _atoms_formatter(molrec, geom, atom_format, ghost_format, width, prec, 2)

        smol = [tagline]
        smol.extend(atoms)

        data['fields'].extend(['molecular_charge', 'molecular_multiplicity'])
        data['keywords'] = {
            'charge': int(molrec['molecular_charge']),
            'multiplicity': molrec['molecular_multiplicity'],
            'units': umap.get(units.lower()),
            'coordinates': 'cartesian',
        }

    elif dtype == 'molpro':

        atom_format = '{elem}'
        ghost_format = '{elem}'
        umap = {'bohr': 'bohr', 'angstrom': 'angstrom'}

        atoms = _atoms_formatter(molrec, geom, atom_format, ghost_format, width, prec, 2)

        smol = []

        # Don't orient the molecule if asked to fix_com or fix_orientation
        if molrec['fix_orientation'] or molrec['fix_com']:
            smol.append('{orient,noorient}')

        # Have no symmetry if asked to fix_symmetry
        if 'fix_symmetry' in molrec.keys() and molrec['fix_symmetry'] == 'c1':
            smol.append('{symmetry,nosym}')
        elif 'fix_symmetry' not in molrec.keys():
            smol.append('{symmetry,auto}')

        smol.append('')

        units_line = f"""{{{umap.get(units.lower())}}}"""
        geom_line = """geometry={"""
        end_bracket = """}"""
        smol.append(units_line)
        smol.append(geom_line)
        smol.extend(atoms)
        smol.append(end_bracket)

        # Write ghost atom declarations in Molpro (using dummy card)
        if False in molrec['real']:
            ghost_line = 'dummy,' + ','.join([str(idx + 1) for idx, real in enumerate(molrec['real']) if not real])
            smol.append(ghost_line)

        smol.append(f"set,charge={molrec['molecular_charge']}")
        smol.append(f"set,multiplicity={molrec['molecular_multiplicity']}")

    elif dtype == 'nwchem':

        atom_format = '{elem}'
        ghost_format = 'GH'
        # TODO handle which units valid
        umap = {'bohr': 'bohr', 'angstrom': 'angstroms', 'nm': 'nanometers', 'pm': 'picometers'}

        atoms = _atoms_formatter(molrec, geom, atom_format, ghost_format, width, prec, 2)

        first_line = f"""geometry units {umap.get(units.lower())}"""
        # noautosym nocenter  # no reorienting input geometry
        fix_symm = molrec.get('fix_symmetry', None)
        symm_line = ''
        if fix_symm:
            symm_line = 'symmetry {}'.format(fix_symm)  # not quite what Jiyoung had
        last_line = """end"""
        smol = [first_line]
        smol.extend(atoms)
        smol.append(symm_line)
        smol.append(last_line)

    elif dtype == 'gamess':
        # Untested by gamess itself

        atom_format = ' {elem}{elbl} {elez}'
        ghost_format = ' {BQ} -{elez}'

        atoms = _atoms_formatter(molrec, geom, atom_format, ghost_format, width, prec, 2)

        first_line = """ $data"""
        second_line = f""" {tagline}"""
        third_line = """ C1"""
        last_line = """ $end"""

        smol = [first_line, second_line, third_line]
        smol.extend(atoms)
        smol.append(last_line)

    elif dtype == 'terachem':

        atom_format = '{elem}'
        ghost_format = 'X{elem}'
        umap = {'bohr': 'au', 'angstrom': ''}

        atoms = _atoms_formatter(molrec, geom, atom_format, ghost_format, width, prec, 2)

        first_line = f"""{len(atoms)} {umap[units.lower()]}"""
        smol = [first_line.rstrip(), name]
        smol.extend(atoms)

    elif dtype == 'psi4':

        atom_format = '{elem}'
        ghost_format = 'Gh({elem})'
        umap = {'bohr': 'bohr', 'angstrom': 'angstrom'}

        atoms = _atoms_formatter(molrec, geom, atom_format, ghost_format, width, prec, 2)

        smol = [f"""{int(molrec['molecular_charge'])} {molrec['molecular_multiplicity']}"""]
        split_atoms = np.split(atoms, molrec["fragment_separators"])
        for ifr, fr in enumerate(split_atoms):
            smol.extend(['--', f"""{int(molrec['fragment_charges'][ifr])} {molrec['fragment_multiplicities'][ifr]}"""])
            smol.extend(fr.tolist())

        # append units and any other non-default molecule keywords
        smol.append(f"units {umap[units.lower()]}")
        if molrec["fix_com"]:
            smol.append("no_com")
        if molrec["fix_orientation"]:
            smol.append("no_reorient")

        data['fields'].extend([
            'molecular_charge', 'molecular_multiplicity', 'fragments', 'fragment_charges', 'fragment_multiplicities',
            'fix_com', 'fix_orientation', 'real',
        ])
        data['keywords'] = {}

    else:
        raise KeyError(f"dtype '{dtype}' not understood.")

    smol = '\n'.join(smol) + '\n'
    if return_data:
        return smol, data
    else:
        return smol


def _atoms_formatter(molrec, geom, atom_format, ghost_format, width, prec, sp):
    """Format a list of strings, one per atom from `molrec`."""

    #geom = molrec['geom'].reshape((-1, 3))
    nat = geom.shape[0]
    fxyz = """{:>{width}.{prec}f}"""
    sp = """{:{sp}}""".format('', sp=sp)

    atoms = []
    for iat in range(nat):
        atom = []
        atominfo = {
            'elea': '' if molrec['elea'][iat] == -1 else molrec['elea'][iat],
            'elez': molrec['elez'][iat],
            'elem': molrec['elem'][iat],
            'mass': molrec['mass'][iat],
            'elbl': molrec['elbl'][iat]
        }

        if molrec['real'][iat]:
            nuc = """{:{width}}""".format(atom_format.format(**atominfo), width=width)
            atom.append(nuc)
        else:
            if ghost_format == '':
                continue
            else:
                nuc = """{:{width}}""".format(ghost_format.format(**atominfo), width=width)
                atom.append(nuc)

        atom.extend([fxyz.format(x, width=width, prec=prec) for x in geom[iat]])
        atoms.append(sp.join(atom))

    return atoms


def formula_generator(elem):
    """Return simple chemical formula from element list `elem`.

    >>> formula_generator(['C', 'Ca', 'O', 'O', 'Ag']
    AgCCaO2

    """
    counted = collections.Counter(elem)
    return ''.join((el if cnt == 1 else (el + str(cnt))) for el, cnt in sorted(counted.items()))
