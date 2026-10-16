function bindVarietyFilter() {
  const product = document.querySelector("[data-product]");
  const variety = document.querySelector("[data-variety]");
  const data = document.getElementById("variety-data");
  if (!product || !variety || !data) return;
  const items = JSON.parse(data.textContent || "[]");
  const allowAny = variety.hasAttribute("data-variety-any");
  let preferred = variety.dataset.selected || variety.value;
  const apply = () => {
    variety.innerHTML = "";
    if (allowAny) {
      const blank = document.createElement("option");
      blank.value = "";
      blank.textContent = "Любой";
      if (!preferred) blank.selected = true;
      variety.appendChild(blank);
    }
    items.filter((item) => String(item.product_id) === String(product.value)).forEach((item) => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.name;
      if (String(item.id) === String(preferred)) option.selected = true;
      variety.appendChild(option);
    });
    preferred = variety.value;
  };
  product.addEventListener("change", apply);
  apply();
}

function bindCaliberMode() {
  const radios = document.querySelectorAll("input[name=caliber_mode]");
  if (!radios.length) return;
  const exact = document.querySelector("[data-caliber-exact]");
  const range = document.querySelector("[data-caliber-range]");
  const apply = () => {
    const mode = document.querySelector("input[name=caliber_mode]:checked");
    const value = mode ? mode.value : "any";
    if (exact) exact.hidden = value !== "exact";
    if (range) range.hidden = value !== "range";
  };
  radios.forEach((radio) => radio.addEventListener("change", apply));
  apply();
}

function bindClientTerms() {
  const client = document.querySelector("[data-client]");
  const terms = document.querySelector("[data-terms]");
  if (!client || !terms) return;
  client.addEventListener("change", () => {
    const option = client.selectedOptions[0];
    if (option) terms.value = option.dataset.terms || "";
  });
}

function bindDigitMenu() {
  document.addEventListener("keydown", (event) => {
    if (event.repeat || event.ctrlKey || event.altKey || event.metaKey) return;
    if (!/^[0-9]$/.test(event.key)) return;
    const active = document.activeElement;
    if (active && (active.closest("input, textarea, select") || active.isContentEditable)) return;
    const opened = document.querySelector("details.menu-group[open] [data-digit='" + event.key + "']");
    const item = opened || document.querySelector("main [data-digit='" + event.key + "']");
    if (!item) return;
    event.preventDefault();
    item.click();
  });
}

function bindRowLinks() {
  document.querySelectorAll("tr[data-href]").forEach((row) => {
    row.addEventListener("dblclick", (event) => {
      if (event.target.closest("a, button, input, select, textarea, label")) return;
      window.location.href = row.dataset.href;
    });
  });
}

function bindBack() {
  const button = document.getElementById("go-back");
  if (!button) return;
  const key = "wms-trail";
  const here = location.pathname + location.search;
  const skipped = here === "/login" || here === "/tsd/login";
  let stack = [];
  try {
    const stored = JSON.parse(sessionStorage.getItem(key) || "[]");
    if (Array.isArray(stored)) stack = stored.filter((item) => item !== "/login" && item !== "/tsd/login");
  } catch (error) {
    stack = [];
  }
  if (!skipped) {
    const index = stack.lastIndexOf(here);
    if (index >= 0) stack = stack.slice(0, index + 1);
    else stack.push(here);
    sessionStorage.setItem(key, JSON.stringify(stack));
  }
  const place = stack.lastIndexOf(here);
  const target = place > 0 ? stack[place - 1] : "";
  if (!target) return;
  button.hidden = false;
  button.addEventListener("click", () => {
    const next = stack.slice(0, place);
    sessionStorage.setItem(key, JSON.stringify(next));
    location.href = target;
  });
}

function bindFilePick() {
  document.querySelectorAll("[data-import]").forEach((button) => {
    const form = button.closest("form");
    const input = form ? form.querySelector("[data-import-file]") : null;
    if (!input) return;
    button.addEventListener("click", () => {
      input.value = "";
      input.click();
    });
    input.addEventListener("change", () => {
      if (input.files && input.files[0]) form.requestSubmit();
    });
  });
}

function sheetDataRows(table) {
  return [...table.rows].filter((row) => row.querySelector("input.pick-row"));
}

function refreshSheetMarks(table) {
  let number = 0;
  let chosen = 0;
  let visible = 0;
  sheetDataRows(table).forEach((row) => {
    const box = row.querySelector("input.pick-row");
    const count = row.querySelector("td.count");
    if (row.hidden) {
      box.checked = false;
      box.disabled = true;
      if (count) count.textContent = "";
      return;
    }
    box.disabled = false;
    visible += 1;
    number += 1;
    if (count) count.textContent = String(number);
    if (box.checked) chosen += 1;
  });
  const all = table.querySelector("input.pick-all");
  if (all) {
    all.checked = visible > 0 && chosen === visible;
    all.indeterminate = chosen > 0 && chosen < visible;
  }
  const target = table.dataset.bulk;
  if (!target) return;
  const label = document.querySelector('[data-bulk-count="' + target + '"]');
  if (label) label.textContent = "Выбрано: " + chosen;
}

function bindSheetMarks() {
  document.querySelectorAll("table.sheet").forEach((table) => {
    if (table.closest("form") || table.hasAttribute("data-zone-map") || table.hasAttribute("data-setup") || table.querySelector("th.pick")) return;
    const header = [...table.rows].find((row) => row.querySelector("th"));
    if (!header) return;
    const pickHead = document.createElement("th");
    pickHead.className = "pick";
    const all = document.createElement("input");
    all.type = "checkbox";
    all.className = "pick-all";
    all.setAttribute("aria-label", "Выбрать все");
    pickHead.appendChild(all);
    const countHead = document.createElement("th");
    countHead.className = "count";
    countHead.textContent = "№";
    header.insertBefore(countHead, header.firstChild);
    header.insertBefore(pickHead, header.firstChild);
    [...table.rows].forEach((row) => {
      if (row === header) return;
      const first = row.cells[0];
      if (row.classList.contains("subtotal") || row.classList.contains("group")) {
        if (first && first.colSpan > 1 && row.cells.length === 1) {
          first.colSpan += 2;
          return;
        }
        const countGap = document.createElement("td");
        countGap.className = "count";
        const pickGap = document.createElement("td");
        pickGap.className = "pick";
        row.insertBefore(countGap, row.firstChild);
        row.insertBefore(pickGap, row.firstChild);
        return;
      }
      if (first && first.colSpan > 1 && row.cells.length === 1) {
        first.colSpan += 2;
        return;
      }
      const countCell = document.createElement("td");
      countCell.className = "count";
      const pickCell = document.createElement("td");
      pickCell.className = "pick";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.className = "pick-row";
      box.setAttribute("aria-label", "Выбрать строку");
      if (row.dataset.id && table.dataset.bulk) {
        box.name = "id";
        box.value = row.dataset.id;
        box.setAttribute("form", table.dataset.bulk);
      }
      pickCell.appendChild(box);
      row.insertBefore(countCell, row.firstChild);
      row.insertBefore(pickCell, row.firstChild);
    });
    table.addEventListener("change", (event) => {
      if (event.target.classList.contains("pick-all")) {
        const on = event.target.checked;
        sheetDataRows(table).forEach((row) => {
          if (row.hidden) return;
          row.querySelector("input.pick-row").checked = on;
        });
      }
      if (!event.target.classList.contains("pick-row") && !event.target.classList.contains("pick-all")) return;
      refreshSheetMarks(table);
    });
    refreshSheetMarks(table);
  });
}

function bindExcelFilters() {
  if (document.body.classList.contains("tsd")) return;
  const menu = document.createElement("div");
  menu.className = "xl-menu";
  menu.hidden = true;
  document.body.appendChild(menu);
  const tables = new WeakMap();
  const arrow = '<svg viewBox="0 0 12 12" aria-hidden="true"><path fill="currentColor" d="M2 4.2h8L6 9z"/></svg>';
  const funnel = '<svg viewBox="0 0 12 12" aria-hidden="true"><path fill="currentColor" d="M1 1.5h10L7.2 6.1V10L4.8 8.7V6.1z"/></svg>';

  function stateOf(table) {
    let state = tables.get(table);
    if (!state) {
      state = { filters: new Map(), sort: null };
      tables.set(table, state);
    }
    return state;
  }

  function headerRow(table) {
    return [...table.rows].find((row) => row.querySelector("th")) || null;
  }

  function columnCount(table) {
    const header = headerRow(table);
    return header ? header.cells.length : 0;
  }

  function isData(row, count) {
    if (row.querySelector("th")) return false;
    if (row.classList.contains("group") || row.classList.contains("subtotal")) return false;
    const cells = [...row.cells];
    if (cells.length === 1 && cells[0].colSpan >= count) return false;
    return cells.length > 0;
  }

  function rowsOf(table) {
    const count = columnCount(table);
    return [...table.rows].filter((row) => isData(row, count));
  }

  function textOf(cell) {
    if (!cell) return "";
    const fields = [...cell.querySelectorAll("input, select, textarea")].filter((field) => field.type !== "hidden");
    if (fields.length) {
      return fields.map((field) => {
        if (field.tagName === "SELECT") {
          const option = field.selectedOptions[0];
          return option ? option.textContent.trim() : "";
        }
        return field.value.trim();
      }).filter(Boolean).join(" ");
    }
    return cell.innerText.replace(/\s+/g, " ").trim();
  }

  function valueAt(row, index) {
    let cursor = 0;
    for (const cell of row.cells) {
      const span = cell.colSpan || 1;
      if (index >= cursor && index < cursor + span) return textOf(cell);
      cursor += span;
    }
    return "";
  }

  function asNumber(text) {
    const clean = text.replace(/\s/g, "").replace(",", ".");
    if (!/^-?\d+(\.\d+)?$/.test(clean)) return null;
    return Number(clean);
  }

  function asDate(text) {
    const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(text);
    if (!match) return null;
    return Date.UTC(Number(match[3]), Number(match[2]) - 1, Number(match[1]));
  }

  function columnKind(table, index) {
    const values = rowsOf(table).map((row) => valueAt(row, index)).filter((value) => value !== "");
    if (values.length && values.every((value) => asNumber(value) !== null)) return "number";
    if (values.length && values.every((value) => asDate(value) !== null)) return "date";
    return "text";
  }

  function compare(left, right, kind) {
    if (left === "" || right === "") {
      if (left === right) return 0;
      return left === "" ? 1 : -1;
    }
    if (kind === "number") return asNumber(left) - asNumber(right);
    if (kind === "date") return asDate(left) - asDate(right);
    return left.localeCompare(right, "ru", { numeric: true, sensitivity: "base" });
  }

  function sortRows(table) {
    const sort = stateOf(table).sort;
    if (!sort) return;
    const count = columnCount(table);
    const all = [...table.rows];
    const data = all.filter((row) => isData(row, count));
    const sorted = [...data].sort((left, right) => {
      const base = compare(valueAt(left, sort.index), valueAt(right, sort.index), sort.kind);
      if (valueAt(left, sort.index) === "" || valueAt(right, sort.index) === "") return base;
      return sort.dir === "asc" ? base : -base;
    });
    const parent = headerRow(table).parentNode;
    let cursor = 0;
    all.map((row) => (isData(row, count) ? sorted[cursor++] : row)).forEach((row) => parent.appendChild(row));
  }

  function syncStructure(table) {
    const count = columnCount(table);
    const rows = [...table.rows].filter((row) => !row.querySelector("th"));
    rows.forEach((row, index) => {
      if (row.classList.contains("group")) {
        let any = false;
        for (let next = index + 1; next < rows.length; next++) {
          if (rows[next].classList.contains("group") || rows[next].classList.contains("subtotal")) break;
          if (isData(rows[next], count) && !rows[next].hidden) any = true;
        }
        row.hidden = !any;
      } else if (row.classList.contains("subtotal")) {
        let sawData = false;
        let any = false;
        for (let prev = index - 1; prev >= 0; prev--) {
          if (rows[prev].classList.contains("group") || rows[prev].classList.contains("subtotal")) break;
          if (isData(rows[prev], count)) {
            sawData = true;
            if (!rows[prev].hidden) any = true;
          }
        }
        if (!sawData) any = rowsOf(table).some((item) => !item.hidden);
        row.hidden = !any;
      }
    });
  }

  function paint(table) {
    const state = stateOf(table);
    const header = headerRow(table);
    if (!header) return;
    [...header.cells].forEach((cell) => {
      const button = cell.querySelector(".xl-filter");
      if (!button) return;
      const filtered = state.filters.has(cell.cellIndex);
      button.classList.toggle("on", filtered);
      button.innerHTML = filtered ? funnel : arrow;
    });
  }

  function apply(table) {
    sortRows(table);
    const state = stateOf(table);
    rowsOf(table).forEach((row) => {
      let visible = true;
      state.filters.forEach((allowed, index) => {
        if (!allowed.has(valueAt(row, index))) visible = false;
      });
      row.hidden = !visible;
    });
    syncStructure(table);
    paint(table);
    if (table.querySelector("input.pick-row")) refreshSheetMarks(table);
  }

  function closeMenu() {
    menu.hidden = true;
  }

  function menuItem(text, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "xl-item";
    button.textContent = text;
    button.addEventListener("click", onClick);
    return button;
  }

  function openMenu(table, cell) {
    const state = stateOf(table);
    const index = cell.cellIndex;
    const kind = columnKind(table, index);
    const seen = new Set();
    const values = [];
    rowsOf(table).forEach((row) => {
      const value = valueAt(row, index);
      if (seen.has(value)) return;
      seen.add(value);
      values.push(value);
    });
    values.sort((left, right) => {
      if (left === "") return -1;
      if (right === "") return 1;
      return compare(left, right, kind);
    });
    const selected = state.filters.get(index);
    const asc = kind === "number" ? "Сортировка от минимального к максимальному" : kind === "date" ? "Сортировка от старых к новым" : "Сортировка от А до Я";
    const desc = kind === "number" ? "Сортировка от максимального к минимальному" : kind === "date" ? "Сортировка от новых к старым" : "Сортировка от Я до А";
    menu.replaceChildren();
    menu.append(
      menuItem(asc, () => {
        state.sort = { index: index, dir: "asc", kind: kind };
        apply(table);
        closeMenu();
      }),
      menuItem(desc, () => {
        state.sort = { index: index, dir: "desc", kind: kind };
        apply(table);
        closeMenu();
      }),
      document.createElement("hr"),
    );
    const clear = menuItem("Снять фильтр с «" + cell.dataset.xlTitle + "»", () => {
      state.filters.delete(index);
      apply(table);
      closeMenu();
    });
    clear.disabled = !selected;
    menu.append(clear, document.createElement("hr"));
    const search = document.createElement("input");
    search.className = "xl-search";
    search.type = "search";
    search.placeholder = "Поиск";
    const allLabel = document.createElement("label");
    allLabel.className = "xl-all";
    const allBox = document.createElement("input");
    allBox.type = "checkbox";
    allLabel.append(allBox, document.createTextNode("(Выделить все)"));
    const list = document.createElement("div");
    list.className = "xl-values";
    const boxes = values.map((value) => {
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = !selected || selected.has(value);
      label.append(box, document.createTextNode(value === "" ? "(Пустые)" : value));
      list.appendChild(label);
      return { box: box, label: label, value: value };
    });
    const actions = document.createElement("div");
    actions.className = "xl-actions";
    const ok = document.createElement("button");
    ok.type = "button";
    ok.textContent = "OK";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "xl-cancel";
    cancel.textContent = "Отмена";
    actions.append(ok, cancel);
    menu.append(search, allLabel, list, actions);

    function visibleBoxes() {
      return boxes.filter((item) => !item.label.hidden);
    }
    function syncAll() {
      const shown = visibleBoxes();
      const checked = shown.filter((item) => item.box.checked).length;
      allBox.checked = shown.length > 0 && checked === shown.length;
      allBox.indeterminate = checked > 0 && checked < shown.length;
    }
    syncAll();
    allBox.addEventListener("change", () => {
      visibleBoxes().forEach((item) => {
        item.box.checked = allBox.checked;
      });
      allBox.indeterminate = false;
    });
    boxes.forEach((item) => item.box.addEventListener("change", syncAll));
    search.addEventListener("input", () => {
      const query = search.value.trim().toLowerCase();
      boxes.forEach((item) => {
        const text = item.value === "" ? "(пустые)" : item.value.toLowerCase();
        item.label.hidden = query !== "" && !text.includes(query);
      });
      syncAll();
    });
    function commit() {
      const query = search.value.trim();
      const pool = query ? visibleBoxes() : boxes;
      const chosen = pool.filter((item) => item.box.checked).map((item) => item.value);
      if (chosen.length === boxes.length) state.filters.delete(index);
      else state.filters.set(index, new Set(chosen));
      apply(table);
      closeMenu();
    }
    ok.addEventListener("click", commit);
    search.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      commit();
    });
    cancel.addEventListener("click", closeMenu);
    menu.hidden = false;
    const anchor = cell.querySelector(".xl-filter").getBoundingClientRect();
    const width = menu.offsetWidth;
    const height = menu.offsetHeight;
    let left = Math.min(Math.max(8, anchor.right - width), window.innerWidth - width - 8);
    let top = anchor.bottom + 2;
    if (top + height > window.innerHeight - 8) top = Math.max(8, anchor.top - height - 2);
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    search.focus();
  }

  document.querySelectorAll("table").forEach((table) => {
    if (table.hasAttribute("data-setup")) return;
    const header = headerRow(table);
    if (!header) return;
    [...header.cells].forEach((cell) => {
      if (cell.tagName !== "TH") return;
      if (cell.classList.contains("pick") || cell.classList.contains("count")) return;
      const title = cell.textContent.replace(/\s+/g, " ").trim();
      if (!title || cell.querySelector(".xl-filter")) return;
      cell.dataset.xlTitle = title;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "xl-filter";
      button.setAttribute("aria-label", "Фильтр: " + title);
      button.innerHTML = arrow;
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        openMenu(table, cell);
      });
      cell.appendChild(button);
    });
  });

  document.addEventListener("mousedown", (event) => {
    if (menu.hidden) return;
    if (menu.contains(event.target) || event.target.closest(".xl-filter")) return;
    closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });
}

function bindNotices() {
  const box = document.querySelector(".notices");
  if (!box) return;
  let sent = false;
  const mark = () => {
    if (sent) return;
    sent = true;
    const token = box.querySelector("[name=csrfmiddlewaretoken]");
    if (!token) return;
    fetch("/notices/seen", {
      method: "POST",
      headers: { "X-CSRFToken": token.value },
      credentials: "same-origin",
    });
    const badge = box.querySelector(".notice-badge");
    if (badge) badge.remove();
    box.querySelectorAll(".unread").forEach((item) => item.classList.remove("unread"));
  };
  box.addEventListener("mouseenter", mark);
  box.addEventListener("focusin", mark);
}

document.addEventListener("DOMContentLoaded", () => {
  bindNotices();
  bindVarietyFilter();
  bindCaliberMode();
  bindClientTerms();
  bindDigitMenu();
  bindRowLinks();
  bindBack();
  bindFilePick();
  bindSheetMarks();
  bindExcelFilters();
});
