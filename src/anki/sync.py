import asyncio
from dataclasses import dataclass

from anki.collection import Collection
from anki.sync import SyncAuth


@dataclass
class SyncResult:
    success: bool
    message: str


class AnkiSyncer:
    def __init__(self, collection_path: str, email: str, password: str):
        self._collection_path = collection_path
        self._email = email
        self._password = password

    def sync(self) -> SyncResult:
        """Synchronous. Call via run_in_executor."""
        col = Collection(self._collection_path)
        try:
            auth: SyncAuth = col.sync_login(self._email, self._password, None)
            out = col.sync_collection(auth, sync_media=False)
            # ChangesRequired enum values are accessed as attributes on the
            # SyncCollectionResponse instance (protobuf convention):
            # out.NO_CHANGES=0, out.NORMAL_SYNC=1, out.FULL_SYNC=2,
            # out.FULL_DOWNLOAD=3, out.FULL_UPLOAD=4
            required = out.required
            if required in (out.NO_CHANGES, out.NORMAL_SYNC):
                return SyncResult(success=True, message="Sync complete")
            if required == out.FULL_DOWNLOAD:
                col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
                return SyncResult(success=True, message="Full download complete")
            if required == out.FULL_UPLOAD:
                col.full_upload_or_download(auth=auth, server_usn=None, upload=True)
                return SyncResult(success=True, message="Full upload complete")
            if required == out.FULL_SYNC:
                return SyncResult(
                    success=False,
                    message=(
                        "Full sync required: open Anki Desktop once to resolve "
                        "the conflict, or choose upload/download manually."
                    ),
                )
            return SyncResult(success=True, message=f"Sync complete (required={required})")
        except Exception as e:
            return SyncResult(success=False, message=str(e))
        finally:
            col.close()

    async def async_sync(self) -> SyncResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.sync)
