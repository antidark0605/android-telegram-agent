"""Compatibility wrappers for the generalized inbound media helpers."""
import urllib

from incoming_media import MAX_INCOMING_BYTES, download_media, select_photo

MAX_PHOTO_BYTES = MAX_INCOMING_BYTES


def download_photo(api, photo, workspace, cancelled=None):
    descriptor = dict(photo)
    descriptor["kind"] = "photo"
    return download_media(api, descriptor, workspace, cancelled=cancelled,
                          directory_name="incoming-images")


def select_photo(value):
    """Keep the historical helper's original Telegram photo object shape."""
    from incoming_media import select_photo as _select_photo
    descriptor = _select_photo(value)
    if descriptor is None:
        return None
    return next((item for item in value
                 if isinstance(item, dict)
                 and item.get("file_id") == descriptor["file_id"]
                 and item.get("width") == descriptor["width"]
                 and item.get("height") == descriptor["height"]), None)


def select_document(value):
    from incoming_media import select_document as _select_document
    return _select_document(value)
