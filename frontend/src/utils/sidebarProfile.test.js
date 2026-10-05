import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { profileEmail, profileInitial, profileName } from "./sidebarProfile.js";

const here = dirname(fileURLToPath(import.meta.url));
const sidebar = readFileSync(resolve(here, "../components/Sidebar.jsx"), "utf8");
const profile = readFileSync(resolve(here, "../components/SidebarProfile.jsx"), "utf8");
const styles = readFileSync(resolve(here, "../styles/global.css"), "utf8");

test("profile helpers use the signed-in Microsoft name and email", () => {
  assert.equal(profileInitial({ name: "ada lovelace" }), "A");
  assert.equal(profileInitial({ name: "" }), "");
  assert.equal(profileName({ name: "Ada Lovelace" }), "Ada Lovelace");
  assert.equal(profileEmail({ email: "ada@example.com" }), "ada@example.com");
  assert.equal(profileEmail({ email: "  " }), "");
});

test("shared sidebar hosts a clickable profile card and account popover", () => {
  assert.match(sidebar, /<SidebarProfile user=\{user\} onLogout=\{handleLogout\} \/>/);
  assert.match(sidebar, /resetSession\(\)/);
  assert.match(sidebar, /logout\(\)/);
  assert.doesNotMatch(sidebar, /sidebar-user-name/);
  assert.match(profile, /aria-haspopup="dialog"/);
  assert.match(profile, /aria-expanded=\{open\}/);
  assert.match(profile, /createPortal/);
  assert.match(profile, /mousedown/);
  assert.match(profile, /Escape/);
  assert.match(profile, /triggerRef\.current\?\.focus/);
  assert.match(profile, /<LogOut/);
  assert.match(profile, /Logout/);
  assert.doesNotMatch(profile, /graph\.microsoft\.com/);
  assert.doesNotMatch(profile, /type="email"/);
  assert.match(styles, /width: 40px/);
  assert.match(styles, /#4caf50/);
  assert.match(styles, /\.nav-list[\s\S]*overflow-y: auto/);
  assert.match(styles, /sidebar-account-logout/);
  assert.match(styles, /#f44336/);
});
