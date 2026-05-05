"""Data coordinator for Suno integration."""

from __future__ import annotations

import logging
from collections.abc import Coroutine
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SunoClient
from .const import DEFAULT_CACHE_TTL, DOMAIN
from .exceptions import SunoAuthError
from .library_refresh import (
    _MAX_PARENT_LOOKUPS_PER_CYCLE,
    HomeAssistantStoredLibrary,
    LibraryRefresh,
    LibrarySnapshot,
    ParentLookup,
    SunoClientLibraryAdapter,
    _build_clip_index,
)
from .models import SunoData, SunoUser

if TYPE_CHECKING:
    import asyncio

    from .cache import SunoCache
    from .models import SunoClip

_LOGGER = logging.getLogger(__name__)


class SunoCoordinator(DataUpdateCoordinator[SunoData]):
    """Home Assistant adapter for the Suno Library Refresh module."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, client: SunoClient, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=DEFAULT_CACHE_TTL),
            config_entry=entry,
        )
        self.client = client
        self.cache: SunoCache | None = None
        self._stored_library = HomeAssistantStoredLibrary(hass, entry.entry_id)
        self._store = self._stored_library.store
        self.library_refresh = LibraryRefresh(
            SunoClientLibraryAdapter(client),
            self._stored_library,
            task_factory=self._create_refresh_task,
            on_update=self._handle_library_update,
            on_error=self._handle_library_error,
        )
        self.user = self.library_refresh.identity

    @property
    def data_version(self) -> int:
        """Monotonic counter incremented when Library Refresh publishes data."""
        return self.library_refresh.data_version

    @property
    def _data_version(self) -> int:
        return self.library_refresh.data_version

    @_data_version.setter
    def _data_version(self, version: int) -> None:
        self.library_refresh.data_version = version

    @property
    def _refresh_task(self) -> asyncio.Task[None] | None:
        return self.library_refresh.refresh_task

    @property
    def _ancestor_task(self) -> asyncio.Task[None] | None:
        return None

    async def async_load_stored_data(self) -> SunoData | None:
        """Load the Stored Library through the Library Refresh seam."""
        data = await self.library_refresh.async_load_stored_library()
        if data is not None:
            self.data = data
        return data

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.unique_id or self.config_entry.entry_id)},
            name=self.user.display_name,
            manufacturer="Suno",
            model="Music Library",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://suno.com",
        )

    async def _async_update_data(self) -> SunoData:
        """Return current Suno Library data and schedule background refresh."""
        if self.library_refresh.data_version == 0:
            self.library_refresh.current_data = SunoData()
        return await self.library_refresh.async_update()

    async def _async_fetch_remote_data(self) -> SunoData:
        """Fetch one remote Library Refresh snapshot for tests and manual callers."""
        try:
            snapshot = await self.library_refresh.async_refresh_once()
        except SunoAuthError as err:
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="auth_failed") from err
        except Exception as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self._sync_identity(snapshot.identity)
        return snapshot.data

    def _schedule_remote_refresh(self) -> None:
        """Compatibility wrapper for the Library Refresh scheduler."""
        self.library_refresh._schedule_remote_refresh()

    async def _async_background_refresh(self) -> None:
        """Compatibility wrapper for the Library Refresh background task."""
        await self.library_refresh._async_background_refresh()

    async def _async_store_save(self, payload: dict[str, Any]) -> None:
        """Persist a raw Stored Library payload, logging failures."""
        try:
            await self._store.async_save(payload)
        except Exception:
            _LOGGER.warning("Failed to persist Suno library to Store", exc_info=True)

    async def _resolve_root_ancestors(self, data: SunoData) -> None:
        """Compatibility test entry-point for Clip Lineage resolution."""
        self.library_refresh._resolve_root_ancestors_in_memory(data)
        pending = await self.library_refresh._resolve_root_ancestors_api(data, self.library_refresh.current_data)
        self.library_refresh._apply_album_details(data)
        if pending:
            self.library_refresh.current_data = data

    def _build_clip_index(self, data: SunoData) -> dict[str, SunoClip]:
        """Build a unified clip id to clip map across all data buckets."""
        return _build_clip_index(data)

    def _resolve_root_ancestors_in_memory(self, data: SunoData) -> None:
        """Compatibility wrapper for in-memory Clip Lineage resolution."""
        self.library_refresh._resolve_root_ancestors_in_memory(data)

    async def _resolve_root_ancestors_api(self, data: SunoData) -> None:
        """Compatibility wrapper for remote Clip Lineage resolution."""
        await self.library_refresh._resolve_root_ancestors_api(data, self.library_refresh.current_data)
        self.library_refresh._apply_album_details(data)

    async def get_clip_parent(self, clip_id: str) -> ParentLookup:
        """Expose parent lookup through the Library Refresh adapter for tests."""
        return await self.library_refresh._source.get_clip_parent(clip_id)

    def _create_refresh_task(self, coro: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
        return self.hass.async_create_background_task(coro, f"{name}_{self.config_entry.entry_id}")

    def _handle_library_update(self, snapshot: LibrarySnapshot) -> None:
        self._sync_identity(snapshot.identity)
        self.async_set_updated_data(snapshot.data)

    def _handle_library_error(self, err: BaseException) -> None:
        if isinstance(err, SunoAuthError):
            self.async_set_update_error(ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="auth_failed"))
        else:
            self.async_set_update_error(UpdateFailed(str(err)))

    def _sync_identity(self, identity: SunoUser) -> None:
        if identity.display_name != self.user.display_name:
            _LOGGER.info("Display name changed: '%s' -> '%s'", self.user.display_name, identity.display_name)
        self.user = identity
        if self.user.display_name != self.config_entry.title:
            self.hass.config_entries.async_update_entry(self.config_entry, title=self.user.display_name)


__all__ = ["SunoCoordinator", "_MAX_PARENT_LOOKUPS_PER_CYCLE"]
