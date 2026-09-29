from auth.config import AuthSettings

class Settings(AuthSettings):
    SERVICE_CONFIG_DIR: str = ""
    DATABASE_URL: str
    # Отдельный внутренний секрет read-only API для MAX-бота. Не равен токену самого бота.
    BOT_CORE_TOKEN: str = ""
    # --- Облачные сервисы (CLOUD_RUNTIME_SPEC.md): раннер на тип, внутри процесс на вуз ---
    # Ключ вывода секретов облачных экземпляров; только в секретах deployment.
    CLOUD_BINDING_KEY: str = ""
    # Токены раннеров: каждый видит только экземпляры своего типа.
    ADMINISTRATION_PROVISIONING_TOKEN: str = ""
    SCHEDULE_PROVISIONING_TOKEN: str = ""
    USER_PROFILE_PROVISIONING_TOKEN: str = ""
    # Публичные адреса: оболочка (расписание и «Люди» — пути на её origin) и origin администрирования.
    SHELL_ORIGIN: str = "https://shell.platform.example"
    ADMINISTRATION_PUBLIC_ORIGIN: str = "https://admin.platform.example"
    # Ограничение числа одновременных заявок одного пользователя.
    MAX_PENDING_APPLICATIONS_PER_USER: int = 3
    # Лимиты запросов в окно RATE_LIMIT_WINDOW_SECONDS (CORE_API_SPEC.md §3); 0 — без лимита.
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_LOGIN: int = 10            # на IP
    RATE_LIMIT_REFRESH: int = 30          # на сессию
    RATE_LIMIT_MACHINE_EXCHANGE: int = 30  # на client_id
    RATE_LIMIT_USER: int = 120            # прочие запросы пользователя
    RATE_LIMIT_CREDENTIAL: int = 600      # прочие запросы с machine token

    def cloud_urls(self, service_type: str, service_id: str):
        """Адреса облачного экземпляра: у каждого вуза свой путь /<service_id>/ в раннере типа."""
        shell = self.SHELL_ORIGIN.rstrip("/")
        admin = self.ADMINISTRATION_PUBLIC_ORIGIN.rstrip("/")
        return {
            "administration": (f"{admin}/{service_id}/api/v1", admin),
            "schedule": (f"{shell}/schedule/{service_id}/api/v1", shell),
            "user-profile": (f"{shell}/people/{service_id}/api/v1", shell),
        }[service_type]

    def provisioning_tokens(self) -> dict:
        return {"administration": self.ADMINISTRATION_PROVISIONING_TOKEN, "schedule": self.SCHEDULE_PROVISIONING_TOKEN,
                "user-profile": self.USER_PROFILE_PROVISIONING_TOKEN}

settings = Settings()
