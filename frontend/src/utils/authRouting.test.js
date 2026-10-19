import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  consumeAuthReturn,
  pageFrameFor,
  rememberAuthReturn,
  routeAfterLogin,
  routeAfterLogout,
  shouldMountProtectedPage,
  signedOutRoute,
} from "../auth/authRouting.js";

const here = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(resolve(here, "../App.jsx"), "utf8");
const protectedRouteSource = readFileSync(resolve(here, "../auth/ProtectedRoute.jsx"), "utf8");
const loginSource = readFileSync(resolve(here, "../pages/Login.jsx"), "utf8");
const authContextSource = readFileSync(resolve(here, "../auth/AuthContext.jsx"), "utf8");
const forecastContextSource = readFileSync(resolve(here, "../context/ForecastContext.jsx"), "utf8");

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    getItem(key) {
      return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null;
    },
    setItem(key, value) {
      data[key] = String(value);
    },
    removeItem(key) {
      delete data[key];
    },
  };
}

test("signed-out user opening / stays on / with the login overlay", () => {
  const route = signedOutRoute("/");
  assert.equal(route.pathname, "/");
  assert.equal(route.replace, false);
  assert.equal(route.showOverlay, true);
  assert.equal(pageFrameFor("/").title, "Overview");
});

test("signed-out user opening /settings stays on /settings with the login overlay", () => {
  const route = signedOutRoute("/settings");
  assert.equal(route.pathname, "/settings");
  assert.equal(route.showOverlay, true);
  assert.equal(pageFrameFor("/settings").title, "Settings");
});

test("signed-out user opening /generate-forecast keeps that route and the login overlay", () => {
  const route = signedOutRoute("/generate-forecast");
  assert.equal(route.pathname, "/generate-forecast");
  assert.equal(route.showOverlay, true);
  assert.equal(pageFrameFor("/generate-forecast").title, "Generate Forecast");
});

test("successful login removes the overlay and keeps the original route", () => {
  const storage = memoryStorage();
  rememberAuthReturn("/settings", storage);
  const returnTo = consumeAuthReturn(storage);
  const route = routeAfterLogin(returnTo);
  assert.equal(route.pathname, "/settings");
  assert.equal(route.showOverlay, false);
  assert.equal(storage.getItem("auth_return_path"), null);
});

test("logout shows the overlay and does not navigate to /login", () => {
  const route = routeAfterLogout("/analytics");
  assert.equal(route.pathname, "/analytics");
  assert.equal(route.navigateToLogin, false);
  assert.equal(route.showOverlay, true);
  assert.doesNotMatch(authContextSource, /["']\/login["']/);
});

test("manually opening /login replaces the address with / and shows the overlay", () => {
  const route = signedOutRoute("/login");
  assert.equal(route.pathname, "/");
  assert.equal(route.replace, true);
  assert.equal(route.showOverlay, true);
  assert.match(appSource, /path="\/login" element=\{<Navigate to="\/" replace \/>\}/);
});

test("signed-out background stays inert and does not mount protected pages", () => {
  assert.equal(shouldMountProtectedPage(false), false);
  assert.equal(shouldMountProtectedPage(true), true);
  assert.match(protectedRouteSource, /inert=\{signedOut \? "" : undefined\}/);
  assert.match(protectedRouteSource, /aria-hidden=\{signedOut \? "true" : undefined\}/);
  assert.match(protectedRouteSource, /login-app-preview/);
  assert.match(protectedRouteSource, /enabled=\{isAuthenticated\}/);
  assert.match(forecastContextSource, /if \(!enabled\)/);
  assert.match(protectedRouteSource, /if \(!shouldMountProtectedPage\(isAuthenticated\)\)/);
});

test("Microsoft login button still invokes the existing login handler", () => {
  assert.match(loginSource, /onClick=\{error \? retry : login\}/);
  assert.match(authContextSource, /rememberAuthReturn\(window\.location\.pathname, sessionStorage\)/);
  assert.match(authContextSource, /startMicrosoftLogin\(\)/);
  assert.match(loginSource, /AuthenticationOverlay/);
});
