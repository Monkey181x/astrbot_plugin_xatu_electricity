class XatuElectricityError(Exception):
    """Base error for the electricity backend."""


class CredentialsMissingError(XatuElectricityError):
    """Raised when a CAS login is needed but credentials are unavailable."""


class AuthenticationError(XatuElectricityError):
    """Raised when the CAS or X-Token authentication flow fails."""


class CaptchaRequiredError(AuthenticationError):
    """Raised when CAS requires an interactive CAPTCHA."""


class ApiResponseError(XatuElectricityError):
    """Raised when the electricity API returns an unexpected response."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"electricity API error {code}: {message}")
        self.code = code
        self.message = message


class RoomNotFoundError(XatuElectricityError):
    """Raised when a building does not contain one unique requested room."""

    def __init__(self, building_id: str, room_name: str, matches: int) -> None:
        super().__init__(
            f"expected one room in building {building_id!r} matching "
            f"{room_name!r}, found {matches}"
        )
        self.building_id = building_id
        self.room_name = room_name
        self.matches = matches
