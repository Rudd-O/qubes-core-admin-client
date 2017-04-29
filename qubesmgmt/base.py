# -*- encoding: utf8 -*-
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2017 Marek Marczykowski-Górecki
#                               <marmarek@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License along
# with this program; if not, see <http://www.gnu.org/licenses/>.

'''Base classes for managed objects'''

import ast
import qubesmgmt.exc

DEFAULT = object()


class PropertyHolder(object):
    '''A base class for object having properties retrievable using mgmt API.

    Warning: each (non-private) local attribute needs to be defined at class
    level, even if initialized in __init__; otherwise will be treated as
    property retrievable using mgmt call.
    '''
    #: a place for appropriate Qubes() object (QubesLocal or QubesRemote),
    # use None for self
    app = None

    def __init__(self, app, method_prefix, method_dest):
        #: appropriate Qubes() object (QubesLocal or QubesRemote), use None
        # for self
        self.app = app
        self._method_prefix = method_prefix
        self._method_dest = method_dest
        self._properties = None
        self._properties_help = None

    def qubesd_call(self, dest, method, arg=None, payload=None):
        '''
        Call into qubesd using appropriate mechanism. This method should be
        defined by a subclass.

        :param dest: Destination VM name
        :param method: Full API method name ('mgmt...')
        :param arg: Method argument (if any)
        :param payload: Payload send to the method
        :return: Data returned by qubesd (string)
        '''
        if dest is None:
            dest = self._method_dest
        # have the actual implementation at Qubes() instance
        if self.app:
            return self.app.qubesd_call(dest, method, arg, payload)
        raise NotImplementedError

    @staticmethod
    def _parse_qubesd_response(response_data):
        '''Parse response from qubesd.

        In case of success, return actual data. In case of error,
        raise appropriate exception.
        '''

        if response_data == b'':
            raise qubesmgmt.exc.QubesDaemonNoResponseError(
                'Got empty response from qubesd')

        if response_data[0:2] == b'\x30\x00':
            return response_data[2:]
        elif response_data[0:2] == b'\x32\x00':
            (_, exc_type, _traceback, format_string, args) = \
                response_data.split(b'\x00', 4)
            # drop last field because of terminating '\x00'
            args = [arg.decode() for arg in args.split(b'\x00')[:-1]]
            format_string = format_string.decode('utf-8')
            exc_type = exc_type.decode('ascii')
            try:
                exc_class = getattr(qubesmgmt.exc, exc_type)
            except AttributeError:
                if exc_type.endswith('Error'):
                    exc_class = __builtins__.get(exc_type,
                        qubesmgmt.exc.QubesException)
                else:
                    exc_class = qubesmgmt.exc.QubesException
            # TODO: handle traceback if given
            raise exc_class(format_string, *args)
        else:
            raise qubesmgmt.exc.QubesDaemonCommunicationError(
                'Invalid response format')

    def property_list(self):
        '''
        List available properties (their names).

        :return: list of strings
        '''
        if self._properties is None:
            properties_str = self.qubesd_call(
                self._method_dest,
                self._method_prefix + 'List',
                None,
                None)
            self._properties = properties_str.decode('ascii').splitlines()
        # TODO: make it somehow immutable
        return self._properties

    def property_help(self, name):
        '''
        Get description of a property.

        :return: property help text
        '''
        help_text = self.qubesd_call(
            self._method_dest,
            self._method_prefix + 'Help',
            name,
            None)
        return help_text.decode('ascii')

    def property_is_default(self, item):
        '''
        Check if given property have default value

        :param str item: name of property
        :return: bool
        '''
        if item.startswith('_'):
            raise AttributeError(item)
        property_str = self.qubesd_call(
            self._method_dest,
            self._method_prefix + 'Get',
            item,
            None)
        (default, _value) = property_str.split(b' ', 1)
        assert default.startswith(b'default=')
        is_default_str = default.split(b'=')[1]
        is_default = ast.literal_eval(is_default_str.decode('ascii'))
        assert isinstance(is_default, bool)
        return is_default

    def __getattr__(self, item):
        # pylint: disable=too-many-return-statements
        if item.startswith('_'):
            raise AttributeError(item)
        try:
            property_str = self.qubesd_call(
                self._method_dest,
                self._method_prefix + 'Get',
                item,
                None)
        except qubesmgmt.exc.QubesDaemonNoResponseError:
            raise qubesmgmt.exc.QubesPropertyAccessError(item)
        (_default, prop_type, value) = property_str.split(b' ', 2)
        prop_type = prop_type.decode('ascii')
        if not prop_type.startswith('type='):
            raise qubesmgmt.exc.QubesDaemonCommunicationError(
                'Invalid type prefix received: {}'.format(prop_type))
        (_, prop_type) = prop_type.split('=', 1)
        value = value.decode()
        if prop_type == 'str':
            return str(value)
        elif prop_type == 'bool':
            if value == '':
                raise AttributeError
            return ast.literal_eval(value)
        elif prop_type == 'int':
            if value == '':
                raise AttributeError
            return ast.literal_eval(value)
        elif prop_type == 'vm':
            if value == '':
                return None
            return self.app.domains[value]
        elif prop_type == 'label':
            if value == '':
                return None
            # TODO
            return self.app.labels[value]
        else:
            raise qubesmgmt.exc.QubesDaemonCommunicationError(
                'Received invalid value type: {}'.format(prop_type))

    def __setattr__(self, key, value):
        if key.startswith('_') or key in dir(self):
            return super(PropertyHolder, self).__setattr__(key, value)
        if value is qubesmgmt.DEFAULT:
            try:
                self.qubesd_call(
                    self._method_dest,
                    self._method_prefix + 'Reset',
                    key,
                    None)
            except qubesmgmt.exc.QubesDaemonNoResponseError:
                raise qubesmgmt.exc.QubesPropertyAccessError(key)
        else:
            if isinstance(value, qubesmgmt.vm.QubesVM):
                value = value.name
            try:
                self.qubesd_call(
                    self._method_dest,
                    self._method_prefix + 'Set',
                    key,
                    str(value).encode('utf-8'))
            except qubesmgmt.exc.QubesDaemonNoResponseError:
                raise qubesmgmt.exc.QubesPropertyAccessError(key)

    def __delattr__(self, name):
        if name.startswith('_') or name in dir(self):
            return super(PropertyHolder, self).__delattr__(name)
        try:
            self.qubesd_call(
                self._method_dest,
                self._method_prefix + 'Reset',
                name
            )
        except qubesmgmt.exc.QubesDaemonNoResponseError:
            raise qubesmgmt.exc.QubesPropertyAccessError(name)


class WrapperObjectsCollection(object):
    '''Collection of simple named objects'''
    def __init__(self, app, list_method, object_class):
        '''
        Construct manager of named wrapper objects.

        :param app: Qubes() object
        :param list_method: name of API method used to list objects,
            must return simple "one name per line" list
        :param object_class: object class (callable) for wrapper objects,
            will be called with just two arguments: app and a name
        '''
        self.app = app
        self._list_method = list_method
        self._object_class = object_class
        #: names cache
        self._names_list = None
        #: returned objects cache
        self._objects = {}

    def clear_cache(self):
        '''Clear cached list of names'''
        self._names_list = None

    def refresh_cache(self, force=False):
        '''Refresh cached list of names'''
        if not force and self._names_list is not None:
            return
        list_data = self.app.qubesd_call('dom0', self._list_method)
        list_data = list_data.decode('ascii')
        assert list_data[-1] == '\n'
        self._names_list = [str(name) for name in list_data[:-1].splitlines()]

        for name, obj in list(self._objects.items()):
            if obj.name not in self._names_list:
                # Object no longer exists
                del self._objects[name]

    def __getitem__(self, item):
        if item not in self:
            raise KeyError(item)
        if item not in self._objects:
            self._objects[item] = self._object_class(self.app, item)
        return self._objects[item]

    def __contains__(self, item):
        self.refresh_cache()
        return item in self._names_list

    def __iter__(self):
        self.refresh_cache()
        for obj in self._names_list:
            yield self[obj]

    def keys(self):
        '''Get list of names.'''
        self.refresh_cache()
        return self._names_list.copy()
