from auth.config import AuthSettings

class Settings(AuthSettings):
    SERVICE_CONFIG_DIR: str = ""
    DATABASE_URL: str
    # Адреса администрирования нового вуза по умолчанию (контейнер вуза, см. services/administration).
    # Оператор меняет их в пульте в карточке сервиса.
    ADMINISTRATION_API_BASE_URL: str = "https://administration.platform.example/api/v1"
    ADMINISTRATION_CLIENT_BASE_URL: str = "https://administration.platform.example"
    # Ограничение числа одновременных заявок одного пользователя.
    MAX_PENDING_APPLICATIONS_PER_USER: int = 3

settings = Settings()
