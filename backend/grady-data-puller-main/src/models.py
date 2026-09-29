from dataclasses import dataclass
from typing import Optional


@dataclass
class WasdeRow:
    report_date: str
    wasde_number: int
    report_title: str
    attribute: str
    commodity: Optional[str]
    region: str
    market_year: str
    proj_est_flag: Optional[str]
    annual_quarter_flag: Optional[str]
    value: Optional[float]
    unit: Optional[str]
    release_date: Optional[str] = None
    release_time: Optional[str] = None
    forecast_year: Optional[int] = None
    forecast_month: Optional[int] = None
