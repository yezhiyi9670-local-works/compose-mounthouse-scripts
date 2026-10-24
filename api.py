from contextlib import contextmanager
from contextvars import ContextVar
import os
import re
from types import MappingProxyType
from typing import (
    Callable,
    Generator,
    Literal,
    Optional,
    TypedDict,
    Union,
    cast,
)
from typing_extensions import NotRequired, Unpack


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

HouseMapper = Callable[[str, str], str]


class MountOptions(TypedDict):
    """Partial options accepted from callers (merged over defaults)."""
    read_only: NotRequired[bool]
    must_exist: NotRequired[bool]
    create: NotRequired[bool]
    tmpfs_size: NotRequired[Optional[Union[int, str]]]
    house_mapper: NotRequired[Optional[HouseMapper]]


class ResolvedMountOptions(TypedDict):
    """Fully-populated options after merging with defaults."""
    read_only: bool
    must_exist: bool
    create: bool
    tmpfs_size: Optional[Union[int, str]]
    house_mapper: Optional[HouseMapper]


class ResolvedConfig(TypedDict):
    service: Optional[str]
    src: Optional[str]
    dst: Optional[str]
    options: ResolvedMountOptions


# ---------------------------------------------------------------------------
# Keyword-argument bundles
# ---------------------------------------------------------------------------

class ConfigKwargs(TypedDict, total=False):
    """Keyword arguments for ``config()`` / ``bind()``."""
    service: Optional[str]
    src: Optional[str]
    dst: Optional[str]
    both: Optional[str]
    options: MountOptions


class TmpfsKwargs(TypedDict, total=False):
    """Keyword arguments for ``tmpfs()`` (no src/both)."""
    service: Optional[str]
    dst: Optional[str]
    options: MountOptions


class SrcCheckKwargs(TypedDict, total=False):
    """Keyword arguments for ``src_exists()`` (only src)."""
    src: Optional[str]

# ---------------------------------------------------------------------------
# Compose output shape
# ---------------------------------------------------------------------------

class BindMountOptions(TypedDict):
    create_host_path: bool


class TmpfsMountOptions(TypedDict):
    size: NotRequired[Union[int, str]]


class BindMountSpec(TypedDict):
    type: Literal['bind']
    source: str
    target: str
    read_only: bool
    bind: BindMountOptions


class TmpfsMountSpec(TypedDict):
    type: Literal['tmpfs']
    target: str
    read_only: bool
    tmpfs: TmpfsMountOptions


MountSpec = Union[BindMountSpec, TmpfsMountSpec]


class ServiceMounts(TypedDict):
    volumes: list[MountSpec]


class ComposeData(TypedDict):
    services: dict[str, ServiceMounts]


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_EMPTY_OPTIONS: MountOptions = {}

_DEFAULT_RESOLVED_OPTIONS: ResolvedMountOptions = {
    'read_only': False,
    'must_exist': True,
    'create': False,
    'tmpfs_size': None,
    'house_mapper': None,
}

_DEFAULT_CONFIG: ResolvedConfig = {
    'service': None,
    'src': None,
    'dst': None,
    'options': _DEFAULT_RESOLVED_OPTIONS,
}


class API:
    def __init__(self) -> None:
        self.__services: dict[str, ServiceMounts] = {}
        self.__c_config: ContextVar[ResolvedConfig] = ContextVar(
            'API#__config',
            default=cast(
                ResolvedConfig,
                MappingProxyType({
                    **_DEFAULT_CONFIG,
                    'options': MappingProxyType(_DEFAULT_RESOLVED_OPTIONS),
                }),
            ),
        )

    @staticmethod
    def __merge_config(old: ResolvedConfig, new: ConfigKwargs) -> ResolvedConfig:
        def coalesce(a: Optional[str], b: Optional[str]) -> Optional[str]:
            return a if a is not None else b

        def join_path(base: Optional[str], rel: Optional[str], force_rel_syntax: bool) -> Optional[str]:
            if rel is None or rel == '':
                return base
            if base is None:
                return rel
            if force_rel_syntax and (rel == '!.' or rel.startswith('!./')):
                # Force relative-to-mounts.yml path.
                return rel[1:]
            if rel.startswith('/') or rel.startswith('\\') or re.match(r'^[A-Za-z]:(\\|/|$)', rel):
                return rel
            if not (base.endswith('/') or base.endswith('\\')):
                return base + '/' + rel
            return base + rel

        def merge_options(
            old: ResolvedMountOptions,
            new: MountOptions,
        ) -> ResolvedMountOptions:
            for key in new:
                if key not in old:
                    raise KeyError(f'Key "{key}" is not allowed in options.')
            return cast(
                ResolvedMountOptions,
                MappingProxyType({**old, **new}),
            )

        return cast(
            ResolvedConfig,
            MappingProxyType({
                'service': coalesce(old['service'], new.get('service')),
                'src': join_path(old['src'], new.get('src'), force_rel_syntax=True),
                'dst': join_path(old['dst'], new.get('dst'), force_rel_syntax=True),
                'options': merge_options(
                    old['options'],
                    new.get('options', _EMPTY_OPTIONS),
                ),
            }),
        )

    @contextmanager
    def config(self, /, **kwargs: Unpack[ConfigKwargs]) -> Generator[None]:
        src = kwargs.get('src')
        dst = kwargs.get('dst')
        both = kwargs.get('both')
        if both is not None:
            if src is not None or dst is not None:
                raise ValueError(
                    'When specifying "both", "src" and "dst" must both be None.'
                )
            src = both
            dst = both

        delta: ConfigKwargs = {
            'service': kwargs.get('service'),
            'src': src,
            'dst': dst,
            'options': kwargs.get('options', _EMPTY_OPTIONS),
        }

        token = self.__c_config.set(
            self.__merge_config(self.__c_config.get(), delta)
        )
        try:
            yield
        finally:
            self.__c_config.reset(token)

    @contextmanager
    def rw(self) -> Generator[None]:
        with self.config(options={'read_only': False}):
            yield

    @contextmanager
    def ro(self) -> Generator[None]:
        with self.config(options={'read_only': True}):
            yield

    def get_src_path(self, /, **kwargs: Unpack[SrcCheckKwargs]):
        with self.config(**kwargs):
            return self.__get_src_path()
    def __get_src_path(self):
        config = self.__c_config.get()
        src_path = config['src']
        return src_path
    def src_exists(self, /, **kwargs: Unpack[SrcCheckKwargs]) -> bool:
        with self.config(**kwargs):
            return self.__src_exists()
    def __src_exists(self) -> bool:
        def host_path_exists(path: str) -> bool:
            return os.path.exists(path)
        src_path = self.__get_src_path()
        if src_path is None:
            raise ValueError(
                'Cannot check existence of unspecified source path.'
            )
        return host_path_exists(src_path)

    def __get_service_mount_list(self, service: str) -> list[MountSpec]:
        if service not in self.__services:
            self.__services[service] = {'volumes': []}
        return self.__services[service]['volumes']

    def bind(self, /, **kwargs: Unpack[ConfigKwargs]) -> None:
        with self.config(**kwargs):
            self.__add('bind')
    def rw_bind(self, /, **kwargs: Unpack[ConfigKwargs]) -> None:
        with self.config(**kwargs):
            with self.rw(): self.bind()
    def rw_bind_ro_git(self, /, **kwargs: Unpack[ConfigKwargs]) -> None:
        with self.config(**kwargs):
            with self.rw():
                self.bind()
            with self.config(both='.git', options={ 'must_exist': True, 'read_only': True }):
                self.bind()
            with self.config(both='.git/lfs/tmp', options={ 'must_exist': True, 'read_only': False }):
                if self.src_exists(): self.bind()
    def ro_bind(self, /, **kwargs: Unpack[ConfigKwargs]) -> None:
        with self.config(**kwargs):
            with self.ro(): self.bind()

    def tmpfs(self, /, **kwargs: Unpack[TmpfsKwargs]) -> None:
        with self.config(**kwargs):
            self.__add('tmpfs')
    def rw_tmpfs(self, /, **kwargs: Unpack[TmpfsKwargs]) -> None:
        with self.config(**kwargs):
            with self.rw(): self.tmpfs()
    def ro_tmpfs(self, /, **kwargs: Unpack[TmpfsKwargs]) -> None:
        with self.config(**kwargs):
            with self.ro(): self.tmpfs()

    def __add(self, kind: Literal['bind', 'tmpfs']) -> None:
        def apply_house_mapper(
            mapper: Optional[HouseMapper], path: str,
        ) -> str:
            if mapper is None:
                return path
            abspath = os.path.abspath(path)
            return mapper(path, abspath)

        config = self.__c_config.get()
        service = config['service']
        if service is None:
            raise ValueError('Cannot create mount with service unspecified.')
        dst_path = config['dst']
        if dst_path is None:
            raise ValueError(
                'Cannot create mount with destination path unspecified.'
            )
        mount_list = self.__get_service_mount_list(service)
        options = config['options']

        if kind == 'bind':
            src_path = config['src']
            if src_path is None:
                raise ValueError(
                    'Cannot create bind mount with host path unspecified.'
                )
            if not self.__src_exists() and options['must_exist']:
                src_path = config['src']
                raise ValueError(
                    f'Mount "{src_path}" -> "{dst_path}" is marked as '
                    f'must_exist, but the source path does not exist.'
                )
            src_path = apply_house_mapper(options['house_mapper'], src_path)
            mount_list.append(BindMountSpec(
                type='bind',
                # Use abspath for source to be safe: relative paths mentioned in mounts.yml
                # is relative to itself, not to where our mounthouse main.py is called from.
                source=os.path.abspath(src_path).replace('$', '$$'),
                target=dst_path.replace('$', '$$'),
                read_only=options['read_only'],
                bind={'create_host_path': options['create']},
            ))
        elif kind == 'tmpfs':
            tmpfs_opts: TmpfsMountOptions = {}
            tmpfs_size = options['tmpfs_size']
            if tmpfs_size is not None:
                tmpfs_opts['size'] = tmpfs_size
            mount_list.append(TmpfsMountSpec(
                type='tmpfs',
                target=dst_path.replace('$', '$$'),
                read_only=options['read_only'],
                tmpfs=tmpfs_opts
            ))
        else:  # pragma: no cover - unreachable given Literal
            raise ValueError(f'Unknown mount kind: {kind}')

    def get_compose_data(self) -> ComposeData:
        return {'services': self.__services}
