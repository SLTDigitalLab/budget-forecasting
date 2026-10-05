import { useEffect, useId, useState } from "react";
import { Loader2 } from "lucide-react";
import { getForecastAccounts } from "../api/forecastApi";
import { useForecast } from "../context/ForecastContext";
import {
  ALL_CATEGORIES_ID,
  forecastCategoryOptionLabel,
  isForecastableCategory,
  withAllCategoriesOption,
} from "../utils/forecastCategory";
import {
  MONTH_OPTIONS,
  PERIOD_TYPE,
  PERIOD_TYPE_OPTIONS,
  compactPeriodHelp,
  financialYearOptions,
  formatSelectedPeriodDisplay,
  lastAllowedIso,
  remainingYearOptions,
  resolveForecastPeriod,
  splitIsoMonth,
  yearOptions,
  isMonthDisabled,
} from "../utils/forecastPeriod";

export default function ForecastControls() {
  const {
    category,
    setCategory,
    periodType,
    setPeriodType,
    startMonth,
    setStartMonth,
    endMonth,
    setEndMonth,
    earliestMonth,
    loading,
    validation,
    canSubmit,
    runForecast,
  } = useForecast();
  const categoryErrorId = useId();
  const categoryHelpId = useId();
  const [categories, setCategories] = useState(withAllCategoriesOption([]));
  const [categoryError, setCategoryError] = useState("");
  const [categoriesLoading, setCategoriesLoading] = useState(true);
  const [categoriesLoadError, setCategoriesLoadError] = useState("");

  const years = yearOptions(earliestMonth);
  const remainingYears = remainingYearOptions(earliestMonth);
  const financialYears = financialYearOptions(earliestMonth);
  const latestMonth = lastAllowedIso(earliestMonth);
  const startParts = splitIsoMonth(startMonth);
  const endParts = splitIsoMonth(endMonth);
  const singleFieldset = periodType !== PERIOD_TYPE.FINANCIAL_YEARS;
  const categoryId = String(category?.id || ALL_CATEGORIES_ID);
  const selectedCategory = categories.find((item) => String(item.id) === categoryId) || category;
  const categoryReady = isForecastableCategory(selectedCategory);
  const periodLocked = !categoryReady || categoriesLoading;
  const periodHelp = compactPeriodHelp({
    startIso: startMonth,
    endIso: endMonth,
    earliestIso: earliestMonth,
  });
  const selectedPeriodLabel = formatSelectedPeriodDisplay(startMonth, endMonth);

  useEffect(() => {
    let cancelled = false;
    async function loadCategories() {
      try {
        const catalog = await getForecastAccounts();
        if (cancelled) {
          return;
        }
        setCategories(withAllCategoriesOption(catalog.categories));
        setCategoriesLoadError("");
      } catch (cause) {
        if (!cancelled) {
          setCategoriesLoadError(cause.message || "Unable to load budget categories.");
        }
      } finally {
        if (!cancelled) {
          setCategoriesLoading(false);
        }
      }
    }
    loadCategories();
    return () => {
      cancelled = true;
    };
  }, []);

  function applyResolved(next) {
    setStartMonth(next.startIso);
    setEndMonth(next.endIso);
  }

  function changePeriodType(nextType) {
    if (periodLocked) {
      return;
    }
    setPeriodType(nextType);
    applyResolved(resolveForecastPeriod({
      periodType: nextType,
      earliestIso: earliestMonth,
      currentStartIso: startMonth,
      currentEndIso: endMonth,
    }));
  }

  function applySpecificMonth(year, month) {
    applyResolved(resolveForecastPeriod({
      periodType: PERIOD_TYPE.SPECIFIC_MONTH,
      year,
      month,
      earliestIso: earliestMonth,
      currentStartIso: startMonth,
    }));
  }

  function applyRemainingYear(year) {
    applyResolved(resolveForecastPeriod({
      periodType: PERIOD_TYPE.REMAINING_YEAR,
      year,
      earliestIso: earliestMonth,
      currentStartIso: startMonth,
    }));
  }

  function applyFinancialYears(startYear, nextEndYear) {
    applyResolved(resolveForecastPeriod({
      periodType: PERIOD_TYPE.FINANCIAL_YEARS,
      startYear,
      endYear: nextEndYear,
      earliestIso: earliestMonth,
      currentStartIso: startMonth,
      currentEndIso: endMonth,
    }));
  }

  function handleSubmit(event) {
    event.preventDefault();
    if (!isForecastableCategory(selectedCategory)) {
      setCategoryError("Select a budget category.");
      return;
    }
    setCategoryError("");
    runForecast();
  }

  return (
    <section className="card section-card generate-form-card" aria-labelledby="forecast-controls-heading">
      <h2 id="forecast-controls-heading" className="section-card-header">Generate New Forecast</h2>
      <form className="generate-form" onSubmit={handleSubmit}>
        <div className="generate-step">
          <div className="generate-step-label">
            <span className="generate-step-badge" aria-hidden="true">1</span>
            <label htmlFor="forecast-budget-category">Budget Category</label>
          </div>
          <div className="field generate-category-field">
            <select
              id="forecast-budget-category"
              value={categoryId}
              onChange={(event) => {
                const nextId = event.target.value;
                const nextCategory = categories.find((item) => String(item.id) === String(nextId));
                if (nextId && !isForecastableCategory(nextCategory)) {
                  return;
                }
                setCategory(nextCategory || { id: nextId, name: nextId });
                if (nextId) {
                  setCategoryError("");
                }
              }}
              required
              aria-required="true"
              aria-invalid={categoryError ? "true" : "false"}
              aria-describedby={[categoryHelpId, categoryError || categoriesLoadError ? categoryErrorId : ""]
                .filter(Boolean)
                .join(" ")}
              disabled={categoriesLoading}
            >
              {categories.map((item) => (
                <option key={item.id} value={item.id}>
                  {forecastCategoryOptionLabel(item)}
                </option>
              ))}
            </select>
          </div>
          <p id={categoryHelpId} className="generate-category-help">
            Forecast every trained Budget Code, or choose one discovered category.
          </p>
          {categoryError || categoriesLoadError ? (
            <p id={categoryErrorId} className="validation generate-inline-validation" role="alert">
              {categoryError || categoriesLoadError}
            </p>
          ) : null}
        </div>

        <div className="generate-step">
          <div className="generate-step-label">
            <span className="generate-step-badge" aria-hidden="true">2</span>
            <span id="forecast-period-type-label">Forecast Period Type</span>
          </div>
          <div
            className="period-type-cards"
            role="radiogroup"
            aria-labelledby="forecast-period-type-label"
            aria-disabled={periodLocked}
          >
            {PERIOD_TYPE_OPTIONS.map((option) => {
              const selected = periodType === option.value;
              return (
                <label
                  key={option.value}
                  className={`period-type-card${selected ? " is-selected" : ""}`}
                >
                  <input
                    type="radio"
                    name="forecast-period-type"
                    value={option.value}
                    checked={selected}
                    disabled={periodLocked}
                    onChange={() => changePeriodType(option.value)}
                  />
                  <span className="period-type-card-title">{option.label}</span>
                  <span className="period-type-card-state">{selected ? "Selected" : "Not selected"}</span>
                </label>
              );
            })}
          </div>
        </div>

        <div className="generate-step">
          <div className="generate-step-label">
            <span className="generate-step-badge" aria-hidden="true">3</span>
            <span>Forecast Period Details</span>
          </div>
          <div className="generate-period-action-row">
            <div className={`generate-period-fields${singleFieldset ? " is-single" : ""}`}>
              {periodType === PERIOD_TYPE.SPECIFIC_MONTH ? (
                <fieldset className="period-group" disabled={periodLocked}>
                  <legend className="sr-only">Forecast Month</legend>
                  <div className="period-fields">
                    <div className="field">
                      <label htmlFor="start-year">Year</label>
                      <select
                        id="start-year"
                        value={startParts.year}
                        onChange={(event) => applySpecificMonth(event.target.value, startParts.month || "01")}
                        required
                        disabled={periodLocked}
                      >
                        {years.map((year) => (
                          <option key={`specific-year-${year}`} value={year}>
                            {year}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor="start-month">Month</label>
                      <select
                        id="start-month"
                        value={startParts.month}
                        onChange={(event) => applySpecificMonth(startParts.year, event.target.value)}
                        required
                        disabled={periodLocked}
                      >
                        {MONTH_OPTIONS.map((option) => (
                          <option
                            key={`specific-month-${option.value}`}
                            value={option.value}
                            disabled={isMonthDisabled(startParts.year, option.number, earliestMonth, latestMonth)}
                          >
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                </fieldset>
              ) : null}
              {periodType === PERIOD_TYPE.REMAINING_YEAR ? (
                <fieldset className="period-group" disabled={periodLocked}>
                  <legend className="sr-only">Forecast Year</legend>
                  <div className="field">
                    <label htmlFor="remaining-year">Forecast Year</label>
                    <select
                      id="remaining-year"
                      value={startParts.year}
                      onChange={(event) => applyRemainingYear(event.target.value)}
                      required
                      disabled={periodLocked}
                    >
                      {remainingYears.map((year) => (
                        <option key={`remaining-year-${year}`} value={year}>
                          {year}
                        </option>
                      ))}
                    </select>
                  </div>
                </fieldset>
              ) : null}
              {periodType === PERIOD_TYPE.FINANCIAL_YEARS ? (
                <fieldset className="period-group" disabled={periodLocked}>
                  <legend className="sr-only">Future financial years</legend>
                  <div className="period-fields">
                    <div className="field">
                      <label htmlFor="financial-start-year">Start Year</label>
                      <select
                        id="financial-start-year"
                        value={startParts.year}
                        onChange={(event) => applyFinancialYears(event.target.value, endParts.year)}
                        required
                        disabled={periodLocked}
                      >
                        {financialYears.map((year) => (
                          <option key={`financial-start-${year}`} value={year}>
                            {year}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor="financial-end-year">End Year</label>
                      <select
                        id="financial-end-year"
                        value={endParts.year}
                        onChange={(event) => applyFinancialYears(startParts.year, event.target.value)}
                        required
                        disabled={periodLocked}
                      >
                        {financialYears.map((year) => (
                          <option
                            key={`financial-end-${year}`}
                            value={year}
                            disabled={Number(year) < Number(startParts.year)}
                          >
                            {year}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                </fieldset>
              ) : null}
            </div>
            <div className="generate-selected-period" aria-live="polite">
              <span>Selected period</span>
              <strong>{selectedPeriodLabel}</strong>
            </div>
            <button
              className="generate-button"
              type="submit"
              disabled={periodLocked || !canSubmit || loading}
            >
              {loading ? <Loader2 className="spin" size={18} aria-hidden="true" /> : null}
              {loading ? "Generating Forecast..." : "Generate Forecast"}
            </button>
          </div>
          {periodHelp ? <p className="generate-period-help">{periodHelp}</p> : null}
        </div>
      </form>
      {validation ? <p className="validation generate-inline-validation" role="alert">{validation}</p> : null}
    </section>
  );
}
