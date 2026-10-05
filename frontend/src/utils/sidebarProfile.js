export function profileInitial(user) {
  const name = String(user?.name || "").trim();
  if (!name) {
    return "";
  }
  const letter = name[0];
  return /[A-Za-z0-9]/.test(letter) ? letter.toLocaleUpperCase() : letter;
}

export function profileName(user) {
  return String(user?.name || "").trim();
}

export function profileEmail(user) {
  return String(user?.email || "").trim();
}
