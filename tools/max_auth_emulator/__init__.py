"""Generate MAX WebApp authentication data for controlled testing."""

from .signer import build_launch_url, generate_init_data

__all__ = ["build_launch_url", "generate_init_data"]
