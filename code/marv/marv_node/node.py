# -*- coding: utf-8 -*-
#
# Copyright 2016 - 2018  Ternaris.
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import absolute_import, division, print_function

import functools
import hashlib
import os
from base64 import b32encode
from collections import OrderedDict, namedtuple
from inspect import isgeneratorfunction
from itertools import count, product

from .io import fork, get_logger, get_stream, pull
from .mixins import Keyed


class InputNameCollision(Exception):
    pass


def input(name, default=None, foreach=None):
    """Decorator to declare input for a node.

    Plain inputs, that is plain python objects, are directly passed to
    the node. Whereas streams generated by other nodes are requested
    and once the handles of all input streams are available the node
    is instantiated.

    Args:
        name (str): Name of the node function argument the input will
            be passed to.
        default: An optional default value for the input. This can be
            any python object or another node.
        foreach (bool): This parameter is currently not supported and
            only for internal usage.

    Returns:
        The original function decorated with this input
        specification. A function is turned into a node by the
        :func:`node` decorator.

    """
    assert default is None or foreach is None
    value = foreach if foreach is not None else default
    value = StreamSpec(value) if isinstance(value, Node) else value
    foreach = foreach is not None
    spec = InputSpec(name, value, foreach)
    def deco(func):
        """Add {!r} to function.""".format(spec)
        specs = func.__dict__.setdefault('__marv_input_specs__', OrderedDict())
        if spec.name in specs:
            raise InputNameCollision(spec.name)
        specs[spec.name] = spec
        return func
    return deco


def node(schema=None, header=None, group=None, version=None):
    """Turn function into node.

    Args:
        schema: capnproto schema describing the output messages format
        header: This parameter is currently not supported and only for
            internal usage.
        group (bool): A boolean indicating whether the default stream
            of the node is a group, meaning it will be used to
            published handles for streams or further groups. In case
            of :paramref:`marv.input.foreach` specifications this flag will
            default to `True`. This parameter is currently only for
            internal usage.
        version (int): This parameter currently has no effect.

    Returns:
        A :class:`Node` instance according to the given
        arguments and :func:`input` decorators.
    """
    def deco(func):
        """Turn function into node with given arguments.

        :func:`node`(schema={!r}, header={!r}, group={!r})
        """.format(schema, header, group)

        if isinstance(func, Node):
            raise TypeError('Attempted to convert function into node twice.')
        assert isgeneratorfunction(func), \
            "Node {}:{} needs to be a generator function".format(func.__module__,
                                                                 func.__name__)

        specs = getattr(func, '__marv_input_specs__', None)
        if hasattr(func, '__marv_input_specs__'):
            del func.__marv_input_specs__

        node = Node(func, schema=schema, header_schema=header,
                    group=group, specs=specs, version=version)
        functools.update_wrapper(node, func)
        return node
    return deco


_InputSpec = namedtuple('_InputSpec', ('name', 'value', 'foreach'))
class InputSpec(Keyed, _InputSpec):
    @property
    def key(self):
        value = self.value.key if hasattr(self.value, 'key') else self.value
        return (self.name, value, self.foreach)

    def clone(self, value):
        cls = type(self)
        value = StreamSpec(value) if isinstance(value, Node) else value
        return cls(self.name, value, self.foreach)

    def __repr__(self):
        foreach = 'foreach ' if self.foreach else ''
        return ('<{} {}{}={!r}>'.format(type(self), foreach, self.name, self.value))


class StreamSpec(object):
    def __init__(self, node, name=None):
        assert isinstance(node, Node)
        self.node = node
        self.name = name
        self.key = (node.key,) if name is None else (node.key, name)
        self.args = (node,) if name is None else (node, name)


class Node(Keyed):
    _key = None
    group = None

    @property
    def key(self):
        key = self._key
        if key:
            return key
        return '-'.join([self.specs_hash, self.fullname])

    @property
    def abbrev(self):
        return '.'.join([self.name, self.specs_hash[:10]])

    @staticmethod
    def genhash(specs):
        spec_keys = tuple(x.key for x in sorted(specs.values()))
        return b32encode(hashlib.sha256(repr(spec_keys)).digest()).lower()[:-4]

    def __init__(self, func, schema=None, header_schema=None, version=None,
                 name=None, namespace=None, specs=None, group=None):
        # TODO: assert no default values on func, or consider default
        # values (instead of) marv.input() declarations
        # Definitely not for node inputs as they are not passed directly!
        self.func = func
        self.version = version
        self.name = name = func.__name__ if name is None else name
        self.namespace = namespace = func.__module__ if namespace is None else namespace
        self.fullname = (name if not namespace else ':'.join([namespace, name]))
        self.specs_hash = self.genhash(specs or {})
        self.header_schema = header_schema
        self.schema = schema
        self.specs = specs or {}
        assert group in (None, False, True, 'ondemand'), group
        self.group = group if group is not None else \
                     any(x.foreach for x in self.specs.values())
        # TODO: StreamSpec, seriously?
        self.deps = {x.value.node for x in self.specs.values()
                     if isinstance(x.value, StreamSpec)}
        self.alldeps = self.deps.copy()
        self.alldeps.update(x for dep in self.deps
                            for x in dep.alldeps)

        self.consumers = set()
        for dep in self.deps:
            assert self not in dep.consumers, (dep, self)
            dep.consumers.add(self)

        self.dependent = set()
        for dep in self.alldeps:
            assert self not in dep.dependent, (dep, self)
            dep.dependent.add(self)

    def __call__(self, **inputs):
        return self.func(**inputs)

    def invoke(self, inputs=None):
        # We must not write any instance variables, a node is running
        # multiple times in parallel.

        common = []
        foreach_plain = []
        foreach_stream = []
        if inputs is None:
            for spec in self.specs.values():
                assert not isinstance(spec.value, Node), (self, spec.value)
                if isinstance(spec.value, StreamSpec):
                    value = yield get_stream(*spec.value.args)
                    target = foreach_stream if spec.foreach else common
                else:
                    value = spec.value
                    target = foreach_plain if spec.foreach else common
                target.append((spec.name, value))

        if foreach_plain or foreach_stream:
            log = yield get_logger()
            cross = product(*[[(k, x) for x in v] for k, v in foreach_plain])
            if foreach_stream:
                assert len(foreach_stream) == 1, self  # FOR NOW
                cross = list(cross)
                name, stream = foreach_stream[0]
                idx = count()
                while True:
                    value = yield pull(stream)
                    if value is None:
                        log.noisy('finished forking')
                        break
                    for inputs in cross:
                        inputs = dict(inputs)
                        inputs.update(common)
                        inputs[name] = value
                        # TODO: give fork a name
                        i = idx.next()
                        log.noisy('FORK %d with: %r', i, inputs)
                        yield fork('{}'.format(i), inputs, False)
            else:
                for i, inputs in enumerate(cross):
                    # TODO: consider deepcopy
                    inputs = dict(inputs)
                    inputs.update(common)
                    log.noisy('FORK %d with: %r', i, inputs)
                    yield fork('{}'.format(i), inputs, False)
                log.noisy('finished forking')
        else:
            if inputs is None:
                inputs = dict(common)
            while True:
                gen = self.func(**inputs)
                assert hasattr(gen, 'send')
                send = None
                while True:
                    send = yield gen.send(send)

    def clone(self, **kw):
        specs = {spec.name: (spec if spec.name not in kw else
                             spec.clone(kw.pop(spec.name)))
                 for spec in self.specs.values()}
        assert not kw, (kw, self.specs)
        cls = type(self)
        clone = cls(func=self.func, header_schema=self.header_schema,
                    schema=self.schema, specs=specs)
        return clone

    def __getitem__(self, key):
        from .tools import select
        import warnings
        warnings.warn('Use ``marv.select(node, name)`` instead of ``node[name]``',
                      DeprecationWarning, stacklevel=2)
        return select(self, key)

    def __str__(self):
        return self.key

    def __repr__(self):
        return '<Node {}>'.format(self.abbrev)
