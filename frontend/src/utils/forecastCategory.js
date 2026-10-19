export const ALL_CATEGORIES_ID = "all";
export const ALL_CATEGORIES_LABEL = "All Categories";
export const FORECASTABLE_CATEGORY_ID = "international_settlement";

function normalizeCategoryText(value) {
  return String(value || "").trim().toLowerCase();
}

function looksLikeInternationalSettlement(value) {
  const text = normalizeCategoryText(value);
  return text.includes("int") && text.includes("settlement");
}

export function isAllCategories(category) {
  if (category == null || category === "") {
    return false;
  }
  if (typeof category !== "object") {
    return normalizeCategoryText(category) === ALL_CATEGORIES_ID
      || normalizeCategoryText(category) === normalizeCategoryText(ALL_CATEGORIES_LABEL);
  }
  const identifiers = [category.id, category.code, category.slug, category.name, category.label];
  return identifiers.some((value) => (
    normalizeCategoryText(value) === ALL_CATEGORIES_ID
    || normalizeCategoryText(value) === normalizeCategoryText(ALL_CATEGORIES_LABEL)
  ));
}

export function isInternationalSettlementCategory(category) {
  if (!category || typeof category !== "object") {
    return looksLikeInternationalSettlement(category);
  }
  const identifiers = [category.id, category.code, category.slug];
  if (identifiers.some((value) => normalizeCategoryText(value) === FORECASTABLE_CATEGORY_ID)) {
    return true;
  }
  return [category.name, category.source_name, category.label].some(looksLikeInternationalSettlement);
}

export function isForecastableCategory(category) {
  if (!category) {
    return false;
  }
  if (isAllCategories(category)) {
    return true;
  }
  if (typeof category !== "object") {
    return Boolean(String(category).trim());
  }
  return Boolean(String(category.id || category.name || category.source_name || "").trim());
}

export function forecastCategoryOptionLabel(category) {
  if (isAllCategories(category)) {
    return ALL_CATEGORIES_LABEL;
  }
  return String(category?.name || category?.source_name || category?.id || "").trim() || "Category";
}

export function requestCategoryValue(category) {
  if (!category || isAllCategories(category)) {
    return "all";
  }
  return String(category.name || category.id || "").trim();
}

export function withAllCategoriesOption(categories = []) {
  const discovered = (Array.isArray(categories) ? categories : [])
    .map((item) => {
      if (!item) {
        return null;
      }
      if (typeof item === "string") {
        return { id: item, name: item, budget_code_count: 0 };
      }
      const name = String(item.name || item.source_name || item.id || "").trim();
      if (!name) {
        return null;
      }
      return {
        id: String(item.id || name),
        name,
        budget_code_count: Number(item.budget_code_count) || 0,
      };
    })
    .filter(Boolean)
    .filter((item) => !isAllCategories(item));
  return [
    { id: ALL_CATEGORIES_ID, name: ALL_CATEGORIES_LABEL, budget_code_count: 0 },
    ...discovered,
  ];
}
