import os
from dotenv import load_dotenv

load_dotenv()


class TariffConfig:
    """Configuration for your Flogas energy tariffs."""

    UNIT_RATE = float(os.getenv("FLOGAS_UNIT_RATE", "0.0809"))
    STANDING_CHARGE_DAILY = float(os.getenv("FLOGAS_STANDING_CHARGE_DAILY", "0.4142"))

    _DEFAULTS = {
        "gas": {
            "unit_rate_eur_per_kwh": 0.0809,
            "standing_charge_eur_per_year": 151.18,
        }
    }

    @classmethod
    def get_unit_rate(cls) -> float:
        return cls.UNIT_RATE if cls.UNIT_RATE > 0 else cls._DEFAULTS["gas"]["unit_rate_eur_per_kwh"]

    @classmethod
    def get_standing_charge_daily(cls) -> float:
        return (
            cls.STANDING_CHARGE_DAILY
            if cls.STANDING_CHARGE_DAILY > 0
            else cls._DEFAULTS["gas"]["standing_charge_eur_per_year"] / 365.0
        )

    @classmethod
    def get_standing_charge_yearly(cls) -> float:
        return cls.get_standing_charge_daily() * 365.0

    @classmethod
    def calculate_daily_cost(cls, daily_kwh: float) -> float:
        unit_rate = cls.get_unit_rate()
        standing_daily = cls.get_standing_charge_daily()
        return daily_kwh * unit_rate + standing_daily

    @classmethod
    def calculate_projected_monthly_cost(cls, daily_kwh: float) -> float:
        daily_cost = cls.calculate_daily_cost(daily_kwh)
        return daily_cost * 30.0


if __name__ == "__main__":
    test_kwh = 12.0
    daily_cost = TariffConfig.calculate_daily_cost(test_kwh)
    projected_monthly = TariffConfig.calculate_projected_monthly_cost(test_kwh)

    print(f"Unit rate: €{TariffConfig.get_unit_rate():.4f}/kWh")
    print(f"Standing charge: €{TariffConfig.get_standing_charge_daily():.4f}/day")
    print(f"Example: {test_kwh} kWh/day -> Daily cost: €{daily_cost:.2f}, Projected monthly: €{projected_monthly:.2f}")
