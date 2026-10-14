import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from incoming_photo import MAX_PHOTO_BYTES, download_photo, select_photo
from telegram_api import TelegramError


class PhotoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.api = Mock(_file_base='https://example.invalid/file/')
        self.photo = {'file_id': 'synthetic', 'width': 20, 'height': 20}

    def download(self, data, size=None, remote='photos/synthetic.jpg'):
        info = {'file_path': remote}
        if size is not None:
            info['file_size'] = size
        self.api._call.return_value = info
        with patch('incoming_photo.urllib.request.urlopen', return_value=io.BytesIO(data)):
            return download_photo(self.api, self.photo, self.root)

    def test_stop_before_download_does_not_contact_telegram(self):
        import threading
        stop = threading.Event()
        stop.set()
        with self.assertRaises(ValueError):
            download_photo(self.api, self.photo, self.root, cancelled=stop)
        self.api._call.assert_not_called()

    def test_largest_valid_variant(self):
        large = dict(self.photo, width=100)
        self.assertIs(select_photo([{}, self.photo, large, dict(large, width=True)]), large)
        for value in [None, {}, [], [{'file_id': 'x', 'width': -1, 'height': 1}]]:
            self.assertIsNone(select_photo(value))

    def test_private_file_and_supported_formats(self):
        for data, suffix in [(b'\xff\xd8\xffsynthetic', '.jpg'), (b'\x89PNG\r\n\x1a\nsynthetic', '.png')]:
            path = self.download(data, len(data))
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(path.suffix, suffix)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(path.is_relative_to(self.root / 'incoming-images'))
            path.unlink()

    def test_bad_remote_paths_never_download(self):
        for remote in ['../secret', '/photos/a.jpg', 'photos//a.jpg', 'photos/./a.jpg', 'https://example.invalid/a', None]:
            with self.subTest(remote=remote), patch('incoming_photo.urllib.request.urlopen') as request:
                self.api._call.return_value = {'file_path': remote}
                with self.assertRaises(TelegramError):
                    download_photo(self.api, self.photo, self.root)
                request.assert_not_called()

    def test_declared_oversize_never_calls_api(self):
        self.photo['file_size'] = MAX_PHOTO_BYTES + 1
        with self.assertRaises(ValueError):
            download_photo(self.api, self.photo, self.root)
        self.api._call.assert_not_called()

    def test_failed_download_leaves_no_file(self):
        for data, size in [(b'', None), (b'not an image', None), (b'\xff\xd8\xffx', 200),
                           (b'\xff\xd8\xff' + b'x' * MAX_PHOTO_BYTES, None)]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.download(data, size)
            self.assertFalse(list((self.root / 'incoming-images').iterdir()))

    def test_symlink_directory_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'incoming-images').symlink_to(outside)
        with self.assertRaises(ValueError):
            self.download(b'\xff\xd8\xffx')
        self.assertFalse(list(outside.iterdir()))

    def test_transport_error_is_sanitized_and_cleans_file(self):
        self.api._call.return_value = {'file_path': 'photos/a.jpg'}
        with patch('incoming_photo.urllib.request.urlopen', side_effect=OSError('private-url-token')):
            with self.assertRaises(TelegramError) as caught:
                download_photo(self.api, self.photo, self.root)
        self.assertNotIn('private-url-token', str(caught.exception))
        self.assertFalse(list((self.root / 'incoming-images').iterdir()))
