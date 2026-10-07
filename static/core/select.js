"use strict";

// Retain native values, form submission and change handlers; replace only the UI.
const selectMenus = new WeakMap();
let selectMenuSerial = 0, activeSelectMenu = null;

function enhanceSelect(select) {
  if (selectMenus.has(select) || select.hidden || select.multiple) return;
  const id = select.id || `app-select-${++selectMenuSerial}`;
  const wrapper = el("span", { class: "app-select" });
  const text = el("span", { class: "app-select-value" });
  const trigger = el("button", { type: "button", id: `${id}-trigger`, class: `filter-trigger app-select-trigger ${select.className}`,
    role: "combobox", "aria-haspopup": "listbox", "aria-expanded": "false", "aria-controls": `${id}-menu`, popovertarget: `${id}-menu` }, text, el("span", { class: "filter-chevron", "aria-hidden": "true" }));
  const menu = el("div", { id: `${id}-menu`, class: "filter-menu app-select-menu", popover: "auto", role: "listbox" });
  let signature = "", choices = [], listeners = null, search = "", searchTime = 0;
  const label = select.getAttribute("aria-label") || [...(select.closest("label")?.childNodes || [])].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent.trim()).join(" ") || "选择选项";
  select.before(wrapper); wrapper.append(select, trigger, menu);
  select.hidden = true; select.classList.add("app-select-source");
  trigger.setAttribute("aria-label", label); menu.setAttribute("aria-label", label);

  function close(restoreFocus = false) {
    if (menu.matches(":popover-open")) menu.hidePopover();
    if (restoreFocus && trigger.isConnected) trigger.focus({ preventScroll: true });
  }
  function position() {
    if (!menu.matches(":popover-open")) return;
    const rect = trigger.getBoundingClientRect(), modal = trigger.closest("dialog"), bounds = modal?.getBoundingClientRect();
    if (!trigger.isConnected || !trigger.getClientRects().length || modal && !modal.open
      || rect.bottom <= Math.max(0, bounds?.top || 0) || rect.top >= Math.min(innerHeight, bounds?.bottom || innerHeight)) { close(); return; }
    const margin = 12, gap = 8, below = innerHeight - rect.bottom - gap - margin, above = rect.top - gap - margin;
    menu.style.width = `${Math.min(Math.max(rect.width, 9 * parseFloat(getComputedStyle(document.documentElement).fontSize)), innerWidth - margin * 2)}px`;
    const openAbove = below < Math.min(menu.scrollHeight, 240) && above > below;
    menu.style.maxHeight = `${Math.max(64, Math.min(360, openAbove ? above : below))}px`;
    menu.style.left = `${Math.max(margin, Math.min(rect.left, innerWidth - menu.offsetWidth - margin))}px`;
    menu.style.top = `${openAbove ? Math.max(margin, rect.top - gap - menu.offsetHeight) : Math.min(rect.bottom + gap, innerHeight - menu.offsetHeight - margin)}px`;
  }
  function enabled() { return choices.filter(choice => !choice.disabled); }
  function focusChoice(choice) {
    if (!choice) return;
    choice.focus({ preventScroll: true });
    if (choice.offsetTop < menu.scrollTop || choice.offsetTop + choice.offsetHeight > menu.scrollTop + menu.clientHeight) menu.scrollTop = choice.offsetTop - (menu.clientHeight - choice.offsetHeight) / 2;
  }
  function sync() {
    if (select.disabled) close();
    const options = [...select.options].filter(option => !option.hidden);
    const next = JSON.stringify([select.value, select.disabled, options.map(option => [option.value, option.text, option.disabled, option.parentElement?.disabled, option.parentElement?.label])]);
    if (signature === next) { position(); return; }
    signature = next;
    const focused = menu.contains(document.activeElement) ? document.activeElement.dataset.value : null;
    text.textContent = select.selectedOptions[0]?.text || "—";
    trigger.disabled = select.disabled;
    const content = []; let previousGroup = null;
    choices = options.map((option, index) => {
      const group = option.parentElement?.tagName === "OPTGROUP" ? option.parentElement : null;
      if (group && group !== previousGroup) content.push(el("div", { class: "app-select-group" }, group.label));
      previousGroup = group;
      const selected = option.selected;
      const choice = el("button", { type: "button", role: "option", tabindex: "-1", class: "filter-menu-option", disabled: option.disabled || group?.disabled,
        "aria-selected": String(selected), "aria-current": String(selected), dataset: { value: option.value }, style: { "--option-order": Math.min(index, 8) }, onclick: event => {
          event.preventDefault(); event.stopPropagation();
          const changed = select.value !== option.value;
          select.value = option.value; close(true); sync();
          if (changed) { select.dispatchEvent(new Event("input", { bubbles: true })); select.dispatchEvent(new Event("change", { bubbles: true })); }
        } }, el("span", {}, option.text), selected ? icon("check") : null);
      content.push(choice); return choice;
    });
    menu.replaceChildren(el("div", { class: "filter-menu-options" }, content));
    position();
    if (menu.matches(":popover-open") && focused !== null) focusChoice(enabled().find(choice => choice.dataset.value === focused) || enabled()[0]);
  }
  function open(edge = "selected") {
    if (select.disabled) return;
    sync(); if (!enabled().length) return;
    if (!menu.matches(":popover-open")) menu.showPopover();
    position();
    if (menu.matches(":popover-open")) focusChoice(edge === "first" ? enabled()[0] : edge === "last" ? enabled().at(-1) : enabled().find(choice => choice.dataset.value === select.value) || enabled()[0]);
  }
  const controller = { sync, close, position, trigger, menu };
  selectMenus.set(select, controller);
  trigger.addEventListener("click", event => { event.preventDefault(); menu.matches(":popover-open") ? close(true) : open(); });
  trigger.addEventListener("keydown", event => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault(); open(event.key === "Home" ? "first" : event.key === "End" ? "last" : "selected");
  });
  menu.addEventListener("keydown", event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); }
    else if (event.key === "Tab") close(true);
    else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault(); const available = enabled(), index = available.indexOf(document.activeElement);
      focusChoice(event.key === "Home" ? available[0] : event.key === "End" ? available.at(-1) : available[(index + (event.key === "ArrowDown" ? 1 : -1) + available.length) % available.length]);
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && event.key !== " ") {
      event.preventDefault(); const now = Date.now(); search = now - searchTime > 700 ? event.key : search + event.key; searchTime = now;
      const available = enabled(), index = available.indexOf(document.activeElement), ordered = [...available.slice(index + 1), ...available.slice(0, index + 1)];
      focusChoice(ordered.find(choice => choice.textContent.trim().toLocaleLowerCase().startsWith(search.toLocaleLowerCase())));
    }
  });
  menu.addEventListener("beforetoggle", event => {
    const opened = event.newState === "open";
    trigger.setAttribute("aria-expanded", String(opened));
    listeners?.abort(); listeners = null;
    if (opened) {
      activeSelectMenu?.close(); activeSelectMenu = controller;
      listeners = new AbortController();
      window.addEventListener("resize", position, { signal: listeners.signal });
      document.addEventListener("scroll", event => { if (!menu.contains(event.target)) position(); }, { capture: true, signal: listeners.signal });
      trigger.closest("dialog")?.addEventListener("close", () => close(), { signal: listeners.signal });
    } else if (activeSelectMenu === controller) activeSelectMenu = null;
  });
  select.addEventListener("change", sync); select.addEventListener("input", sync);
  sync();
}

function syncSelectMenus() {
  document.querySelectorAll("select").forEach(select => {
    if (!selectMenus.has(select)) enhanceSelect(select);
    selectMenus.get(select)?.sync();
  });
  if (activeSelectMenu && !activeSelectMenu.trigger.isConnected) activeSelectMenu.close();
}

function installSelectMenus() {
  syncSelectMenus();
  new MutationObserver(syncSelectMenus).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true,
    attributeFilter: ["disabled", "value", "selected", "label", "hidden", "open"] });
}
