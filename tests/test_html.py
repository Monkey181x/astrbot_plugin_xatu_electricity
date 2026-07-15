import unittest

from xatu_electricity.html import extract_element_text, parse_login_form


class HtmlTests(unittest.TestCase):
    def test_parse_login_form(self) -> None:
        html = """
        <form id="other"><input name="execution" value="wrong"></form>
        <form id="pwdFromId" action="/authserver/login">
          <input name="execution" value="e1&amp;x">
          <input id="pwdEncryptSalt" value="rjBFAaHsNkKAhpoi">
        </form>
        """
        form = parse_login_form(html)
        self.assertEqual(form.action, "/authserver/login")
        self.assertEqual(form.fields["execution"], "e1&x")
        self.assertEqual(form.values_by_id["pwdEncryptSalt"], "rjBFAaHsNkKAhpoi")

    def test_extract_element_text(self) -> None:
        html = '<div id="showErrorTip"><span>Login failed</span></div>'
        self.assertEqual(extract_element_text(html, "showErrorTip"), "Login failed")


if __name__ == "__main__":
    unittest.main()
