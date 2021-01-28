import re
import os
import warnings
import yaml
from itertools import repeat
from warnings import warn

# Find default config
# in-container location
DEFAULT_PIPELINE_FILE = '/cpac_resources/default_pipeline.yml'
if not os.path.exists(DEFAULT_PIPELINE_FILE):
    CPAC_DIRECTORY = os.path.abspath(os.path.join(
        __file__,
        *repeat(os.path.pardir, 3)))
    # package location
    DEFAULT_PIPELINE_FILE = os.path.join(
        CPAC_DIRECTORY,
        'CPAC/resources/configs/default_pipeline.yml')
    # source code (developer) location
    if not os.path.exists(DEFAULT_PIPELINE_FILE):
        DEFAULT_PIPELINE_FILE = os.path.join(
            CPAC_DIRECTORY,
            'dev/docker_data/default_pipeline.yml')
    del CPAC_DIRECTORY

with open(DEFAULT_PIPELINE_FILE, 'r') as dp_fp:
    default_config = yaml.safe_load(dp_fp)


class Configuration(object):
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
    'cpac-default-pipeline'
    >>> c = Configuration({'pipeline_setup': {
    ...     'pipeline_name': 'example_pipeline'}})
    >>> c['pipeline_setup', 'pipeline_name']
    'example_pipeline'
    >>> c['pipeline_setup', 'pipeline_name'] = 'new_pipeline2'
    >>> c['pipeline_setup', 'pipeline_name']
    'new_pipeline2'
    """
    def __init__(self, config_map=None):
        from CPAC.pipeline.schema import schema
        from CPAC.utils.utils import load_preconfig, update_nested_dict
        from optparse import OptionError

        if config_map is None:
            config_map = {}

        base_config = config_map.get('FROM', 'default_pipeline')
        # base on default pipeline if FROM not specified or specified 'default'
        if base_config in ['default', 'default_pipeline']:
            config_map = update_nested_dict(default_config, config_map)
        # base on another config (specified with 'FROM' key)
        else:
            try:
                base_config = load_preconfig(base_config)
            except OptionError:
                base_config = base_config
            from_config = yaml.safe_load(open(base_config, 'r'))
            config_map = update_nested_dict(from_config, config_map)
        config_map = schema(self.nonestr_to_None(config_map))

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
        self.__update_attr()

    def __str__(self):
        return 'C-PAC Configuration'

    def __repr__(self):
        # show Configuration as a dict when accessed directly
        return self.__str__()

    def __copy__(self):
        newone = type(self)({})
        newone.__dict__.update(self.__dict__)
        newone.__update_attr()
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

    def dict(self):
        '''Show contents of a C-PAC configuration as a dict
        '''
        return {k: self[k] for k in self.__dict__ if not callable(
            self.__dict__[k])}

    def nonestr_to_None(self, d):
        """
        recursive method to type convert 'None' to None in nested
        config

        Parameters
        ----------
        d: any
            config item to check

        Returns
        -------
        d: any
            same item, same type, but with 'none' strings converted to
            Nonetypes
        """
        if isinstance(d, str) and d.lower() == 'none':
            return None
        elif isinstance(d, list):
            return [self.nonestr_to_None(i) for i in d]
        elif isinstance(d, set):
            return {self.nonestr_to_None(i) for i in d}
        elif isinstance(d, dict):
            return {i: self.nonestr_to_None(d[i]) for i in d}
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

    def check_pattern(self, orig_key, tags=[]):
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
                    warn(str(ae), category=SyntaxWarning)
        return orig_key

    # method to find any pattern ($) in the configuration
    # and update the attributes with its pattern value
    def update_attr(self):

        def check_path(key):
            if type(key) is str and '/' in key:
                if not os.path.exists(key):
                    warnings.warn(
                        "Invalid path- %s. Please check your configuration "
                        "file" % key)

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

    __update_attr = update_attr

    def update(self, key, val):
        setattr(self, key, val)

    def get_nested(self, d, keys):
        if isinstance(keys, str):
            return d[keys]
        elif isinstance(keys, tuple) or isinstance(keys, list):
            if len(keys) > 1:
                return self.get_nested(d[keys[0]], keys[1:])
            else:
                return d[keys[0]]

    def set_nested(self, d, keys, value):
        if isinstance(keys, str):
            d[keys] = value
        elif isinstance(keys, tuple) or isinstance(keys, list):
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


def set_from_ENV(conf):
        '''Function to replace strings like $VAR and ${VAR} with
        environment variable values
        
        Parameters
        ----------
        conf: any
        
        Returns
        -------
        conf: any
        
        Examples
        --------
        >>> import os
        >>> os.environ['SAMPLE_VALUE_SFE'] = '/example/path'
        >>> set_from_ENV({'key': {'nested_list': [1, '1', '$SAMPLE_VALUE_SFE/extended']}})
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