function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    // Keep accessible names without hover text.
    else if (key === "title") continue;
    else if (key === "text") node.textContent = String(value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "style") { const styles = { ...value }; if (/^\d+(?:\.\d+)?px$/.test(styles.fontSize || "")) styles.fontSize = `calc(var(--ui-font-size) * ${Math.max(.875, Number.parseFloat(styles.fontSize) / 14)})`; for (const [name, item] of Object.entries(styles)) { if (name.startsWith("--")) node.style.setProperty(name, String(item)); else node.style[name] = item; } }
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "value") node.value = value;
    else if (key === "checked" || key === "disabled" || key === "selected") node[key] = Boolean(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon"); svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use"); use.setAttribute("href", `#i-${name}`); svg.append(use); return svg;
}
function button(label, action, kind = "secondary", iconName = "", attrs = {}) {
  return el("button", { type: "button", class: `button ${kind}`, onclick: action, ...attrs }, iconName ? icon(iconName) : null, label);
}
function iconButton(name, label, action) { return el("button", { type: "button", class: "icon-button", title: label, "aria-label": label, onclick: action }, icon(name)); }
function field(label, input) { return el("label", { class: "field" }, label, input); }
function asText(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(asText).filter(Boolean).join("；");
  if (typeof value === "object") return value.name || value.text || value.description || value.summary || Object.entries(value).map(([k, v]) => `${k}：${asText(v)}`).join("\n");
  return "";
}
function list(value) { return Array.isArray(value) ? value : (typeof value === "string" ? value.split(/[,，;；\n]/).map(x => x.trim()).filter(Boolean) : []); }
