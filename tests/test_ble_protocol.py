from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_DIR = ROOT / "desktop"
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from ble_protocol import decode_prompt_bundle, encode_prompt_bundle  # noqa: E402


class PromptBundleTests(unittest.TestCase):
    def test_roundtrip_with_gzipped_metadata_and_image(self) -> None:
        metadata = json.dumps(
            {
                "type": "prompt",
                "messageId": "req-1",
                "prompt": "Choose the best answer",
                "imageMimeType": "image/png",
            },
            separators=(",", ":"),
        ).encode("utf-8")
        image_bytes = b"\x89PNG" + b"test-image-payload"

        payload = encode_prompt_bundle(metadata, image_bytes, gzip_metadata=True)
        decoded = decode_prompt_bundle(payload)

        self.assertIsNotNone(decoded)
        decoded_metadata, decoded_image = decoded or (b"", b"")
        self.assertEqual(decoded_metadata, metadata)
        self.assertEqual(decoded_image, image_bytes)

    def test_roundtrip_without_image(self) -> None:
        metadata = b'{"type":"prompt","messageId":"req-2","prompt":"hello"}'
        payload = encode_prompt_bundle(metadata, b"", gzip_metadata=False)

        decoded = decode_prompt_bundle(payload)

        self.assertEqual(decoded, (metadata, b""))


if __name__ == "__main__":
    unittest.main()
