"""C-PAC Configuration class and related functions

Copyright (C) 2022  C-PAC Developers

This file is part of C-PAC.

C-PAC is free software: you can redistribute it and/or modify it under
the terms of the GNU Lesser General Public License as published by the
Free Software Foundation, either version 3 of the License, or (at your
option) any later version.

C-PAC is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public
License for more details.

You should have received a copy of the GNU Lesser General Public
License along with C-PAC. If not, see <https://www.gnu.org/licenses/>."""
import os
import re
from typing import Optional, Tuple
from warnings import warn
import pkg_resources as p
import yaml
from CPAC.utils.utils import load_preconfig
from .diff import dct_diff

SPECIAL_REPLACEMENT_STRINGS = {r'${resolution_for_anat}',
                               r'${func_resolution}'}


class ConfigurationDictUpdateConflation(SyntaxError):
    """Custom exception to clarify similar methods"""
    def __init__(self):
        self.msg = (
            '`Configuration().update` requires a key and a value. '
            'Perhaps you meant `Configuration().dict().update`?')
        super().__init__()


class Configuration:
    """Class to set dictionary keys as map attributes.

    If the given dictionary includes the key `FROM`, that key's value
    will form the base of the Configuration object with the values in
    the given dictionary overriding matching keys in the base at any
    depth. If no `FROM` key is included, the base Configuration is
    the default Configuration.

    `FROM` accepts either the name of a preconfigured pipleine or a
    path to a YAML file.

    Given a Configuration `c`, and a list or tuple of an attribute name
    and nested keys `keys = ['attribute', 'key0', 'key1']` or
    `keys = ('attribute', 'key0', 'key1')`, the value 'value' nested in

    c.attribute = {'key0': {'key1': 'value'}}

    can be accessed (get and set) in any of the following ways (and
    more):

    c.attribute['key0']['key1']
    c['attribute']['key0']['key1']
    c['attribute', 'key0', 'key1']
    c[keys]

    Examples
    --------
    >>> c = Configuration({})
    >>> c['pipeline_setup', 'pipeline_name']
    'cpac-blank-template'
    >>> c = Configuration({'pipeline_setup': {
    ...     'pipeline_name': 'example_pipeline'}})
    >>> c['pipeline_setup', 'pipeline_name']
    'example_pipeline'
    >>> c['pipeline_setup', 'pipeline_name'] = 'new_pipeline2'
    >>> c['pipeline_setup', 'pipeline_name']
    'new_pipeline2'
    """
    def __init__(self, config_map=None):
        from click import BadParameter
        from CPAC.pipeline.schema import schema
        from CPAC.utils.utils import lookup_nested_value, update_nested_dict

        if config_map is None:
            config_map = {}

        base_config = config_map.pop('FROM', None)
        if base_config:
            if base_config.lower() in ['default', 'default_pipeline']:
                base_config = 'default'
            # import another config (specified with 'FROM' key)
            try:
                base_config = Preconfiguration(base_config)
            except BadParameter:
                base_config = configuration_from_file(base_config)
            config_map = update_nested_dict(base_config.dict(), config_map)
        else:
            # base everything on blank pipeline for unspecified keys
            with open(preconfig_yaml('blank'), 'r', encoding='utf-8') as _f:
                config_map = update_nested_dict(yaml.safe_load(_f), config_map)

        config_map = self._nonestr_to_None(config_map)

        try:
            regressors = lookup_nested_value(
                config_map,
                ['nuisance_corrections', '2-nuisance_regression', 'Regressors']
            )
        except KeyError:
            regressors = []
        if isinstance(regressors, list):
            for i, regressor in enumerate(regressors):
                # set Regressor 'Name's if not provided
                if 'Name' not in regressor:
                    regressor['Name'] = f'Regressor-{str(i + 1)}'
                # replace spaces with hyphens in Regressor 'Name's
                regressor['Name'] = regressor['Name'].replace(' ', '-')

        config_map = schema(config_map)

        # remove 'FROM' before setting attributes now that it's imported
        if 'FROM' in config_map:
            del config_map['FROM']

        # set FSLDIR to the environment $FSLDIR if the user sets it to
        # 'FSLDIR' in the pipeline config file
        _FSLDIR = config_map.get('FSLDIR')
        if _FSLDIR and bool(re.match(r'^[\$\{]{0,2}?FSLDIR[\}]?$', _FSLDIR)):
            config_map['FSLDIR'] = os.environ['FSLDIR']

        for key in config_map:
            # set attribute
            setattr(self, key, set_from_ENV(config_map[key]))

        self._update_attr()

    def __str__(self):
        return ('C-PAC Configuration '
                f"('{self['pipeline_setup', 'pipeline_name']}')")

    def __repr__(self):
        # show Configuration as a dict when accessed directly
        return str(self.dict())

    def __copy__(self):
        newone = type(self)({})
        newone.__dict__.update(self.__dict__)
        newone._update_attr()
        return newone

    def __getitem__(self, key):
        if isinstance(key, str):
            return getattr(self, key)
        elif isinstance(key, tuple) or isinstance(key, list):
            return self.get_nested(self, key)
        else:
            self.key_type_error(key)

    def __setitem__(self, key, value):
        if isinstance(key, str):
            setattr(self, key, value)
        elif isinstance(key, tuple) or isinstance(key, list):
            self.set_nested(self, key, value)
        else:
            self.key_type_error(key)

    def __sub__(self: 'Configuration', other: 'Configuration'):
        '''Return the set difference between two Configurations

        Examples
        --------
        >>> diff = (Preconfiguration('fmriprep-options')
        ...         - Preconfiguration('default'))
        >>> diff['pipeline_setup']['pipeline_name']
        ('cpac_fmriprep-options', 'cpac-default-pipeline')
        >>> diff['pipeline_setup']['pipeline_name'].s_value
        'cpac_fmriprep-options'
        >>> diff['pipeline_setup']['pipeline_name'].t_value
        'cpac-default-pipeline'
        >>> diff.s_value['pipeline_setup']['pipeline_name']
        'cpac_fmriprep-options'
        >>> diff.t_value['pipeline_setup']['pipeline_name']
        'cpac-default-pipeline'
        >>> diff['pipeline_setup']['pipeline_name'].left
        'cpac_fmriprep-options'
        >>> diff.left['pipeline_setup']['pipeline_name']
        'cpac_fmriprep-options'
        >>> diff['pipeline_setup']['pipeline_name'].minuend
        'cpac_fmriprep-options'
        >>> diff.minuend['pipeline_setup']['pipeline_name']
        'cpac_fmriprep-options'
        >>> diff['pipeline_setup']['pipeline_name'].right
        'cpac-default-pipeline'
        >>> diff.right['pipeline_setup']['pipeline_name']
        'cpac-default-pipeline'
        >>> diff['pipeline_setup']['pipeline_name'].subtrahend
        'cpac-default-pipeline'
        >>> diff.subtrahend['pipeline_setup']['pipeline_name']
        'cpac-default-pipeline'
        '''
        return(dct_diff(self.dict(), other.dict()))

    def dict(self):
        '''Show contents of a C-PAC configuration as a dict'''
        return {k: v for k, v in self.__dict__.items() if not callable(v)}

    def keys(self):
        '''Show toplevel keys of a C-PAC configuration dict'''
        return self.dict().keys()

    def _nonestr_to_None(self, d):
        '''Recursive method to type convert 'None' to None in nested
        config

        Parameters
        ----------
        d : any
            config item to check

        Returns
        -------
        d : any
            same item, same type, but with 'none' strings converted to
            Nonetypes
        '''
        if isinstance(d, str) and d.lower() == 'none':
            return None
        elif isinstance(d, list):
            return [self._nonestr_to_None(i) for i in d]
        elif isinstance(d, set):
            return {self._nonestr_to_None(i) for i in d}
        elif isinstance(d, dict):
            return {i: self._nonestr_to_None(d[i]) for i in d}
        else:
            return d

    def return_config_elements(self):
        # this returns a list of tuples
        # each tuple contains the name of the element in the yaml config file
        # and its value
        attributes = [
            (attr, getattr(self, attr))
            for attr in dir(self)
            if not callable(attr) and not attr.startswith("__")
        ]
        return attributes

    def sub_pattern(self, pattern, orig_key):
        return orig_key.replace(pattern, self[pattern[2:-1].split('.')])

    def check_pattern(self, orig_key, tags=None):
        if tags is None:
            tags = []
        if isinstance(orig_key, dict):
            return {k: self.check_pattern(orig_key[k], tags) for k in orig_key}
        if isinstance(orig_key, list):
            return [self.check_pattern(item) for item in orig_key]
        if not isinstance(orig_key, str):
            return orig_key
        template_pattern = r'\${.*}'
        r = re.finditer(template_pattern, orig_key)
        for i in r:
            pattern = i.group(0)
            if (
                isinstance(pattern, str) and len(pattern) and
                pattern not in tags
            ):
                try:
                    orig_key = self.sub_pattern(pattern, orig_key)
                except AttributeError as ae:
                    if pattern not in SPECIAL_REPLACEMENT_STRINGS:
                        warn(str(ae), category=SyntaxWarning)
        return orig_key

    # method to find any pattern ($) in the configuration
    # and update the attributes with its pattern value
    def _update_attr(self):

        def check_path(key):
            if isinstance(key, str) and '/' in key:
                if not os.path.exists(key):
                    warn(f"Invalid path- {key}. Please check your "
                         "configuration file")

        attributes = [(attr, getattr(self, attr)) for attr in dir(self)
                      if not callable(attr) and not attr.startswith("__")]

        template_list = ['template_brain_only_for_anat',
                         'template_skull_for_anat',
                         'ref_mask',
                         'template_brain_only_for_func',
                         'template_skull_for_func',
                         'template_symmetric_brain_only',
                         'template_symmetric_skull',
                         'dilated_symmetric_brain_mask']

        for attr_key, attr_value in attributes:

            if attr_key in template_list:
                new_key = self.check_pattern(attr_value, 'FSLDIR')
            else:
                new_key = self.check_pattern(attr_value)
            setattr(self, attr_key, new_key)

    def update(self, key, val=ConfigurationDictUpdateConflation):
        if isinstance(key, dict):
            raise ConfigurationDictUpdateConflation
        setattr(self, key, val)

    def get_nested(self, d, keys):
        if d is None:
            d = {}
        if isinstance(keys, str):
            return d[keys]
        if isinstance(keys, (list, tuple)):
            if len(keys) > 1:
                return self.get_nested(d[keys[0]], keys[1:])
            return d[keys[0]]
        return d

    def set_nested(self, d, keys, value):  # pylint: disable=invalid-name
        if isinstance(keys, str):
            d[keys] = value
        elif isinstance(keys, (list, tuple)):
            if len(keys) > 1:
                d[keys[0]] = self.set_nested(d[keys[0]], keys[1:], value)
            else:
                d[keys[0]] = value
        return d

    def key_type_error(self, key):
        raise KeyError(' '.join([
                'Configuration key must be a string, list, or tuple;',
                type(key).__name__,
                f'`{str(key)}`',
                'was given.'
            ]))


def check_pname(p_name: str, pipe_config: Configuration) -> str:
    '''Function to check / set `p_name`, the string representation of a
    pipeline for use in filetrees

    Parameters
    ----------
    p_name : str or None

    pipe_config : Configuration

    Returns
    -------
    p_name

    Examples
    --------
    >>> c = Configuration()
    >>> check_pname(None, c)
    'pipeline_cpac-blank-template'
    >>> check_pname('cpac-default-pipeline', c)
    'pipeline_cpac-default-pipeline'
    >>> check_pname('pipeline_cpac-default-pipeline', c)
    'pipeline_cpac-default-pipeline'
    >>> check_pname('different-name', Configuration())
    'pipeline_different-name'
    >>> p_name = check_pname(None, Preconfiguration('blank'))
    >>> p_name
    'pipeline_cpac-blank-template'
    >>> p_name = check_pname(None, Preconfiguration('default'))
    >>> p_name
    'pipeline_cpac-default-pipeline'
    '''
    if p_name is None:
        p_name = f'pipeline_{pipe_config["pipeline_setup", "pipeline_name"]}'
    elif not p_name.startswith('pipeline_'):
        p_name = f'pipeline_{p_name}'
    return p_name


def collect_key_list(config_dict):
    '''Function to return a list of lists of keys for a nested dictionary

    Parameters
    ----------
    config_dict : dict

    Returns
    -------
    key_list : list

    Examples
    --------
    >>> collect_key_list({'test': {'nested': 1, 'dict': 2}})
    [['test', 'nested'], ['test', 'dict']]
    '''
    key_list = []
    for key in config_dict:
        if isinstance(config_dict[key], dict):
            for inner_key_list in collect_key_list(config_dict[key]):
                key_list.append([key, *inner_key_list])
        else:
            key_list.append([key])
    return key_list


def configuration_from_file(config_file):
    """Function to load a Configuration from a pipeline config file.

    Parameters
    ----------
    config_file : str
        path to configuration file

    Returns
    -------
    Configuration
    """
    with open(config_file, 'r', encoding='utf-8') as config:
        return Configuration(yaml.safe_load(config))


def preconfig_yaml(preconfig_name='default', load=False):
    """Get the path to a preconfigured pipeline's YAML file

    Parameters
    ----------
    preconfig_name : str

    load : boolean
        return dict if True, str if False

    Returns
    -------
    str or dict
        path to YAML file or dict loaded from YAML
    """
    if load:
        with open(preconfig_yaml(preconfig_name), 'r', encoding='utf-8') as _f:
            return yaml.safe_load(_f)
    return p.resource_filename("CPAC", os.path.join(
        "resources", "configs", f"pipeline_config_{preconfig_name}.yml"))


class Preconfiguration(Configuration):
    """A preconfigured Configuration

    Parameters
    ----------
    preconfig : str
        The canonical name of the preconfig to load
    """
    def __init__(self, preconfig):
        super().__init__(config_map=preconfig_yaml(preconfig, True))


def set_from_ENV(conf):  # pylint: disable=invalid-name
    '''Function to replace strings like $VAR and ${VAR} with
    environment variable values

    Parameters
    ----------
    conf : any

    Returns
    -------
    conf : any

    Examples
    --------
    >>> import os
    >>> os.environ['SAMPLE_VALUE_SFE'] = '/example/path'
    >>> set_from_ENV({'key': {'nested_list': [
    ...     1, '1', '$SAMPLE_VALUE_SFE/extended']}})
    {'key': {'nested_list': [1, '1', '/example/path/extended']}}
    >>> set_from_ENV(['${SAMPLE_VALUE_SFE}', 'SAMPLE_VALUE_SFE'])
    ['/example/path', 'SAMPLE_VALUE_SFE']
    >>> del os.environ['SAMPLE_VALUE_SFE']
    '''
    if isinstance(conf, list):
        return [set_from_ENV(item) for item in conf]
    if isinstance(conf, dict):
        return {key: set_from_ENV(conf[key]) for key in conf}
    if isinstance(conf, str):
        # set any specified environment variables
        # (only matching all-caps plus `-` and `_`)
        # like `${VAR}`
        _pattern1 = r'\${[A-Z\-_]*}'
        # like `$VAR`
        _pattern2 = r'\$[A-Z\-_]*(?=/|$)'
        # replace with environment variables if they exist
        for _pattern in [_pattern1, _pattern2]:
            _match = re.search(_pattern, conf)
            if _match:
                _match = _match.group().lstrip('${').rstrip('}')
                conf = re.sub(
                    _pattern, os.environ.get(_match, f'${_match}'), conf)
    return conf


def set_subject(sub_dict: dict, pipe_config: 'Configuration',
                p_name: Optional[str] = None) -> Tuple[str, str, str]:
    '''Function to set pipeline name and log directory path for a given
    sub_dict

    Parameters
    ----------
    sub_dict : dict

    pipe_config : CPAC.utils.configuration.Configuration

    p_name : str, optional
        pipeline name string

    Returns
    -------
    subject_id : str

    p_name : str
        pipeline name string

    log_dir : str
        path to subject log directory

    Examples
    --------
    >>> from tempfile import TemporaryDirectory
    >>> from CPAC.utils.configuration import Configuration
    >>> sub_dict = {'site_id': 'site1', 'subject_id': 'sub1',
    ...             'unique_id': 'uid1'}
    >>> with TemporaryDirectory() as tmpdir:
    ...     subject_id, p_name, log_dir = set_subject(
    ...         sub_dict, Configuration({'pipeline_setup': {'log_directory':
    ...             {'path': tmpdir}}}))
    >>> subject_id
    'sub1_uid1'
    >>> p_name
    'pipeline_cpac-blank-template'
    >>> log_dir.endswith(f'{p_name}/{subject_id}')
    True
    '''
    subject_id = sub_dict['subject_id']
    if sub_dict.get('unique_id'):
        subject_id += f'_{sub_dict["unique_id"]}'
    p_name = check_pname(p_name, pipe_config)
    log_dir = os.path.join(pipe_config.pipeline_setup['log_directory']['path'],
                           p_name, subject_id)
    if not os.path.exists(log_dir):
        os.makedirs(os.path.join(log_dir))
    return subject_id, p_name, log_dir
