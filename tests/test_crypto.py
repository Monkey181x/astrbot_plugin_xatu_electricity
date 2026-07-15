import base64
import unittest
from unittest.mock import patch

from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import unpad

from xatu_electricity.crypto import encrypt_cas_password


class CryptoTests(unittest.TestCase):
    def test_encrypt_cas_password_matches_page_algorithm(self) -> None:
        salt = "rjBFAaHsNkKAhpoi"
        with patch(
            "xatu_electricity.crypto._random_string",
            side_effect=["A" * 64, "B" * 16],
        ):
            encrypted = encrypt_cas_password("secret", salt)

        cipher = AES.new(salt.encode(), AES.MODE_CBC, b"B" * 16)
        plaintext = unpad(
            cipher.decrypt(base64.b64decode(encrypted)), AES.block_size
        ).decode()
        self.assertEqual(plaintext, ("A" * 64) + "secret")


if __name__ == "__main__":
    unittest.main()
