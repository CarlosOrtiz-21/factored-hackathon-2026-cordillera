"""Brand identity. **FICTIONAL**: "Banco Cordillera" is invented for the Factored
Hackathon 2026 prototype; any resemblance to a real bank is unintended. Phone lines and
hours below are fictional too.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Brand:
    bank: str = "Banco Cordillera"
    assistant: str = "Lía"
    app: str = "App Cordillera"
    phone: str = "#2222"  # fictional short code
    advisor_hours: dict[str, str] | None = None

    def hours(self, language: str) -> str:
        return {
            "es": "de lunes a sábado, de 7:00 a 20:00",
            "pt": "de segunda a sábado, das 7h às 20h",
        }.get(language, "de lunes a sábado, de 7:00 a 20:00")


BRAND = Brand()
