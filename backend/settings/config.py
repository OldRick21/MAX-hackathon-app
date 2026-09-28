from auth.config import AuthSettings

class Settings(AuthSettings):
    USER_PROFILE_PROVISIONING_TOKEN: str = ""
    SCHEDULE_PROVISIONING_TOKEN: str = ""
    SERVICE_CONFIG_DIR: str = ""
    DATABASE_URL: str
    # --- Облачные сервисы платформы (CLOUD_RUNTIME_SPEC.md) ---
    # Ключ вывода machine-секретов cloud bindings; хранится только в секретах deployment.
    CLOUD_BINDING_KEY: str = ""
    # Токен, которым процесс administration читает свои bindings на private listener.
    ADMINISTRATION_PROVISIONING_TOKEN: str = ""
    # Адреса облачных типов задаёт платформа, а не администратор вуза.
    ADMINISTRATION_API_BASE_URL: str = "https://administration.platform.example/api/v1"
    ADMINISTRATION_CLIENT_BASE_URL: str = "https://administration.platform.example"
    SCHEDULE_API_BASE_URL: str = "https://schedule.platform.example/api/v1"
    SCHEDULE_CLIENT_BASE_URL: str = "https://schedule.platform.example"
    USER_PROFILE_API_BASE_URL: str = "https://profiles.platform.example/api/v1"
    USER_PROFILE_CLIENT_BASE_URL: str = "https://profiles.platform.example"
    # Ограничение числа одновременных заявок одного пользователя.
    MAX_PENDING_APPLICATIONS_PER_USER: int = 3

    def cloud_endpoints(self, service_type: str):
        return {
            "administration": (self.ADMINISTRATION_API_BASE_URL, self.ADMINISTRATION_CLIENT_BASE_URL),
            "schedule": (self.SCHEDULE_API_BASE_URL, self.SCHEDULE_CLIENT_BASE_URL),
            "user-profile": (self.USER_PROFILE_API_BASE_URL, self.USER_PROFILE_CLIENT_BASE_URL),
        }[service_type]

settings = Settings()
