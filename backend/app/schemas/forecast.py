from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class MonthlyForecastRequest(BaseModel):
    start_month: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    end_month: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    forecast_months: Optional[int] = Field(default=None, ge=1, le=60)
    account_codes: Optional[list[str]] = None
    category: Optional[str] = None

    @model_validator(mode="after")
    def validate_request(self):
        if (self.start_month or self.end_month) and self.forecast_months is not None:
            raise ValueError(
                "Provide either start_month and end_month, or forecast_months, not both."
            )
        if self.start_month or self.end_month:
            if not self.start_month or not self.end_month:
                raise ValueError("start_month and end_month must be provided together.")
        elif self.forecast_months is None:
            raise ValueError("Provide start_month and end_month, or forecast_months.")
        return self


class MonthlyForecastItem(BaseModel):
    month: str
    forecast_amount: float
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    interval_level: Optional[float] = None
    interval_status: Optional[str] = None
    specific_month_historical_average: Optional[float] = None
    specific_month_history_count: int = 0


class AccountMonthlyForecastItem(BaseModel):
    budget_code: str
    account_name: Optional[str] = None
    month: str
    forecast_amount: float
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    interval_level: Optional[float] = None
    interval_status: Optional[str] = None


class AccountYearlyForecastItem(BaseModel):
    budget_code: str
    account_name: Optional[str] = None
    year: int
    forecast_amount: float
    year_status: str
    months_included: int


class CombinedYearlyForecastItem(BaseModel):
    year: int
    forecast_amount: float
    year_status: str
    months_included: int


class HistoricalActualItem(BaseModel):
    month: str
    actual_amount: float


class MonthlyForecastResponse(BaseModel):
    forecast_type: str
    category: str
    target_description: str
    overall_best_algorithm: str
    selected_account_count: int
    selected_accounts: list[str]
    partial_forecast: bool = False
    budget_code_forecasts: Optional[list[dict]] = None
    historical_start: str
    historical_end: str
    history_end: str
    forecast_start: str
    forecast_end: str
    requested_start_month: str
    requested_end_month: str
    forecast_month_count: int
    amount_unit: str
    model_amount_unit: str
    response_amount_unit: str
    conversion_factor: float
    clip_negative_applied: bool
    monthly_forecasts: list[MonthlyForecastItem]
    account_monthly_forecasts: list[AccountMonthlyForecastItem]
    combined_monthly_forecasts: list[MonthlyForecastItem]
    account_yearly_forecasts: list[AccountYearlyForecastItem]
    combined_yearly_forecasts: list[CombinedYearlyForecastItem]
    historical_actuals: list[HistoricalActualItem]
    historical_monthly_average: Optional[float] = None
    historical_month_count: int = 0
    overall_total: float
    monthly_average: float
    minimum_monthly_forecast: float
    maximum_monthly_forecast: float
    generated_at: datetime
    interval_method: Optional[str] = None
    interval_coverage: Optional[float] = None
    forecast_coverage: Optional[dict] = None


class AccountListItem(BaseModel):
    budget_code: str
    account_name: Optional[str] = None
    category: Optional[str] = None
    production_status: Optional[str] = None


class ForecastCategoryItem(BaseModel):
    id: str
    name: str
    budget_code_count: int = 0


class AccountListResponse(BaseModel):
    selected_account_count: int
    selected_accounts: list[str]
    accounts: list[AccountListItem]
    categories: list[ForecastCategoryItem] = Field(default_factory=list)
    missing_category_codes: list[dict] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    forecast_type: Optional[str] = None
    history_end: Optional[str] = None
    max_forecast_end: Optional[str] = "2030-12"
    category: Optional[str] = None
    overall_best_algorithm: Optional[str] = None
    selected_account_count: Optional[int] = None
    categories: Optional[list[str]] = None


class ForecastRecordSummary(BaseModel):
    id: int
    generated_at: datetime
    category: str
    overall_best_algorithm: str
    requested_start_month: str
    requested_end_month: str
    forecast_month_count: int
    selected_account_count: int
    amount_unit: str
    overall_total: float
    monthly_average: float
    minimum_monthly_forecast: float
    maximum_monthly_forecast: float


class ForecastHistoryListResponse(BaseModel):
    records: list[ForecastRecordSummary]
